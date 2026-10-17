"""读取层：把各类文件转成纯文本（对应 PRD FR-2 解析方式列）。

本期实现：txt/md/代码类、docx/odt/rtf、pdf（逐页）。
image/audio/video reader 先落地为元数据占位（返回空切片列表），
视觉/音频编码器接入后补语义切片——见 README「下一步」。
"""

from __future__ import annotations

import re
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Iterator

from . import config


# --------------------------------------------------------------------- #
# 文本文档
# --------------------------------------------------------------------- #
def _read_docx(path: Path) -> str:
    """docx = zip，抽 word/document.xml 的 <w:t> 文本，段间隔保留为换行。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return _unescape_xml(text)


def _read_odt(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("content.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</text:p>", "\n", xml)
    xml = re.sub(r"</text:h>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return _unescape_xml(text)


def _read_rtf(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", raw)
    raw = raw.replace("{", "").replace("}", "")
    return raw


def _read_html(path: Path) -> str:
    from html.parser import HTMLParser

    class _Strip(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip += 1
            if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    p = _Strip()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    return "".join(p.parts)


def _unescape_xml(text: str) -> str:
    return (
        text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        .replace("&apos;", "'").replace("&amp;", "&")
    )


def read_text(path: Path) -> str:
    """纯文本/代码/结构化文本统一入口。"""
    ext = path.suffix.lower()
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".odt":
        return _read_odt(path)
    if ext == ".rtf":
        return _read_rtf(path)
    if ext in (".html", ".htm", ".xml"):
        return _read_html(path)
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------- #
# PDF：逐页产出 (页码, 文本)
# --------------------------------------------------------------------- #
def read_pdf_pages(path: Path, max_pages_first: int | None = None) -> Iterator[tuple[int, str]]:
    """PRD：长文档先索引前 30 页，其余后台懒加载。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    total = len(reader.pages)
    limit = total if max_pages_first is None else min(total, max_pages_first)
    for i in range(limit):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        yield i + 1, text.strip()


# --------------------------------------------------------------------- #
# 图片 / 音频 / 视频：占位 reader（视觉/音频编码器接入后补语义切片）
# --------------------------------------------------------------------- #
def read_image_meta(path: Path) -> dict:
    """只取元数据，不产切片。视觉语义切片待 vision_encoder.onnx 接入。"""
    meta = {"width": None, "height": None}
    try:
        from PIL import Image

        with Image.open(path) as im:
            meta["width"], meta["height"] = im.size
    except Exception:
        pass
    return meta


def read_media_meta(path: Path) -> dict:
    """音视频时长等元数据（无 ffmpeg 时仅返回存在性）。"""
    ext = path.suffix.lower()
    if ext in config.AUDIO_EXTS:
        return {"modality": "audio"}
    if ext in config.VIDEO_EXTS:
        meta = {"modality": "video", "duration_s": None, "fps": None}
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            if cap.isOpened():
                meta["fps"] = cap.get(cv2.CAP_PROP_FPS) or None
                meta["duration_s"] = (cap.get(cv2.CAP_PROP_FRAME_COUNT) /
                                       meta["fps"]) if meta["fps"] else None
            cap.release()
        except Exception:
            pass
        return meta
    return {}


def _format_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _parse_ts(s: str) -> float | None:
    """"HH:MM:SS" / "MM:SS" / "SS" → 秒数。空串/格式错返回 None。"""
    if not s:
        return None
    parts = s.split(":")
    try:
        sec = 0.0
        for p in parts:
            sec = sec * 60 + float(p)
        return sec
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------- #
# 视频帧去重：8×8 平均哈希（DigUp 口径：XOR 后 ≤4 bit 视为同镜头）
# --------------------------------------------------------------------- #
def _frame_signature(frame_bgr, side: int = 8) -> tuple[int, float]:
    """cv2 BGR 帧 → (64-bit 平均灰度哈希, 灰度均值 0-255)。

    单靠 aHash 对纯色（蓝屏/黑屏）失效——所有像素=均值 → 哈希=0，
    误把不同纯色帧判为同镜头。配套规则：aHash 相近时若均值差 > 10，
    仍视为不同镜头。
    """
    import cv2
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if (gray.shape[1], gray.shape[0]) != (side, side):
        gray = cv2.resize(gray, (side, side), interpolation=cv2.INTER_AREA)
    mean = float(gray.mean())
    bits = 0
    for i, v in enumerate(gray.flatten()):
        if v > mean:
            bits |= 1 << i
    return bits, mean


