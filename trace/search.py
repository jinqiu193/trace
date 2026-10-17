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
VISION_MODALITIES = ("image", "video_frame")  # 以图搜图比对的切片模态


def _sort_scored(scored: list, sort: str) -> None:
    """文件级排序：relevance（融合分）/ mtime_desc / mtime_asc / size_desc / name。

    排序在全量候选上完成后才分页，切换排序维度不需要重新查询。
    """
    if sort == "mtime_desc":
        scored.sort(key=lambda x: x[1]["row"]["mtime"] or 0, reverse=True)
    elif sort == "mtime_asc":
        scored.sort(key=lambda x: x[1]["row"]["mtime"] or 0)
    elif sort == "size_desc":
        scored.sort(key=lambda x: x[1]["row"]["size"] or 0, reverse=True)
    elif sort == "name":
        scored.sort(key=lambda x: (x[1]["row"]["name"] or "").lower())
    else:
        scored.sort(key=lambda x: x[0], reverse=True)


def _facets(file_rows: dict, candidates: set, dir_filter: str | None) -> dict[str, int]:
    """类型分布统计（file_type 过滤之前、dir 过滤之后）——类型筛选按钮的计数。"""
    out: dict[str, int] = {}
    for fid in candidates:
        fr = file_rows.get(fid)
        if fr is None:
            continue
        if dir_filter and dir_filter.lower() not in fr["path"].lower():
            continue
        out[fr["file_type"]] = out.get(fr["file_type"], 0) + 1
    return out


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
    kw_all: dict[int, list[tuple[float, int]]] = {}
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
            for sid, bm in h.items():
                info = sid_text.get(sid)
                if info is None:
                    continue
                fid, text = info
                if is_watermark_only(text):
                    continue
                file_hits[fid] = file_hits.get(fid, 0) + 1
                cur = kw_file_slice.get(fid)
                if cur is None or bm > cur[0]:
                    kw_file_slice[fid] = (bm, sid)
                kw_all.setdefault(fid, []).append((bm, sid))
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
            kw_all.setdefault(fid, []).append((0.0, sid))
        discount = FUZZY_COVERAGE if fuzzy else 1.0
        for fid, n in file_hits.items():
            coverage[fid] = min(n / total_words, 1.0) * discount
    return kw_file_slice, kw_all, coverage, fuzzy


# ---- 查询向量 LRU 缓存 ----
# 实测瓶颈：embed_query 的 ONNX 前向 ≈500ms，占检索总耗时 99%。
# 用户调筛选/翻页/切排序时同一查询词反复触发——缓存后这些场景直接 0ms。
_QUERY_VEC_CACHE: dict[str, np.ndarray] = {}
_QUERY_VEC_CACHE_MAX = 32


def _cached_query_vector(embedder, query: str) -> np.ndarray:
    qv = _QUERY_VEC_CACHE.get(query)
    if qv is not None:
        return qv
    qv = np.asarray(embedder.embed_query(query), dtype=np.float32)
    qv /= max(np.linalg.norm(qv), 1e-9)
    if len(_QUERY_VEC_CACHE) >= _QUERY_VEC_CACHE_MAX:
        _QUERY_VEC_CACHE.pop(next(iter(_QUERY_VEC_CACHE)))  # FIFO 淘汰
    _QUERY_VEC_CACHE[query] = qv
    return qv


_VIDEO_MODALITIES = ("video_frame", "video_sound")


