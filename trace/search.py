"""混合检索层（DigUp 融合内核移植，PRD FR-4 升级版）。

1. 语义通道：全量 cosine → 按 modality 分组去均值 → 全体 z-score（每查询自适应尺度），
   z ≥ zGate(2.0) 才算语义命中（"没找到"由此可判断）
2. 关键词通道：查询词逐词 FTS5 OR（trigram 子串），bm25(name 列权重 3.0)，
   coverage = 查询词在文件中的命中比例，≥0.5 入围
3. 融合分：meaning_w·max(z,0) + words_w·coverage^1.5 + kindHint(+1)；
   lookup 查询（纯编码/数字，如 "INV-2041"）语义降权 0.5、关键词加倍
4. strongCount：score ≥ top·0.5 为强匹配，其余折叠展示

2026-10-16 工程化改造：
- P0：空候选集短路返回（原先生成 `WHERE file_id IN ()` 非法 SQL，空库必 500）
- 错词容错：FTS+LIKE 双零命中时，用查询词截断变体（去尾/去头）降级召回，
  coverage 打 0.75 折——英文拼写错误（embeding→embedding）与中文切分歧义可救回
- 分页：offset 参数；结果组装消除 N+1（slices_by_ids / files_by_ids 批量取）
- 语义通道走 store.all_vectors_matrix() 矩阵缓存（L2 归一化一次完成）
"""

from __future__ import annotations

import re
import time

import numpy as np

from . import config
from .readers import _parse_ts
from .searchtext import (
    expand_concepts, fts_patterns, fuzzy_variants, has_words, is_lookup,
    is_watermark_only, kind_hints, normalized, terms,
)

Z_GATE = 2.0            # 语义命中门限（DigUp 默认）
WORD_WEIGHT = 2.0       # 关键词基础权重（DigUp：代码索引 1.0）
MIN_GROUP = 20          # modality 分组少于这么多样本时借用全体均值
STD_FLOOR = 0.02        # z-score 标准差下限，防退化
STRONG_RATIO = 0.5      # strong 折叠阈值
KW_LIMIT = 400          # 每 pattern FTS 命中上限
FUZZY_COVERAGE = 0.75   # 模糊命中（截断变体）的 coverage 折扣


def _meaning(cos: np.ndarray, modalities: list[str], std_floor: float = STD_FLOOR) -> np.ndarray:
    """DigUp `Searcher.meaning`：按 modality 去均值后全体 z-score。

    文本向量天然比图像/音频向量余弦高（实测 0.75-0.86 vs 0.67-0.78），
    不分组归一化会让文档永远压过截图。
    """
    n = len(cos)
    if n == 0:
        return cos
    all_mean = float(cos.mean())
    group_mean: dict[str, float] = {}
    for m in set(modalities):
        idx = [i for i in range(n) if modalities[i] == m]
        if len(idx) >= MIN_GROUP:
            group_mean[m] = float(cos[idx].mean())
        else:
            group_mean[m] = all_mean
    centered = np.array([cos[i] - group_mean[modalities[i]] for i in range(n)], dtype=np.float64)
    mean = float(centered.mean())
    std = float(((centered - mean) ** 2).mean() ** 0.5)
    return (centered - mean) / max(std, std_floor)