def _is_same_shot(curr: tuple[int, float], prev: tuple[int, float],
                  tolerance: int, mean_threshold: float = 10.0) -> bool:
    """判定两帧是否同一镜头。

    DigUp 口径：aHash XOR ≤ tolerance bit 视为同；额外补丁：纯色帧（aHash
    都是 0 / 都是全 1）靠均值差区分。
    """
    h1, m1 = curr
    h2, m2 = prev
    if (h1 ^ h2).bit_count() > tolerance:
        return False
    if abs(m1 - m2) > mean_threshold:
        return False
    return True


def _video_pil(arr, max_pixel: int):
    """BGR ndarray → 缩放到 max_pixel 长边的 RGB PIL.Image。"""
    import cv2
    from PIL import Image
    h0, w0 = arr.shape[:2]
    if max(h0, w0) > max_pixel:
        scale = max_pixel / max(h0, w0)
        arr = cv2.resize(arr, (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                         interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def _open_video_threaded(path):
    """打开视频 + 流多线程，返回 (container, video_stream)。"""
    import av
    container = av.open(str(path))
    v_stream = next((s for s in container.streams if s.type == "video"), None)
    if v_stream is None:
        container.close()
        return None, None
    try:
        v_stream.codec_context.thread_count = 0  # auto
        v_stream.codec_context.thread_type = "AUTO"
    except Exception:
        pass
    return container, v_stream


def _iter_video_scene_slices(path: Path, embedder, *, max_pixel: int,
                             ahash_tolerance: int, max_frames: int,
                             cancel=None) -> Iterator[dict]:
    """场景模式抽帧：只解关键帧（skip_frame=NONKEY）+ aHash 场景检测 + 超上限等距抽稀。

    用户口径（2026-10-17）：覆盖视频大部分场景即可，静止/缓变画面忽略，
    几小时视频抽 ~N 个代表镜头用于检索。两遍法恒定内存：
    第一遍只算 aHash 不留像素，定下保留帧时间点；
    第二遍同参数再解一遍，命中帧缩放 512px 产出。
    实测 65min 视频：501 关键帧 / 每遍 ~9s（vs tick 模式 seek 抽帧 87s+335 帧嵌入）。
    cancel: threading.Event；置位后在解码循环边界立即返回（用户取消索引）。
    """
    import av
    limit = max(1, min(max_frames, config.VIDEO_SCENE_MAX_SLICES))

    # ---- 第一遍：关键帧 aHash，找出场景边界帧 ----
    sigs: list[tuple[int, float]] = []
    tss: list[float] = []
    container, v_stream = _open_video_threaded(path)
    if v_stream is None:
        return
    try:
        v_stream.codec_context.skip_frame = "NONKEY"  # 丢弃全部 P/B 帧
        for f in container.decode(v_stream):
            if cancel is not None and cancel.is_set():
                return
            sigs.append(_frame_signature(f.to_ndarray(format="bgr24"),
                                         side=config.VIDEO_AHASH_SIDE))
            tss.append(f.time or 0.0)
    finally:
        container.close()
    if not sigs:
        return

    scene_idx: list[int] = []
    last_sig = None
    for i, s in enumerate(sigs):
        if last_sig is not None and _is_same_shot(s, last_sig, ahash_tolerance):
            continue
        last_sig = s
        scene_idx.append(i)

    # 场景帧数超上限 → 等距抽稀（保首尾）
    if len(scene_idx) > limit:
        n = len(scene_idx)
        picked = sorted({int(round(k * (n - 1) / (limit - 1))) for k in range(limit)})
        picked[0] = 0
        picked[-1] = n - 1
        scene_idx = [scene_idx[k] for k in picked]
    keep = set(scene_idx)

    # ---- 第二遍：同参数再解一遍，命中帧产出 ----
    container, v_stream = _open_video_threaded(path)
    if v_stream is None:
        return
    try:
        v_stream.codec_context.skip_frame = "NONKEY"
        for i, f in enumerate(container.decode(v_stream)):
            if cancel is not None and cancel.is_set():
                return
            if i not in keep:
                continue
            actual_t = tss[i] if i < len(tss) else (f.time or 0.0)
            img = _video_pil(f.to_ndarray(format="bgr24"), max_pixel)
            slice_dict = {
                "modality": "video_frame",
                "location": _format_ts(actual_t),
                "text": "",
                "keywords": f"{path.stem} t={_format_ts(actual_t)}",
                "_pil": img,
                "_ts": actual_t,
                "_reason": "scene",
            }
            if embedder is not None:
                try:
                    slice_dict["vector"] = embedder.embed_image_pil(img)
                except Exception:
                    pass
            yield slice_dict

    finally:
        container.close()


def iter_video_slices(path: Path, embedder=None, *,
                     sample_interval_sec: float | None = None,
                     max_frames: int = 1200,
                     ahash_tolerance: int | None = None,
                     max_pixel: int | None = None,
                     cancel=None) -> Iterator[dict]:
    """视频帧切片（两种模式，config.VIDEO_SCENE_MODE 控制）：

    - scene（默认）：skip_frame=NONKEY 只解关键帧 + aHash 场景检测 +
      超上限等距抽稀（VIDEO_SCENE_MAX_SLICES，默认 48）。覆盖场景即可，
      静止/缓变画面零嵌入成本——几小时视频抽几十个代表镜头。
    - tick（回退，TRACE_VIDEO_SCENE=0）：每 N 秒抽一帧 + aHash 去重
      （DigUp VideoExtractor.keyframes 口径）。

    Args:
        path: 视频文件路径
        embedder: 可选；若提供 embedder.embed_image_pil 则每帧嵌入视觉向量
        sample_interval_sec: tick 模式采样间隔（默认 config.VIDEO_FRAME_INTERVAL_SEC）
        max_frames: 单视频最多产出的切片数（防爆，默认 1200；scene 模式
                    与 VIDEO_SCENE_MAX_SLICES 取小）
        ahash_tolerance: aHash 重复阈值（默认 config.VIDEO_AHASH_TOLERANCE）
        max_pixel: 抽帧前长边缩放（默认 config.VIDEO_FRAME_MAX_PIXEL）
    """
    import cv2
    import numpy as np
    from PIL import Image

    sample_interval_sec = sample_interval_sec if sample_interval_sec is not None else config.VIDEO_FRAME_INTERVAL_SEC
    ahash_tolerance = ahash_tolerance if ahash_tolerance is not None else config.VIDEO_AHASH_TOLERANCE
    max_pixel = max_pixel if max_pixel is not None else config.VIDEO_FRAME_MAX_PIXEL

    # ---- 1. 用 PyAV 取时长（cv2 拿不准 .mkv/.mov 的 frame count）----
    duration_s: float | None = None
    try:
        import av
        with av.open(str(path)) as container:
            v_stream = next((s for s in container.streams if s.type == "video"), None)
            if v_stream is not None and v_stream.duration is not None and v_stream.time_base:
                duration_s = float(v_stream.duration * v_stream.time_base)
    except Exception:
        pass
    if duration_s is None:
        # cv2 fallback
        cap_meta = cv2.VideoCapture(str(path))
        if cap_meta.isOpened():
            fps_m = cap_meta.get(cv2.CAP_PROP_FPS) or 25.0
            total_m = int(cap_meta.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if fps_m > 0 and total_m > 0:
                duration_s = total_m / fps_m
        cap_meta.release()
    if not duration_s or duration_s <= 0:
        return

    # ---- 1.5 场景模式（默认）：关键帧-only 解码 + aHash 场景检测 + 上限抽稀 ----
    if config.VIDEO_SCENE_MODE:
        try:
            yield from _iter_video_scene_slices(
                path, embedder, max_pixel=max_pixel,
                ahash_tolerance=ahash_tolerance, max_frames=max_frames,
                cancel=cancel)
            return
        except Exception:
            pass  # 场景路径失败 → 回退下方 tick 均匀采样

    # ---- 2. 步长：固定间隔 OR 等比分到 max_frames（DigUp 公式）----
    step = max(sample_interval_sec, duration_s / max_frames)
    # 采样时刻列表（首帧 = min(0.5, duration/2)，与 DigUp 一致）
    times = []
    t = min(0.5, duration_s / 2)
    while t < duration_s:
        times.append(t)
        t += step
    if not times:
        return

    # ---- 3. 用 PyAV 跳到每个时刻（容差 step/2 走最近 IDR 帧）----
    last_sig: tuple[int, float] | None = None
    out_count = 0
    try:
        import av
        with av.open(str(path)) as container:
            v_stream = next((s for s in container.streams if s.type == "video"), None)
            if v_stream is None:
                return
            # 解码多线程（frame threading）：实测长 GOP 视频抽帧 4x 提速。
            # 注意：必须设 codec_context（设 stream.thread_count 属性无效）
            try:
                v_stream.codec_context.thread_count = 0  # auto
                v_stream.codec_context.thread_type = "AUTO"
            except Exception:
                pass
            tb = float(v_stream.time_base) if v_stream.time_base else 1/25
            # 提升到毫秒级时间基，避免漂移
            # （av>=14 改名 AVRational、旧版叫 AvRational，两代都是 Fraction 子类，
            #   直接赋 Fraction 跨版本兼容）
            v_stream.time_base = Fraction(1, 1000)
            for target_t in times:
                if cancel is not None and cancel.is_set():
                    return
                if out_count >= max_frames:
                    break
                target_ms = int(target_t * 1000)
                # seek 到 target 之前最近的关键帧
                container.seek(target_ms, backward=True, any_frame=False, stream=v_stream)
                frame = None
                actual_t = target_t
                for f in container.decode(v_stream):
                    pts_ms = f.pts if f.pts is not None else 0
                    if pts_ms >= target_ms:
                        frame = f
                        actual_t = pts_ms / 1000.0
                        break
                if frame is None:
                    continue
                # 转 ndarray BGR
                arr = frame.to_ndarray(format="bgr24")
                # aHash 去重
                sig = _frame_signature(arr, side=config.VIDEO_AHASH_SIDE)
                if last_sig is not None and _is_same_shot(sig, last_sig, ahash_tolerance):
                    continue
                last_sig = sig
                # 抽帧前缩到 max_pixel
                h0, w0 = arr.shape[:2]
                if max(h0, w0) > max_pixel:
                    scale = max_pixel / max(h0, w0)
                    arr = cv2.resize(arr, (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                                     interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                slice_dict = {
                    "modality": "video_frame",
                    "location": _format_ts(actual_t),
                    "text": "",
                    "keywords": f"{path.stem} t={_format_ts(actual_t)}",
                    "_pil": img,
                    "_ts": actual_t,
                    "_reason": "tick-or-shot",
                }
                if embedder is not None:
                    try:
                        slice_dict["vector"] = embedder.embed_image_pil(img)
                    except Exception:
                        pass
                yield slice_dict
                out_count += 1
    except Exception:
        # PyAV 失败（极老编码器/损坏文件）→ 回退 cv2 步进式
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        last_sig = None
        out_count = 0
        for target_t in times:
            if cancel is not None and cancel.is_set():
                cap.release()
                return
            if out_count >= max_frames:
                break
            cap.set(cv2.CAP_PROP_POS_MSEC, target_t * 1000.0)
            ok, arr = cap.read()
            if not ok or arr is None:
                continue
            sig = _frame_signature(arr, side=config.VIDEO_AHASH_SIDE)
            if last_sig is not None and _is_same_shot(sig, last_sig, ahash_tolerance):
                continue
            last_sig = sig
            h0, w0 = arr.shape[:2]
            if max(h0, w0) > max_pixel:
                scale = max_pixel / max(h0, w0)
                arr = cv2.resize(arr, (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                                 interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            slice_dict = {
                "modality": "video_frame",
                "location": _format_ts(target_t),
                "text": "",
                "keywords": f"{path.stem} t={_format_ts(target_t)}",
                "_pil": img,
                "_ts": target_t,
                "_reason": "fallback",
            }
            if embedder is not None:
                try:
                    slice_dict["vector"] = embedder.embed_image_pil(img)
                except Exception:
                    pass
            yield slice_dict
            out_count += 1
        cap.release()


# --------------------------------------------------------------------- #
# 音频窗口：30s / 25s 滑窗 + 静音剔除（DigUp AudioExtractor.windows 口径）
# --------------------------------------------------------------------- #
def _rms_int16(samples) -> float:
    """int16 PCM 块的均方根（0-1 标幺）。"""
    import numpy as np
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((samples.astype(np.float32) / 32768.0) ** 2)))


def iter_audio_windows(path: Path, *,
                      window_sec: float | None = None,
                      hop_sec: float | None = None,
                      min_sec: float = 2.0,
                      silence_rms: float = 0.003,
                      sr: int = 16000,
                      cancel=None) -> Iterator[tuple[np.ndarray, float, float]]:
    """音频文件（或视频音轨）→ (int16 PCM, start_s, end_s) 流。

    仿 DigUp AudioExtractor.windows：
    - PyAV 重采样到 sr Hz mono int16，恒定内存
    - 30s 窗口，25s 步长（5s 重叠）
    - 整窗口 RMS < silence_rms（约 −50 dBFS）即视为静音丢弃
    - 短于窗口的尾段 ≥ min_sec 单独发

    Yields:
        (pcm_int16 [window_samples], start_sec, end_sec)
    """
    import numpy as np
    import av

    window_sec = window_sec if window_sec is not None else config.AUDIO_WINDOW_SEC
    hop_sec = hop_sec if hop_sec is not None else config.AUDIO_STRIDE_SEC

    window_samples = int(window_sec * sr)
    hop_samples = int(hop_sec * sr)
    min_samples = int(min_sec * sr)

    try:
        container = av.open(str(path))
    except Exception:
        return

    try:
        audio_stream = next((s for s in container.streams if s.type == "audio"), None)
        if audio_stream is None:
            return
        # 音轨解码多线程（与视频抽帧同理）
        try:
            audio_stream.codec_context.thread_count = 0  # auto
            audio_stream.codec_context.thread_type = "AUTO"
        except Exception:
            pass

        resampler = av.AudioResampler(
            format="s16", layout="mono", rate=sr,
        )

        buffer = np.empty(0, dtype=np.int16)
        offset = 0   # sample 索引（buffer[0] 在文件中的位置）
        tail_emitted = False

        def _emit(count: int, start_sample: int) -> np.ndarray:
            return buffer[:count].copy()

        for frame in container.decode(audio_stream):
            if cancel is not None and cancel.is_set():
                return
            resampled = resampler.resample(frame)
            if isinstance(resampled, list):
                resampled = resampled[0] if resampled else None
            if resampled is None:
                continue
            # 拿到 int16 ndarray
            pcm = resampled.to_ndarray()
            if pcm.ndim == 2:
                # PyAV AudioFrame 转出来是 (channels, samples) 布局
                # mono 时只有 1 通道 → 取全部 samples（[0, :]）
                pcm = pcm[0, :]
            pcm = pcm.astype(np.int16, copy=False)
            buffer = np.concatenate([buffer, pcm]) if buffer.size else pcm

            while buffer.size >= window_samples:
                if _rms_int16(buffer[:window_samples]) > silence_rms:
                    arr = _emit(window_samples, offset)
                    yield arr, offset / sr, (offset + window_samples) / sr
                buffer = buffer[hop_samples:]
                offset += hop_samples

        # 尾段：上一窗口覆盖到 offset+window_samples-hop_samples，新音频起点 = offset+window_samples-hop_samples
        already_covered = window_samples - hop_samples
        tail = buffer.size - already_covered
        if tail >= min_samples:
            if _rms_int16(buffer[:buffer.size]) > silence_rms:
                yield buffer.copy(), (offset + already_covered) / sr, (offset + buffer.size) / sr
            tail_emitted = True
        # 若文件总长 < window_samples：单独整段发
        if not tail_emitted and offset == 0 and buffer.size >= min_samples:
            if _rms_int16(buffer) > silence_rms:
                yield buffer.copy(), 0.0, buffer.size / sr
    except Exception:
        return
    finally:
        try:
            container.close()
        except Exception:
            pass


def iter_audio_slices(path: Path, embedder=None, *, source_kind: str = "audio",
                     **kw) -> Iterator[dict]:
    """音频窗口 → 语义切片 dict（每窗口一个 768 维向量）。

    Args:
        path: 音频文件路径（or 视频文件，会解音轨）
        embedder: 提供 embedder.embed_audio_pcm(pcm_int16, sr=16000) → 768d
        source_kind: "audio"（独立音频文件）或 "video_sound"（视频音轨），
                     仅影响 modality 字段
    """
    from pathlib import Path as _P
    stem = _P(path).stem
    for pcm, start_s, end_s in iter_audio_windows(path, **kw):
        slice_dict = {
            "modality": "audio" if source_kind == "audio" else "video_sound",
            "location": _format_ts(start_s),
            "text": "",
            "keywords": f"{stem} t={_format_ts(start_s)}-{_format_ts(end_s)}",
            "_pcm": pcm,
            "_sr": 16000,
            "_ts_start": start_s,
            "_ts_end": end_s,
        }
        if embedder is not None:
            try:
                slice_dict["vector"] = embedder.embed_audio_pcm(pcm, sr=16000)
            except Exception:
                pass
        yield slice_dict


# --------------------------------------------------------------------- #
# 分发
# --------------------------------------------------------------------- #
def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in config.PDF_EXTS:
        return "pdf"
    if ext in config.IMAGE_EXTS:
        return "image"
    if ext in config.AUDIO_EXTS:
        return "audio"
    if ext in config.VIDEO_EXTS:
        return "video"
    if ext in config.TEXT_EXTS or ext in config.DOC_EXTS:
        return "text"
    return "other"


def iter_slices(path: Path, token_counter=None) -> Iterator[dict]:
    """产出切片 dict：{modality, location, text, keywords}。

    text 进 FTS5 + 语义向量；keywords 预留给 OCR/标识符（PRD FR-4）。
    """
    kind = classify(path)
    if kind == "text":
        text = read_text(path)
        if text.strip():
            yield {"modality": "text", "location": "none", "text": text, "keywords": path.name}
    elif kind == "pdf":
        first = True
        for page_no, page_text in read_pdf_pages(path, max_pages_first=30 if first else None):
            first = False
            if page_text.strip():
                yield {"modality": "pdf", "location": f"page:{page_no}", "text": page_text, "keywords": path.name}
    elif kind == "video":
        # 视频帧：需调用方传入 embedder 才能产生带向量的切片（默认仅返回空）
        # 实际索引在 indexer.py 里走专属路径（直接读 iter_video_slices）
        return
    elif kind in ("image", "audio"):
        # 图片在 indexer 里走 vision encoder；音频编码器未就绪
        return
    # kind == "other"：跳过