def _fast_mode_keep(fids, mods) -> "np.ndarray":
    """简单模式行掩码：非视频行全保留；视频行每文件保留 1/4 与 3/4 两个代表位。

    位置而非随机：片头常是黑场/标题，1/4 与 3/4 比首尾更有代表性；
    切片按写入序 ≈ 时间序。
    """
    n = len(fids)
    keep = np.ones(n, dtype=bool)
    by_fid: dict[int, list[int]] = {}
    for i in range(n):
        if mods[i] in _VIDEO_MODALITIES:
            by_fid.setdefault(int(fids[i]), []).append(i)
    for _fid, idxs in by_fid.items():
        if len(idxs) <= 2:
            continue
        pick = {idxs[len(idxs) // 4], idxs[(3 * len(idxs)) // 4]}
        for i in idxs:
            if i not in pick:
                keep[i] = False
    return keep


def search(store, embedder, query: str, limit: int = 10, offset: int = 0,
           file_type: str | None = None, dir_filter: str | None = None,
           z_gate: float | None = None, sort: str = "relevance",
           mtime_after: float | None = None, mtime_before: float | None = None,
           min_size: int | None = None, max_size: int | None = None,
           mode: str = "detailed",
           with_facets: bool = False) -> list[dict] | tuple[list[dict], dict]:
    """混合语义+关键词检索，返回按融合分排序的文件级结果。

    Args:
        z_gate: 语义命中门限（默认 2.0）。前端滑杆 0-10 传过来。
        offset: 分页偏移（与 limit 配合；排序在全量候选上完成后切片）。
        sort: 文件级排序维度（relevance/mtime_desc/mtime_asc/size_desc/name）。
        mtime_after/before: 修改时间窗口（epoch 秒，含下界不含上界）。
        min_size/max_size: 文件大小窗口（字节）。
        mode: "detailed"（默认，全切片 + 命中详情）/ "fast"（视频每文件
            仅比对 2 个代表片段，跳过命中详情组装）。
        with_facets: True 时返回 (results, facets)——类型分布计数。
    """
    z_gate = z_gate if z_gate is not None else Z_GATE
    qv = _cached_query_vector(embedder, query)

    # ---- 1. 语义通道：全量 cosine + z-score（矩阵缓存）----
    sids, fids, mods, matrix = store.all_vectors_matrix()
    best_sem: dict[int, tuple[float, float, int]] = {}   # file_id -> (z, cosine, slice_id)
    # 视频文件的语义命中片段（file_id -> [(z, slice_id)]，槽位制保留 top-8）
    vid_sem: dict[int, list[tuple[float, int]]] = {}
    if len(sids):
        cos = matrix @ qv
        # z-score 恒用全量分布（fast 抽稀后样本太少，分布会失真）
        z = _meaning(cos, mods)
        # fast 模式：视频切片每文件只保留 1/4 与 3/4 两个代表位参与命中
        keep = _fast_mode_keep(fids, mods) if mode == "fast" else None
        # 每文件取最佳切片（向量化：np.unique + argmax 思路，循环仅用于并列少的情况）
        for i in range(len(sids)):
            if keep is not None and not keep[i]:
                continue
            fid = int(fids[i])
            cur = best_sem.get(fid)
            if cur is None or z[i] > cur[0]:
                best_sem[fid] = (float(z[i]), float(cos[i]), int(sids[i]))
            if mods[i] in ("video_frame", "video_sound") and z[i] >= z_gate:
                lst = vid_sem.setdefault(fid, [])
                if len(lst) < 8:
                    lst.append((float(z[i]), int(sids[i])))
                else:
                    mi = min(range(len(lst)), key=lambda k: lst[k][0])
                    if z[i] > lst[mi][0]:
                        lst[mi] = (float(z[i]), int(sids[i]))

    # ---- 2. 关键词通道（含模糊降级）----
    ts = terms(query)
    expanded = expand_concepts(ts)
    ts_fts = ts + expanded
    kw_file_slice, kw_all, coverage, fuzzy = _keyword_channel(store, ts, ts_fts)

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
        if mtime_after is not None and (fr["mtime"] or 0) < mtime_after:
            continue
        if mtime_before is not None and (fr["mtime"] or 0) >= mtime_before:
            continue
        if min_size is not None and (fr["size"] or 0) < min_size:
            continue
        if max_size is not None and (fr["size"] or 0) > max_size:
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
    _sort_scored(scored, sort)
    facets_out = _facets(file_rows, candidates, dir_filter) if with_facets else {}
    # 兜底场景下，score 普遍很低，UI 端应折叠展示
    if fallback:
        for _sc, item in scored:
            item["weak_fallback"] = True

    # 分页：排序完成后切片，offset 只影响组装阶段
    page = scored[offset:offset + limit]

    # ---- 4. 组装结果（批量取 slice / 帧信息，消除 N+1）----
    # 视频命中片段：语义 + 关键词两通道的 per-slice 命中（file_id -> [(score, sid, source)]）
    vid_hits: dict[int, list[tuple[float, int, str]]] = {}
    for _score, item in page:
        fid = item["file_id"]
        if item["row"]["file_type"] != "video":
            continue
        lst = vid_hits.setdefault(fid, [])
        for z_v, sid in vid_sem.get(fid, []):
            lst.append((z_v, sid, "sem"))
        for bm, sid in kw_all.get(fid, []):
            lst.append((bm, sid, "kw"))
    for fid, lst in vid_hits.items():
        lst.sort(key=lambda x: x[0], reverse=True)
        seen: set[int] = set()
        dedup: list[tuple[float, int, str]] = []
        for sc, sid, src in lst:
            if sid in seen:
                continue
            seen.add(sid)
            dedup.append((sc, sid, src))
        vid_hits[fid] = dedup[:8]

    need_sids: set[int] = set()
    for _score, item in page:
        if item["sem_sid"] is not None:
            need_sids.add(item["sem_sid"])
        if item["kw"] is not None:
            need_sids.add(item["kw"][1])
    for lst in vid_hits.values():
        need_sids.update(sid for _sc, sid, _src in lst)
    slice_rows = store.slices_by_ids(list(need_sids)) if need_sids else {}

    # 视频帧位置批量取（仅 video 命中；fast 模式跳过——无 nearby_frames，省全帧扫描）
    frame_locs: dict[int, list[str]] = {}
    if mode != "fast":
        video_fids = [item["file_id"] for _s, item in page
                      if item["row"]["file_type"] == "video" and item.get("video_loc_ts")]
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
                if r["modality"] in ("video_frame", "video_sound") and r["location"]:
                    item["video_loc_ts"] = r["location"]
        if not snippet and kw is not None:
            r = slice_rows.get(kw[1])
            if r:
                snippet = _highlight(r["text_snippet"] or "", ts)
                if r["modality"] in ("video_frame", "video_sound") and r["location"]:
                    item["video_loc_ts"] = r["location"]
        # ---- 视频命中片段列表（前端时间轴标记 + 片段跳转）----
        hits_out: list[dict] = []
        if fr["file_type"] == "video":
            for sc, hsid, src in vid_hits.get(fr["file_id"], []):
                r = slice_rows.get(hsid)
                if not r or not r["location"]:
                    continue
                t_v = _parse_ts(r["location"])
                if t_v is None:
                    continue
                hits_out.append({
                    "t": t_v,
                    "loc": r["location"],
                    "snippet": _highlight(r["text_snippet"] or "", ts),
                    "source": src,
                    "score": round(float(sc), 3),
                })
            # 时间相邻（≤3s）的命中点合并为同一处，保留高分；按时间排序
            merged: list[dict] = []
            for h in sorted(hits_out, key=lambda x: x["t"]):
                if merged and h["t"] - merged[-1]["t"] <= 3.0:
                    if h["score"] > merged[-1]["score"]:
                        merged[-1] = h
                else:
                    merged.append(h)
            hits_out = merged[:8]
            if hits_out:
                best = max(hits_out, key=lambda x: x["score"])
                item["video_loc_ts"] = best["loc"]
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
            "hits": hits_out,
            "confidence": confidence,
            "size": fr["size"] if "size" in fr.keys() else 0,
            "sid": sid,
        })
    if with_facets:
        return out, facets_out
    return out


