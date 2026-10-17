"""索引器：遍历 roots → 读取 → 切片 → 向量化 → 入库（PRD FR-1 / FR-9）。

增量策略：files 表记录 (size, mtime)，未变则跳过；
每次索引对 roots 做一次对账（磁盘上已消失的文件删索引）。

2026-10-16 工程化改造：
- P0 修复：文本/PDF/docx 解析纳入 per-file try（此前读取循环在 try 块外，
  文件被 Word/Excel 独占打开或损坏时会炸掉整轮索引）
- 进度/取消：progress 回调 + threading.Event 取消检查点（供后台任务用）
- 批量事务：store.batch() 每 BATCH_COMMIT_FILES 个文件统一 commit
- index_paths()：watcher 定向增量（只处理变更路径，不整目录遍历）
- print → logging
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import config, readers
from .log import get_logger
from .searchtext import has_words, strip_watermarks
from .slicer import chunk_text

log = get_logger("trace.indexer")


def _clean(s: str) -> str:
    """写切片前的水印/空白归一：让"摄图网/大图网"等水印不进 vec/slices/fts。"""
    return strip_watermarks(s or "")


def _kw_with_name(path: Path, kw_extra: str = "") -> str:
    """组装 keywords：filename（含扩展） + 文件夹名 + 任何补充。
    关键：把 path.name 整体放进去 → 搜 "男性声音" 命中 男性声音.mp3
    """
    parts = [path.name, path.stem, path.parent.name]
    if kw_extra:
        parts.append(kw_extra)
    return _clean(" ".join(p for p in parts if p))


def _excluded(path: Path, root: Path) -> bool:
    for part in path.relative_to(root).parts:
        if part in config.EXCLUDE_DIR_NAMES:
            return True
    name = path.name
    low = name.lower()
    if low in config.EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in config.EXCLUDE_FILE_SUFFIXES:
        return True
    return False


def _iter_files(root: Path):
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):
            continue
        for e in entries:
            try:
                if e.is_dir():
                    if e.name in config.EXCLUDE_DIR_NAMES:
                        continue
                    stack.append(e)
                elif e.is_file() and not _excluded(e, root):
                    yield e
            except (PermissionError, OSError):
                continue


class Indexer:
    def __init__(self, store, embedder, verbose=True):
        self.store = store
        self.embedder = embedder
        self.verbose = verbose
        self._count_fn = lambda t: len(self.embedder.tok.encode(t, add_special_tokens=False).ids)

    # ------------------------------------------------------------------ #
    # 单文件处理（index_root 与 index_paths 共用）
    # ------------------------------------------------------------------ #
    def _extract_slices(self, path: Path, kind: str, stats: dict) -> list[dict] | None:
        """文件 → 切片列表。任何解析异常都降级为 metadata_only，不炸整轮。

        返回 None = 用户取消（视频/音频长流水线中检到 cancel 事件），
        调用方应放弃该文件不入库。
        """
        slices: list[dict] = []
        if kind == "image" and self.embedder.vision_session is not None:
            # 图片语义切片：视觉编码器向量 + OCR 关键词（DigUp：OCR 不进向量）
            from . import ocr

            vec = self.embedder.embed_image(spath := str(path))
            ocr_text = ocr.ocr_image(spath)
            kw_combined = _kw_with_name(path, ocr_text)
            text_clean = _clean(ocr_text)[:200]
            if not text_clean:
                text_clean = path.stem
            slices = [{
                "modality": "image",
                "location": "none",
                "text": text_clean,
                "keywords": kw_combined,
                "vector": vec,
            }]
        elif kind == "video" and self.embedder.vision_session is not None:
            # 视频帧 + 音轨：抽帧/解音轨在后台线程（解码多线程已开），
            # 主线程攒批嵌入（主模型批量 2.3x）——三阶段流水线重叠。
            # 文件名线索经 _kw_with_name 注入（文件名有语义的视频能被 FTS 命中）
            # 取消：cancel 事件穿透进 readers 解码循环 + 消费端批间检查，
            # 返回 None 表示被取消——调用方放弃该文件（不入库，下次重跑）。
            from . import readers as _rd
            import queue as _queue

            _SENTINEL = object()
            _cancel = getattr(self, "_cancel", None)
            frame_q: "queue.Queue" = _queue.Queue(maxsize=32)
            audio_q: "queue.Queue" = _queue.Queue(maxsize=4)

            def _produce_frames():
                try:
                    for raw in _rd.iter_video_slices(path, embedder=None,
                                                     cancel=_cancel):
                        frame_q.put(raw)
                except Exception:
                    pass
                finally:
                    frame_q.put(_SENTINEL)

            def _produce_audio():
                try:
                    for raw in _rd.iter_audio_slices(path, embedder=None,
                                                     source_kind="video_sound",
                                                     cancel=_cancel):
                        audio_q.put(raw)
                except Exception:
                    pass
                finally:
                    audio_q.put(_SENTINEL)

            threading.Thread(target=_produce_frames, daemon=True).start()
            threading.Thread(target=_produce_audio, daemon=True).start()

            def _drain(q):
                while True:
                    item = q.get()
                    if item is _SENTINEL:
                        return
                    yield item

            IMG_BATCH = 8
            fbuf = []
            cancelled = False
            for raw in _drain(frame_q):
                if _cancel is not None and _cancel.is_set():
                    cancelled = True
                    break
                fbuf.append(raw)
                if len(fbuf) >= IMG_BATCH:
                    self._flush_video_frames(path, fbuf, slices)
            if cancelled:
                # 排空两队列：生产者（生成器内已检 cancel）会很快送出 SENTINEL 退出，
                # 避免线程卡在 put 上占着视频文件句柄
                for _ in _drain(frame_q):
                    pass
                for _ in _drain(audio_q):
                    pass
                return None
            self._flush_video_frames(path, fbuf, slices)

            if getattr(self.embedder, "audio_session", None) is not None:
                AUD_BATCH = 8
                abuf = []
                for raw in _drain(audio_q):
                    if _cancel is not None and _cancel.is_set():
                        cancelled = True
                        break
                    abuf.append(raw)
                    if len(abuf) >= AUD_BATCH:
                        self._flush_audio_windows(path, abuf, slices)
                if cancelled:
                    for _ in _drain(audio_q):
                        pass
                    return None
                self._flush_audio_windows(path, abuf, slices)
            else:
                # 无 audio 编码器：排空队列避免生产者线程卡在 put 上
                for _ in _drain(audio_q):
                    pass
        elif kind == "audio" and getattr(self.embedder, "audio_session", None) is not None:
            # 独立音频文件：30s 窗口批量嵌入
            from . import readers as _rd

            _cancel = getattr(self, "_cancel", None)
            AUD_BATCH = 8
            abuf = []
            for raw in _rd.iter_audio_slices(path, embedder=None, source_kind="audio",
                                             cancel=_cancel):
                if _cancel is not None and _cancel.is_set():
                    return None
                abuf.append(raw)
                if len(abuf) >= AUD_BATCH:
                    self._flush_audio_windows(path, abuf, slices)
            self._flush_audio_windows(path, abuf, slices)
        elif kind in ("image", "audio", "video"):
            pass  # 编码器未就绪，仅登记元数据
        else:
            # 文本/PDF/docx：解析 + 切片（P0：纳入 try，独占/损坏文件不再炸整轮）
            for raw in readers.iter_slices(path):
                for block in chunk_text(raw["text"], self._count_fn):
                    text_clean = _clean(block) or path.stem
                    kw_combined = _kw_with_name(path, raw.get("keywords", ""))
                    slices.append({
                        "modality": raw["modality"],
                        "location": raw["location"],
                        "text": text_clean,
                        "keywords": kw_combined,
                    })
        return slices

    # ------------------------------------------------------------------ #
    # 视频/音频批量 flush（流水线消费端）
    # ------------------------------------------------------------------ #
    def _flush_video_frames(self, path: Path, buf: list[dict], out: list[dict]) -> None:
        """攒批的视频帧 → 批量嵌入 → 组装切片 dict 追加进 out。"""
        if not buf:
            return
        try:
            vecs = self.embedder.embed_images_pil_batch([r["_pil"] for r in buf])
        except Exception:
            vecs = [None] * len(buf)
        for raw, vec in zip(buf, vecs):
            kw_combined = _kw_with_name(path, raw.get("keywords", ""))
            text = _clean(raw.get("text") or "") or path.stem
            out.append({
                "modality": raw["modality"],
                "location": raw["location"],
                "text": text,
                "keywords": kw_combined,
                "vector": vec,
            })
        buf.clear()

    def _flush_audio_windows(self, path: Path, buf: list[dict], out: list[dict]) -> None:
        """攒批的音频窗 → 批量嵌入 → 组装切片 dict 追加进 out。"""
        if not buf:
            return
        try:
            vecs = self.embedder.embed_audio_pcm_batch([r["_pcm"] for r in buf], sr=16000)
        except Exception:
            vecs = [None] * len(buf)
        for raw, vec in zip(buf, vecs):
            kw_combined = _kw_with_name(path, raw.get("keywords", ""))
            text = _clean(raw.get("text") or "") or path.stem
            out.append({
                "modality": raw["modality"],
                "location": raw["location"],
                "text": text,
                "keywords": kw_combined,
                "vector": vec,
            })
        buf.clear()

    def _index_one(self, path: Path, st, full: bool, stats: dict) -> None:
        """处理单个文件：提取切片 → 向量化 → 入库。所有异常 per-file 吞掉记 errors。"""
        spath = str(path)
        kind = readers.classify(path)
        if kind == "other":
            return
        old = self.store.get_file(spath)
        if old and not full and old["size"] == st.st_size and abs(old["mtime"] - st.st_mtime) < 1e-6:
            stats["skipped"] += 1
            return

        slices: list[dict] = []
        status = "indexed"
        try:
            slices = self._extract_slices(path, kind, stats)
            if slices is None:
                # 用户取消：放弃该文件（不 upsert，保留旧记录），下次任务重跑
                return
            if not slices and kind in ("image", "audio", "video"):
                status = "metadata_only"
            elif not slices:
                status = "metadata_only"
        except Exception as e:
            # 单文件失败不拖垮整轮（DigUp：per-file problems 记录在案）。
            # 覆盖：PermissionError（文件被 Word/Excel 独占）、BadZipFile（损坏
            # docx/odt）、PdfReader 构造失败、OCR/解码失败等。
            status = "metadata_only"
            stats["errors"] += 1
            log.warning("解析失败 %s: %s", spath, e)

        if status == "metadata_only":
            stats["metadata_only"] += 1

        file_id = self.store.upsert_file(spath, st.st_size, st.st_mtime, kind, kind, status)
        try:
            if slices:
                # 标题 prompt（DigUp Prompts.document）+ hasWords 过滤
                # （纯数字/表格文本只进 FTS 关键词，不浪费向量——DigUp 实测
                #  97 万字符数字转储会产生 607 个无意义向量）
                title = path.stem
                to_embed = [s for s in slices if s.get("vector") is None and has_words(s["text"])]
                if to_embed:
                    import numpy as np

                    vecs = self.embedder.embed_documents_titled(
                        [(title, s["text"]) for s in to_embed])
                    vmap = {id(s): np.asarray(v, dtype=np.float32) for s, v in zip(to_embed, vecs)}
                    for s in slices:
                        s["vector"] = vmap.get(id(s), s.get("vector"))
                self.store.replace_slices(file_id, slices)
                stats["indexed"] += 1
            else:
                self.store.replace_slices(file_id, [])
        except Exception as e:
            stats["errors"] += 1
            log.warning("入库失败 %s: %s", spath, e)

    # ------------------------------------------------------------------ #
    # 整目录索引
    # ------------------------------------------------------------------ #
    def index_root(self, root: str, full: bool = False,
                   progress=None, cancel: threading.Event | None = None) -> dict:
        """索引一个目录。

        Args:
            progress: 可选回调 progress(stats_dict)，每个文件处理后触发（含
                      scanned/total/indexed/…，GUI 轮询友好）。
            cancel:   取消信号；置位后在下一个文件边界安全中止（返回部分
                      结果，stats["cancelled"]=True）。
        """
        root_path = Path(root).resolve()
        self._cancel = cancel  # 供 _extract_slices 流水线批间检查
        try:
            return self._index_root_impl(root, full, progress, cancel, root_path)
        finally:
            self._cancel = None

    def _index_root_impl(self, root: str, full: bool, progress, cancel, root_path: Path) -> dict:

        # 指纹检查：模型/prompt/切片参数变化后，旧向量不可与新向量比较（DigUp 同款）
        fp_now = config.FINGERPRINT
        fp_old = self.store.get_meta("fingerprint")
        if fp_old is not None and fp_old != fp_now and not full:
            log.warning("索引指纹已变化（%s → %s），旧向量不可混用，请用 --full 全量重建。",
                        fp_old, fp_now)
            return {"scanned": 0, "indexed": 0, "skipped": 0, "metadata_only": 0,
                    "errors": 0, "removed": 0, "seconds": 0.0, "total": 0, "cancelled": False}

        self.store.add_root(str(root_path))
        stats = {"scanned": 0, "indexed": 0, "skipped": 0, "metadata_only": 0,
                 "errors": 0, "removed": 0, "total": 0, "cancelled": False}
        t0 = time.time()
        seen: set[str] = set()

        # 预扫描：先统计总数（stat 遍历快），让进度有百分比
        all_files = list(_iter_files(root_path))
        stats["total"] = len(all_files)
        if progress:
            progress(dict(stats))

        batch_n = 0
        for path in all_files:
            if cancel is not None and cancel.is_set():
                stats["cancelled"] = True
                break
            spath = str(path)
            # 进入文件前先上报 current（大文件嵌入可达数分钟）
            if progress:
                progress(dict(stats, current=spath))
            stats["scanned"] += 1
            seen.add(spath)
            try:
                st = path.stat()
            except OSError:
                stats["errors"] += 1
                continue
            try:
                with self.store.batch():
                    self._index_one(path, st, full, stats)
            except Exception as e:
                stats["errors"] += 1
                log.warning("索引失败 %s: %s", spath, e)
            batch_n += 1
            if progress:
                progress(dict(stats, current=spath))
            if self.verbose and stats["scanned"] % 50 == 0:
                log.info("… 已扫描 %d/%d 个文件（索引 %d，跳过 %d）",
                         stats["scanned"], stats["total"],
                         stats["indexed"], stats["skipped"])
            if batch_n >= config.BATCH_COMMIT_FILES:
                batch_n = 0  # batch() 已在退出时 commit；计数器仅用于日志节奏

        # 对账：root 下已消失的文件删索引。
        # 安全护栏：若 seen 数远小于 DB 现有数，说明遍历被中断（强制 kill / 异常），
        # 此时删除会把误判"已消失"的全删光，必须跳过 GC。
        if not stats["cancelled"]:
            db_files = self.store.paths_under(str(root_path))
            if len(db_files) > 0 and len(seen) < 0.8 * len(db_files):
                log.warning("索引扫描提前结束（seen=%d / db=%d），跳过对账删除以防误删。",
                            len(seen), len(db_files))
            else:
                for p in db_files:
                    if p not in seen and self.store.delete_file(p):
                        stats["removed"] += 1

        stats["seconds"] = round(time.time() - t0, 1)
        self.store.set_meta("fingerprint", config.FINGERPRINT)
        if progress:
            progress(dict(stats))
        return stats

    # ------------------------------------------------------------------ #
    # 定向增量（watcher 用）：只处理给定路径，不整目录遍历
    # ------------------------------------------------------------------ #
    def index_paths(self, paths: list[str], progress=None,
                    cancel: threading.Event | None = None) -> dict:
        stats = {"scanned": 0, "indexed": 0, "skipped": 0, "metadata_only": 0,
                 "errors": 0, "removed": 0, "total": len(paths), "cancelled": False}
        t0 = time.time()
        self._cancel = cancel  # 供 _extract_slices 流水线批间检查
        try:
            for spath in paths:
                if cancel is not None and cancel.is_set():
                    stats["cancelled"] = True
                    break
                # 进入文件前先上报（大文件嵌入可达数分钟，首个进度不能等它完成才有）
                if progress:
                    progress(dict(stats, current=spath))
                path = Path(spath)
                stats["scanned"] += 1
                try:
                    st = path.stat()
                except OSError:
                    # 文件已消失（watcher 检测后又被删）：对账删索引
                    if self.store.delete_file(spath):
                        stats["removed"] += 1
                    continue
                kind = readers.classify(path)
                if kind == "other":
                    continue
                try:
                    with self.store.batch():
                        self._index_one(path, st, False, stats)
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("定向索引失败 %s: %s", spath, e)
        finally:
            self._cancel = None

        stats["seconds"] = round(time.time() - t0, 1)
        if progress:
            progress(dict(stats))
        return stats