def _highlight(snippet: str, ts: list[str], width: int = 160) -> str:
    """轻量高亮：把查询词出现位置包上 «»（DigUp snippet 风格），截断到 width。"""
    if not snippet:
        return ""
    flat = re.sub(r"\s+", " ", snippet).strip()
    low = flat.lower()
    marks: list[tuple[int, int]] = []
    for t in ts:
        nt = normalized(t).lower()
        if len(nt) < 2:
            continue
        start = 0
        while True:
            pos = low.find(nt, start)
            if pos < 0:
                break
            marks.append((pos, pos + len(nt)))
            start = pos + max(len(nt), 1)
    if not marks:
        return flat[:width]
    marks.sort()
    first = marks[0][0]
    lo = max(0, first - width // 3)
    hi = min(len(flat), lo + width)
    out = []
    cursor = lo
    for a, b in marks:
        if a < cursor or b > hi:
            continue
        out.append(flat[cursor:a] + "«" + flat[a:b] + "»")
        cursor = b
    out.append(flat[cursor:hi])
    text = ("…" if lo > 0 else "") + "".join(out) + ("…" if hi < len(flat) else "")
    return text.strip()


def _keyword_channel(store, ts: list[str], ts_fts: list[str]) -> tuple:
    """关键词通道：长词 FTS + 短词 LIKE；双零命中时降级模糊召回。

    Returns:
        (kw_file_slice, coverage, fuzzy)：kw_file_slice={fid:(bm25,sid)}，
        coverage={fid:0..1}，fuzzy=是否走了模糊降级。
    """
    longs = [t for t in ts_fts if len(normalized(t)) >= 3]
    shorts = [t for t in ts_fts if len(normalized(t)) < 3]
    patterns = fts_patterns(longs)
    per_pattern = store.fts_per_pattern(patterns) if patterns else []
    short_hits = store.fts_short_terms(shorts) if shorts else {}

    # 错词容错：精确词全部零命中 → 截断变体降级召回（embeding→embedin*，
    # 中文切分歧义的边界字错误也能靠子串救回部分命中）
    fuzzy = False
    if not any(per_pattern) and not short_hits:
        variants = []
        for t in ts_fts:
            variants.extend(fuzzy_variants(t))
        if variants:
            v_longs = [v for v in variants if len(v) >= 3]
            v_shorts = [v for v in variants if len(v) < 3]
            per_pattern = store.fts_per_pattern(fts_patterns(v_longs)) if v_longs else []
            short_hits = store.fts_short_terms(v_shorts) if v_shorts else {}
            fuzzy = bool(per_pattern and any(per_pattern)) or bool(short_hits)

    kw_file_slice: dict[int, tuple[float, int]] = {}
    coverage: dict[int, float] = {}
    total_words = len(ts) or 1  # coverage 用原始词数（不要被扩展词放大）
    if per_pattern or short_hits:
        all_sids: set[int] = set()
        for h in per_pattern:
            all_sids.update(h)
        all_sids.update(short_hits)
        sid_text = store.slice_text_by_ids(list(all_sids))
        file_hits: dict[int, int] = {}
        for h in per_pattern:
            for sid in h:
                info = sid_text.get(sid)
                if info is None:
                    continue
                fid, text = info
                if is_watermark_only(text):
                    continue
                bm = h[sid]
                file_hits[fid] = file_hits.get(fid, 0) + 1
                cur = kw_file_slice.get(fid)
                if cur is None or bm > cur[0]:
                    kw_file_slice[fid] = (bm, sid)
        for sid, n in short_hits.items():
            info = sid_text.get(sid)
            if info is None:
                continue
            fid, text = info
            if is_watermark_only(text):
                continue
            file_hits[fid] = file_hits.get(fid, 0) + n
            if fid not in kw_file_slice:
                kw_file_slice[fid] = (0.0, sid)
        discount = FUZZY_COVERAGE if fuzzy else 1.0
        for fid, n in file_hits.items():
            coverage[fid] = min(n / total_words, 1.0) * discount
    return kw_file_slice, coverage, fuzzy


def search(store, embedder, query: str, limit: int = 10, offset: int = 0,
           file_type: str | None = None, dir_filter: str | None = None,
           z_gate: float | None = None) -> list[dict]:
    """混合语义+关键词检索，返回按融合分排序的文件级结果。

    Args:
        z_gate: 语义命中门限（默认 2.0）。前端滑杆 0-10 传过来。
        offset: 分页偏移（与 limit 配合；排序在全量候选上完成后切片）。
    """
    z_gate = z_gate if z_gate is not None else Z_GATE
    qv = np.asarray(embedder.embed_query(query), dtype=np.float32)
    qv /= max(np.linalg.norm(qv), 1e-9)

    # ---- 1. 语义通道：全量 cosine + z-score（矩阵缓存）----
    sids, fids, mods, matrix = store.all_vectors_matrix()
    best_sem: dict[int, tuple[float, float, int]] = {}   # file_id -> (z, cosine, slice_id)
    if len(sids):
        cos = matrix @ qv
        z = _meaning(cos, mods)
        # 每文件取最佳切片（向量化：np.unique + argmax 思路，循环仅用于并列少的情况）
        for i in range(len(sids)):
            fid = int(fids[i])
            cur = best_sem.get(fid)
            if cur is None or z[i] > cur[0]:
                best_sem[fid] = (float(z[i]), float(cos[i]), int(sids[i]))

    # ---- 2. 关键词通道（含模糊降级）----
    ts = terms(query)
    expanded = expand_concepts(ts)
    ts_fts = ts + expanded
    kw_file_slice, coverage, fuzzy = _keyword_channel(store, ts, ts_fts)

    # ---- 3. 融合 ----
    lookup = is_lookup(ts)
    meaning_w = 0.5 if lookup else 1.0
    words_w = WORD_WEIGHT * (2 if lookup else 1)
    hints = kind_hints(query)

    candidates = {fid for fid, (z, _c, _s) in best_sem.items() if z >= z_gate}
    candidates |= {fid for fid, cov in coverage.items() if cov >= 0.5}
    # 兜底：单模态 / 长视频场景下 z 全在 ±1，z-gate 永远卡掉所有结果
    # 此时按 raw cosine 取 top-3，让用户至少能看到"语义最像"的视频帧
    fallback = False
    if not candidates and best_sem:
        fallback = True
        top = sorted(best_sem.items(), key=lambda kv: kv[1][0], reverse=True)[:3]
        candidates = {fid for fid, _ in top}

    # P0：空候选集短路（原先生成 IN () 非法 SQL）
    if not candidates:
        return []

    file_rows = store.files_by_ids(candidates)

    scored: list[tuple[float, dict]] = []
    for fid in candidates:
        fr = file_rows.get(fid)
        if fr is None:
            continue
        if file_type and fr["file_type"] != file_type:
            continue
        if dir_filter and dir_filter.lower() not in fr["path"].lower():
            continue
        z, cos_v, sem_sid = best_sem.get(fid, (0.0, 0.0, None))
        cov = coverage.get(fid, 0.0)
        score = meaning_w * max(z, 0.0) + words_w * (cov ** 1.5)
        if fr["file_type"] in hints:
            score += 1.0
        scored.append((score, {"file_id": fid, "row": fr, "z": z, "cos": cos_v,
                               "cov": cov, "sem_sid": sem_sid,
                               "kw": kw_file_slice.get(fid)}))

    scored.sort(key=lambda x: x[0], reverse=True)
    # 兜底场景下，score 普遍很低，UI 端应折叠展示
    if fallback:
        for _sc, item in scored:
            item["weak_fallback"] = True

    # 分页：排序完成后切片，offset 只影响组装阶段
    page = scored[offset:offset + limit]

    # ---- 4. 组装结果（批量取 slice / 帧信息，消除 N+1）----
    need_sids: set[int] = set()
    for _score, item in page:
        if item["sem_sid"] is not None:
            need_sids.add(item["sem_sid"])
        if item["kw"] is not None:
            need_sids.add(item["kw"][1])
    slice_rows = store.slices_by_ids(list(need_sids)) if need_sids else {}

    # 视频帧位置批量取（仅 video 命中）
    video_fids = [item["file_id"] for _s, item in page
                  if item["row"]["file_type"] == "video" and item.get("video_loc_ts")]
    frame_locs: dict[int, list[str]] = {}
    for vfid in video_fids:
        frame_locs[vfid] = store.frame_locations(vfid)

    out: list[dict] = []
    top_score = scored[0][0] if scored else 0.0
    for score, item in page:
        fr = item["row"]
        sem_sid = item["sem_sid"]
        kw = item["kw"]
        use_sem = sem_sid is not None and (item["z"] >= z_gate or kw is None)
        sid = sem_sid if use_sem else (kw[1] if kw else sem_sid)
        snippet = ""
        if sid is not None:
            r = slice_rows.get(sid)
            if r:
                snippet = _highlight(r["text_snippet"] or "", ts)
                if r["modality"] == "video_frame" and r["location"]:
                    item["video_loc_ts"] = r["location"]
        if not snippet and kw is not None:
            r = slice_rows.get(kw[1])
            if r:
                snippet = _highlight(r["text_snippet"] or "", ts)
                if r["modality"] == "video_frame" and r["location"]:
                    item["video_loc_ts"] = r["location"]
        # ---- 视频附近 5 帧：单帧不准时让用户能视觉扫到正确画面 ----
        nearby_frames: list[dict] = []
        if fr["file_type"] == "video" and item.get("video_loc_ts"):
            center = _parse_ts(item["video_loc_ts"])
            if center is not None:
                parsed = []
                for loc in frame_locs.get(fr["file_id"], []):
                    t = _parse_ts(loc)
                    if t is not None:
                        parsed.append((t, loc))
                parsed.sort()
                nearby = [(t, loc) for t, loc in parsed if abs(t - center) <= 12.0]
                if nearby:
                    if len(nearby) <= 5:
                        picks = nearby
                    else:
                        idx = min(range(len(nearby)), key=lambda i: abs(nearby[i][0] - center))
                        lo = max(0, idx - 2)
                        hi = min(len(nearby), lo + 5)
                        lo = max(0, hi - 5)
                        picks = nearby[lo:hi]
                    nearby_frames = [{"t": t, "loc": loc} for t, loc in picks]

        z_v = item["z"]
        if z_v >= 2.0:
            confidence = "high"
        elif z_v >= 1.0:
            confidence = "medium"
        else:
            confidence = "low"

        out.append({
            "score": round(score, 4),
            "path": fr["path"],
            "name": fr["name"],
            "file_type": fr["file_type"],
            "location": item.get("video_loc_ts", ""),
            "cos_sim": round(item["cos"], 4),
            "z": round(item["z"], 3),
            "coverage": round(item["cov"], 2),
            "keyword_rank": kw[1] if kw else None,
            "snippet": snippet,
            "mtime": fr["mtime"],
            "strong": score >= top_score * STRONG_RATIO if top_score > 0 else True,
            "weak_fallback": item.get("weak_fallback", False),
            "fuzzy_match": fuzzy,
            "nearby_frames": nearby_frames,
            "confidence": confidence,
        })
    return out


def search_with_timing(store, embedder, query: str, **kw) -> dict:
    """带耗时信息的检索（CLI 用）。"""
    t0 = time.time()
    results = search(store, embedder, query, **kw)
    return {"query": query, "ms": round((time.time() - t0) * 1000, 1), "results": results}