def search_with_timing(store, embedder, query: str, **kw) -> dict:
    """带耗时信息的检索（CLI / web 用）。facets 始终返回（无成本时为空）。"""
    t0 = time.time()
    with_facets = bool(kw.pop("with_facets", False))
    r = search(store, embedder, query, with_facets=with_facets, **kw)
    if with_facets:
        results, facets = r
    else:
        results, facets = r, {}
    return {"query": query, "ms": round((time.time() - t0) * 1000, 1),
            "results": results, "facets": facets}


def name_search(store, query: str, limit: int = 20, offset: int = 0,
                file_type: str | None = None, dir_filter: str | None = None,
                sort: str = "name",
                mtime_after: float | None = None, mtime_before: float | None = None,
                min_size: int | None = None, max_size: int | None = None) -> dict:
    """`name:` 前缀的纯文件名子串搜索（Everything 风格精确模式）。

    不走向量；大小写不敏感子串匹配；空 `name:` 列出全部文件（浏览模式）。
    返回与 search_with_timing 同构（前端共用渲染）。
    """
    t0 = time.time()
    q = query.strip()
    rows = store.search_files_by_name(q) if q else store.search_files_by_name("")

    kept: list[sqlite3.Row] = []
    facets: dict[str, int] = {}
    for fr in rows:
        if file_type and fr["file_type"] != file_type:
            continue
        if dir_filter and dir_filter.lower() not in fr["path"].lower():
            continue
        if mtime_after is not None and (fr["mtime"] or 0) < mtime_after:
            continue
        if mtime_before is not None and (fr["mtime"] or 0) >= mtime_before:
            continue
        if min_size is not None and (fr["size"] or 0) < min_size:
            continue
        if max_size is not None and (fr["size"] or 0) > max_size:
            continue
        kept.append(fr)
        facets[fr["file_type"]] = facets.get(fr["file_type"], 0) + 1

    # relevance 对文件名搜索无意义，落到 name
    key = sort if sort != "relevance" else "name"
    if key == "mtime_desc":
        kept.sort(key=lambda r: r["mtime"] or 0, reverse=True)
    elif key == "mtime_asc":
        kept.sort(key=lambda r: r["mtime"] or 0)
    elif key == "size_desc":
        kept.sort(key=lambda r: r["size"] or 0, reverse=True)
    else:
        kept.sort(key=lambda r: r["name"] or "")

    page = kept[offset:offset + limit]
    results = [{
        "score": 1.0,
        "path": fr["path"],
        "name": fr["name"],
        "file_type": fr["file_type"],
        "location": "",
        "cos_sim": 0.0,
        "z": 0.0,
        "coverage": 0.0,
        "keyword_rank": None,
        "snippet": "",
        "mtime": fr["mtime"],
        "strong": True,
        "weak_fallback": False,
        "fuzzy_match": False,
        "nearby_frames": [],
        "hits": [],
        "confidence": "high",
        "size": fr["size"] if "size" in fr.keys() else 0,
        "sid": None,
    } for fr in page]
    return {"query": query, "ms": round((time.time() - t0) * 1000, 1),
            "results": results, "facets": facets, "mode": "name"}


def search_by_image(store, embedder, img, limit: int = 20, offset: int = 0,
                    file_type: str | None = None, dir_filter: str | None = None,
                    sort: str = "relevance", with_facets: bool = False,
                    text: str = "", mode: str = "detailed"):
    """以图搜图：上传图片 → 视觉嵌入 → 与库内视觉切片（image/video_frame）cos 检索。

    查询向量与图片/视频帧切片同源（embed_image_pil，BOI token 口径），
    直接 cosine 无跨模态 gap；z-score 沿用 _meaning 的按 modality 分组归一。
    text 非空时走图+文联合嵌入（embed_image_text_pil，"像这张图但要…"）。
    返回结构与 search() 同构（前端共用渲染）；snippet/coverage 等文本通道
    字段置空。
    """
    if text and text.strip():
        qv = np.asarray(embedder.embed_image_text_pil(img, text), dtype=np.float32)
    else:
        qv = np.asarray(embedder.embed_image_pil(img), dtype=np.float32)
    qv /= max(float(np.linalg.norm(qv)), 1e-9)
    return _pool_search(store, qv, limit=limit, offset=offset, file_type=file_type,
                        dir_filter=dir_filter, sort=sort, with_facets=with_facets,
                        vis_only=True, mode=mode)


def search_by_sid(store, embedder, sid: int, limit: int = 20, offset: int = 0,
                  file_type: str | None = None, dir_filter: str | None = None,
                  sort: str = "relevance", with_facets: bool = False,
                  mode: str = "detailed"):
    """More like this：以某个切片的向量做查询，找最相似的文件。

    全模态参与（文本/图片/视频帧），z-score 在参与模态内归一；
    Top-N 池浏览语义与以图搜图一致。
    """
    qv = store.vector_by_sid(sid)
    if qv is None:
        return ([], {}) if with_facets else []
    return _pool_search(store, qv, limit=limit, offset=offset, file_type=file_type,
                        dir_filter=dir_filter, sort=sort, with_facets=with_facets,
                        vis_only=False, mode=mode)


def _pool_search(store, qv, limit: int = 20, offset: int = 0,
                 file_type: str | None = None, dir_filter: str | None = None,
                 sort: str = "relevance", with_facets: bool = False,
                 vis_only: bool = False, mode: str = "detailed",
                 mtime_after: float | None = None, mtime_before: float | None = None,
                 min_size: int | None = None, max_size: int | None = None):

    """向量查询的浏览型检索（以图搜图 / More like this 共用内核）。

    语义是"最相似的 N 个"（浏览型），不是"强匹配才算命中"（文字查询的
    z-gate 是"没找到"语义）。这里按相似度圈定候选池（Top-N，N 随 limit
    放大），排序维度（时间/大小）在池内切换。
    vis_only=True 只比对视觉切片（image/video_frame，图搜图口径）；
    False 时全模态参与（More like this 口径）。
    """
    sids, fids, mods, matrix = store.all_vectors_matrix()
    if vis_only:
        idx = [i for i, m in enumerate(mods) if m in VISION_MODALITIES]
    else:
        idx = list(range(len(mods)))
    if mode == "fast" and len(idx) > 2:
        # 简单模式：视频切片每文件只留 2 个代表位（与文字搜索同策略）
        keep = _fast_mode_keep(fids, mods)
        idx = [i for i in idx if keep[i]]
    if not idx:
        return ([], {}) if with_facets else []

    cos = matrix[idx] @ qv
    vmods = [mods[i] for i in idx]
    z = _meaning(cos, vmods)

    best: dict[int, tuple[float, float, int]] = {}      # fid -> (z, cos, sid)
    hits_by_file: dict[int, list[tuple[float, int]]] = {}
    for j, i in enumerate(idx):
        fid = int(fids[i])
        cur = best.get(fid)
        if cur is None or z[j] > cur[0]:
            best[fid] = (float(z[j]), float(cos[j]), int(sids[i]))
        if z[j] >= Z_GATE:
            hits_by_file.setdefault(fid, []).append((float(z[j]), int(sids[i])))


    pool_n = max(limit * 4, 60)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:pool_n]
    candidates = {fid for fid, _t in ranked}

    if not candidates:
        return ([], {}) if with_facets else []
    file_rows = store.files_by_ids(candidates)
    facets_out = _facets(file_rows, candidates, dir_filter) if with_facets else {}

    scored: list[tuple[float, dict]] = []
    for fid in candidates:
        fr = file_rows.get(fid)
        if fr is None:
            continue
        if file_type and fr["file_type"] != file_type:
            continue
        if dir_filter and dir_filter.lower() not in fr["path"].lower():
            continue
        if mtime_after is not None and (fr["mtime"] or 0) < mtime_after:
            continue
        if mtime_before is not None and (fr["mtime"] or 0) >= mtime_before:
            continue
        if min_size is not None and (fr["size"] or 0) < min_size:
            continue
        if max_size is not None and (fr["size"] or 0) > max_size:
            continue
        z_v, cos_v, sem_sid = best.get(fid, (0.0, 0.0, None))
        scored.append((max(z_v, 0.0), {"file_id": fid, "row": fr, "z": z_v,
                                        "cos": cos_v, "sem_sid": sem_sid}))
    _sort_scored(scored, sort)

    page = scored[offset:offset + limit]
    top_z = scored[0][0] if scored else 0.0

    # 视频帧位置批量取（nearby_frames 用）
    video_fids = [item["file_id"] for _s, item in page
                  if item["row"]["file_type"] == "video" and item["sem_sid"] is not None]
    frame_locs: dict[int, list[str]] = {}
    for vfid in video_fids:
        frame_locs[vfid] = store.frame_locations(vfid)

    need_sids: set[int] = set()
    for _s, item in page:
        if item["sem_sid"] is not None:
            need_sids.add(item["sem_sid"])
    for fid, lst in hits_by_file.items():
        if fid in {item["file_id"] for _s, item in page}:
            need_sids.update(sid for _z, sid in lst)
    slice_rows = store.slices_by_ids(list(need_sids)) if need_sids else {}

    out: list[dict] = []
    for score, item in page:
        fr = item["row"]
        z_v = item["z"]

        location = ""
        sem_r = slice_rows.get(item["sem_sid"]) if item["sem_sid"] is not None else None
        if sem_r is not None and sem_r["location"]:
            location = sem_r["location"]

        # 视频命中片段列表（时间轴标记用；单图命中不需要）
        hits_out: list[dict] = []
        if fr["file_type"] == "video":
            for hz, hsid in sorted(hits_by_file.get(fr["file_id"], []),
                                   key=lambda x: x[0], reverse=True)[:8]:
                r = slice_rows.get(hsid)
                if not r or not r["location"]:
                    continue
                t_v = _parse_ts(r["location"])
                if t_v is None:
                    continue
                hits_out.append({"t": t_v, "loc": r["location"], "snippet": "",
                                 "source": "image", "score": round(hz, 3)})
            merged: list[dict] = []
            for h in sorted(hits_out, key=lambda x: x["t"]):
                if merged and h["t"] - merged[-1]["t"] <= 3.0:
                    if h["score"] > merged[-1]["score"]:
                        merged[-1] = h
                else:
                    merged.append(h)
            hits_out = merged[:8]

        nearby_frames: list[dict] = []
        if fr["file_type"] == "video" and location:
            center = _parse_ts(location)
            if center is not None:
                parsed = sorted(
                    (t, loc) for t, loc in
                    [( _parse_ts(l), l) for l in frame_locs.get(fr["file_id"], [])]
                    if t is not None)
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

        confidence = "high" if z_v >= 2.0 else ("medium" if z_v >= 1.0 else "low")
        out.append({
            "score": round(score, 4),
            "path": fr["path"],
            "name": fr["name"],
            "file_type": fr["file_type"],
            "location": location,
            "cos_sim": round(item["cos"], 4),
            "z": round(z_v, 3),
            "coverage": 0.0,
            "keyword_rank": None,
            "snippet": (sem_r["text_snippet"] or "")[:160] if sem_r is not None else "",
            "mtime": fr["mtime"],
            "strong": score >= top_z * STRONG_RATIO if top_z > 0 else True,
            "weak_fallback": False,
            "fuzzy_match": False,
            "nearby_frames": nearby_frames,
            "hits": hits_out,
            "confidence": confidence,
            "size": fr["size"] if "size" in fr.keys() else 0,
            "sid": item["sem_sid"],
        })
    if with_facets:
        return out, facets_out
    return out

def find_duplicates(store, threshold: float = 0.95, max_clusters: int = 200) -> list[dict]:
    """近似重复图片检测：image 模态切片 cos ≥ threshold 聚簇（Union-Find）。

    只参与 modality=="image"（图片文件）：视频帧同源天然高相似会淹没报告，
    文档切片重复属于"查重"另一场景。图片文件通常单切片，文件级即切片级。
    返回簇列表（成员数降序），每簇 members[0] 为代表（体积最大者）。
    """
    sids, fids, mods, matrix = store.all_vectors_matrix()
    idx = [i for i, m in enumerate(mods) if m == "image"]
    if len(idx) < 2:
        return []

    sub = matrix[idx]
    sub_sids = [int(sids[i]) for i in idx]
    sub_fids = [int(fids[i]) for i in idx]
    g = sub @ sub.T

    # 并查集（按 file_id 归并；同一文件多切片天然同簇）
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # numpy 找边（上三角），union 只跑真实边——避免 O(n²) 的 Python 循环
    pairs = np.argwhere(np.triu(g >= threshold, k=1))
    for a, b in pairs:
        union(sub_fids[int(a)], sub_fids[int(b)])

    clusters: dict[int, list[int]] = {}
    for a in range(len(idx)):
        clusters.setdefault(find(sub_fids[a]), []).append(a)

    out: list[dict] = []
    for _root, members in clusters.items():
        fids_set = {sub_fids[a] for a in members}
        if len(fids_set) < 2:
            continue
        rows = store.files_by_ids(fids_set)
        rep_fid = max(fids_set, key=lambda f: (rows[f]["size"] if f in rows else 0))
        rep_idx = next(a for a in members if sub_fids[a] == rep_fid)
        members_out = []
        for f in fids_set:
            fr = rows.get(f)
            if fr is None:
                continue
            a = next(a for a in members if sub_fids[a] == f)
            members_out.append({
                "path": fr["path"], "name": fr["name"], "size": fr["size"],
                "mtime": fr["mtime"], "sid": sub_sids[a],
                "cos": 1.0 if a == rep_idx else round(float(g[rep_idx, a]), 4),
            })
        members_out.sort(key=lambda m: -m["size"])
        others = [m["cos"] for m in members_out[1:]]
        out.append({"members": members_out,
                    "max_cos": round(max(others), 4) if others else 1.0})
    out.sort(key=lambda c: (-len(c["members"]), -c["max_cos"]))
    return out[:max_clusters]
