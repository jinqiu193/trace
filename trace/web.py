"""FastAPI 边车层 — 给 Tauri 桌面端（以及任何 HTTP 客户端）暴露 trace 的全部能力。

CLI（run.py）原样保留；这只是新加的 HTTP 壳，复用 Store / Embedder / Indexer / search 等。

2026-10-16 工程化改造：
- /api/index 异步化：后台线程跑（IndexTaskManager），立即返回；此前同步
  持锁到整轮结束，期间 status/搜索全部排队，UI 呈现"挂了"
- 新增 /api/index/status（进度快照）与 /api/index/cancel
- 移除全局 _STORE_LOCK：Store 自带方法级 RLock，索引与搜索交错执行
- 启动 RootWatcher（轮询文件变更 → 定向增量，TRACE_WATCH=0 关闭）
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config
from .embedder import Embedder
from .search import search_with_timing
from .store import Store
from .tasks import IndexTaskManager

log = logging.getLogger("trace.web")


class State:
    """lifespan 期间单例：Store + 按需加载的 Embedder + 后台任务。"""

    def __init__(self) -> None:
        self.store: Optional[Store] = None
        self.embedder: Optional[Embedder] = None
        self._embedder_lock = threading.Lock()
        self.tasks = IndexTaskManager(self)

    def get_store(self) -> Store:
        if self.store is None:
            raise HTTPException(503, "store not ready")
        return self.store

    def get_embedder(self) -> Embedder:
        if self.embedder is None:
            with self._embedder_lock:
                if self.embedder is None:
                    self.embedder = Embedder()
        return self.embedder


class SearchBody(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = 20
    offset: int = 0                       # 分页偏移（2026-10-16）
    file_type: Optional[str] = None
    dir: Optional[str] = None
    threshold: Optional[float] = None     # z-gate 门限（0=用默认 2.0）
    sort: str = "relevance"               # relevance/mtime_desc/mtime_asc/size_desc/name


class SimilarBody(BaseModel):
    sid: int                              # 起始切片 id（More like this）
    limit: int = 20
    offset: int = 0
    file_type: Optional[str] = None
    dir: Optional[str] = None
    sort: str = "relevance"


class IndexBody(BaseModel):
    path: str = Field(..., min_length=1)
    full: bool = False


class RootBody(BaseModel):
    path: str = Field(..., min_length=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = State()
    state.store = Store(check_same_thread=False)
    app.state.trace = state
    watcher = None
    if config.WATCH_ENABLED:
        from .watcher import RootWatcher
        watcher = RootWatcher(state, state.tasks)
        watcher.start()
    try:
        yield
    finally:
        if watcher is not None:
            watcher.stop()
        state.tasks.wait(timeout=10)
        if state.store is not None:
            state.store.close()


app = FastAPI(title="Trace Sidecar", version="0.2.0", lifespan=lifespan)

# Tauri WebView 通过 tauri:// / http://localhost 拉取；开放 CORS 仅本地，安全边界由 WebView2 兜底
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _state() -> State:
    s: State = app.state.trace
    if s.store is None:
        raise HTTPException(503, "store not ready")
    return s


@app.exception_handler(Exception)
async def _on_error(_req, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": str(exc), "type": type(exc).__name__})


@app.get("/api/status")
def status() -> dict:
    s = _state()
    stats = s.store.stats()
    roots = s.store.list_roots()
    embedder_info = {}
    try:
        embedder_info = s.get_embedder().info()
    except Exception as e:
        embedder_info = {"error": str(e)}
    return {
        "stats": stats,
        "roots": roots,
        "embedder": embedder_info,
        "fingerprint": config.FINGERPRINT,
        "db_path": str(config.DB_PATH),
        "task": s.tasks.brief(),       # 索引任务概要（进度条用）
        "watch": {"enabled": config.WATCH_ENABLED,
                  "interval_sec": config.WATCH_INTERVAL_SEC},
    }


@app.get("/api/doctor")
def doctor() -> dict:
    s = _state()
    out = {"checks": []}
    for label, path in [("text_model", config.MODEL_ONNX), ("tokenizer", config.TOKENIZER_JSON),
                        ("vision_model", config.VISION_ONNX)]:
        exists = path.exists()
        size = round(path.stat().st_size / 1e6, 1) if exists else 0
        out["checks"].append({
            "name": label,
            "path": str(path),
            "exists": exists,
            "size_mb": size,
        })
    try:
        out["embedder"] = s.get_embedder().info()
    except Exception as e:
        out["embedder"] = {"error": str(e)}
    return out


@app.post("/api/search")
def search(body: SearchBody) -> dict:
    s = _state()
    embedder = s.get_embedder()
    return search_with_timing(s.store, embedder, body.query,
                              limit=body.limit,
                              offset=max(0, body.offset),
                              file_type=body.file_type,
                              dir_filter=body.dir,
                              z_gate=body.threshold,
                              sort=body.sort,
                              with_facets=True)


_MAX_UPLOAD_MB = 30


@app.post("/api/search/image")
async def search_image(
    file: UploadFile = File(...),
    limit: int = Form(20),
    offset: int = Form(0),
    file_type: Optional[str] = Form(None),
    dir: Optional[str] = Form(None),
    sort: str = Form("relevance"),
    text: Optional[str] = Form(None),
) -> dict:
    """以图搜图：multipart 上传图片 → 视觉嵌入 → 相似切片检索。

    text 非空时为图+文联合查询（"像这张图但要…"，BOI 尾接文本）。
    """
    import io
    import time as _time

    from PIL import Image, ImageOps

    from .search import search_by_image

    s = _state()
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"image too large (> {_MAX_UPLOAD_MB}MB)")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise HTTPException(400, f"cannot decode image: {e}")

    embedder = s.get_embedder()
    if getattr(embedder, "vision_session", None) is None:
        raise HTTPException(503, "vision encoder not available (model missing)")

    t0 = _time.time()
    r = search_by_image(s.store, embedder, img,
                        limit=limit, offset=max(0, offset),
                        file_type=file_type, dir_filter=dir,
                        sort=sort, with_facets=True,
                        text=text or "")
    results, facets = r
    label = f"(以图搜图{f' · {text.strip()}' if text and text.strip() else ''})"
    return {"query": label, "ms": round((_time.time() - t0) * 1000, 1),
            "results": results, "facets": facets, "mode": "image"}


@app.post("/api/search/similar")
def search_similar(body: SimilarBody) -> dict:
    """More like this：以某切片向量为查询，找最相似的文件（全模态）。"""
    import time as _time

    from .search import search_by_sid

    s = _state()
    t0 = _time.time()
    r = search_by_sid(s.store, s.get_embedder(), body.sid,
                      limit=body.limit, offset=max(0, body.offset),
                      file_type=body.file_type, dir_filter=body.dir,
                      sort=body.sort, with_facets=True)
    results, facets = r
    return {"query": f"(相似 #{body.sid})", "ms": round((_time.time() - t0) * 1000, 1),
            "results": results, "facets": facets, "mode": "similar"}


@app.get("/api/duplicates")
def duplicates(threshold: float = 0.95) -> dict:
    """近似重复图片检测报告：image 切片 cos ≥ threshold 聚簇。"""
    import time as _time

    from .search import find_duplicates

    s = _state()
    threshold = min(max(threshold, 0.5), 0.9999)
    t0 = _time.time()
    clusters = find_duplicates(s.store, threshold=threshold)
    n_files = sum(len(c["members"]) for c in clusters)
    return {"threshold": threshold, "ms": round((_time.time() - t0) * 1000, 1),
            "clusters": clusters, "cluster_count": len(clusters),
            "file_count": n_files}


# --------------------------------------------------------------------- #
# 索引：后台任务（异步） + 进度 + 取消
# --------------------------------------------------------------------- #
@app.post("/api/index")
def index(body: IndexBody) -> dict:
    s = _state()
    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {body.path}")
    s.store.add_root(body.path)   # 立即登记（首索中断也保留范围，watcher 可续）
    if not s.tasks.start_root(body.path, full=body.full, by="user"):
        raise HTTPException(409, "an index task is already running")
    return {"ok": True, "path": body.path, "task": s.tasks.brief()}


@app.get("/api/index/status")
def index_status() -> dict:
    return _state().tasks.status()


@app.post("/api/index/cancel")
def index_cancel() -> dict:
    s = _state()
    ok = s.tasks.cancel()
    return {"ok": ok}


@app.get("/api/roots")
def list_roots() -> dict:
    s = _state()
    return {"roots": s.store.list_roots()}


@app.post("/api/roots")
def add_root(body: RootBody) -> dict:
    s = _state()
    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {body.path}")
    s.store.add_root(body.path)
    return {"ok": True, "path": body.path}


@app.delete("/api/roots/{path:path}")
def remove_root(path: str) -> dict:
    s = _state()
    ok = s.store.remove_root(path)
    return {"ok": ok, "path": path}


@app.delete("/api/files")
def delete_file(path: str) -> dict:
    s = _state()
    ok = s.store.delete_file(path)
    return {"ok": ok, "path": path}


@app.post("/api/reindex")
def reindex_all() -> dict:
    s = _state()
    roots = s.store.list_roots()
    if not roots:
        return {"results": []}
    # 逐个排队跑：start_root 单任务模型，这里直接起第一个并告知总量
    started = s.tasks.start_root(roots[0], full=True, by="user")
    if not started:
        raise HTTPException(409, "an index task is already running")
    return {"ok": True, "queued": len(roots), "first": roots[0],
            "task": s.tasks.brief()}


@app.post("/api/open")
def open_file(path: str, page: Optional[int] = None) -> dict:
    """精准定位打开（FR-5 简化版）。"""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"file missing: {path}")
    if page and p.suffix.lower() == ".pdf":
        # Edge / Chrome 系 PDF 阅读器支持 #page=N
        return {"ok": True, "url": f"file:///{path.replace(chr(92), '/')}#page={page}"}
    return {"ok": True, "url": f"file:///{path.replace(chr(92), '/')}"}


# --------------------------------------------------------------------- #
# 视频流：ffmpeg 从指定时间点剪切 + 转 WebM(VP9) 边转边发给 WebView2
# WebView2 原生 <video> 对 WebM/VP9 100% 支持，绕过 H.264 解码限制
# --------------------------------------------------------------------- #
def _find_ffmpeg() -> str:
    """定位 ffmpeg 可执行文件。优先级：PATH > 项目 bundled。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    cand = Path(__file__).parent.parent / "tools" / "ffmpeg.exe"
    if cand.exists():
        return str(cand)
    raise HTTPException(500, "ffmpeg not found; install ffmpeg or place ffmpeg.exe in PATH")


@app.get("/api/video/info")
def video_info(path: str) -> dict:
    """返回视频 fps / duration / 编码（前端判断是否需要走帧回退）。"""
    import cv2
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"file missing: {path}")
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise HTTPException(400, f"cannot open: {path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4))
        return {
            "fps": fps,
            "frames": n,
            "duration_s": (n / fps) if fps else 0.0,
            "codec": codec.strip("\x00") or "?",
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        }
    finally:
        cap.release()


@app.get("/api/raw")
def raw_file(path: str, max_w: int = 0) -> "StreamingResponse":
    """图片内嵌预览用：原图（可选缩到 max_w 以内加速大图渲染）。"""
    from fastapi.responses import Response
    from PIL import Image

    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"file missing: {path}")
    ext = p.suffix.lower()
    if ext not in config.IMAGE_EXTS:
        raise HTTPException(400, f"not an image extension: {ext}")
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".bmp": "image/bmp", ".gif": "image/gif",
        ".heic": "image/heic", ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(ext, "application/octet-stream")

    if max_w <= 0:
        # 原图直出：交给 <img> 自己处理（WebView2 内存里解码）
        data = p.read_bytes()
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "public, max-age=600"})

    # 受控缩放：超大原图先 PIL downscale，再 jpg 出去。保证 DPR 高的屏不卡。
    try:
        with Image.open(p) as img:
            img.load()
            w, h = img.size
            if w > max_w:
                ratio = max_w / w
                img = img.resize((max_w, int(h * ratio)), Image.LANCZOS)
            if ext in (".jpg", ".jpeg") and img.mode == "RGB":
                # jpg 原图就 jpg 出去，省一次重编码
                import io
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85, optimize=True)
                data = buf.getvalue()
            else:
                import io
                buf = io.BytesIO()
                target = "JPEG" if ext in (".jpg", ".jpeg") else (
                    "PNG" if ext == ".png" else "WEBP" if ext == ".webp" else "PNG"
                )
                if img.mode not in ("RGB", "RGBA", "L"):
                    img = img.convert("RGB")
                img.save(buf, format=target, quality=85)
                data = buf.getvalue()
                mime = "image/jpeg" if target == "JPEG" else (
                    "image/png" if target == "PNG" else "image/webp"
                )
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "public, max-age=600"})
    except Exception as e:
        # 解码失败 → 退回原文件
        log.warning("raw_file decode failed for %s: %s; fallback to bytes", path, e)
        return Response(content=p.read_bytes(), media_type=mime,
                        headers={"Cache-Control": "public, max-age=600"})


@app.get("/api/video/frame")
def video_frame(path: str, t: float = 0.0, max_w: int = 640) -> "StreamingResponse":
    """抽指定时间的一帧，返回 jpg。供 WebView2 视频解码失败时回退用。"""
    import cv2
    from fastapi.responses import Response
    from PIL import Image

    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"file missing: {path}")
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        raise HTTPException(400, f"cannot open: {path}")
    try:

        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise HTTPException(404, "no frame at requested time")
        # 缩到 max_w 以内（jpg 体积小、WebView 渲染快）
        h, w = frame.shape[:2]
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, (max_w, int(h * scale)), interpolation=cv2.INTER_AREA)
        # BGR → RGB，再 PIL jpg
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=78)
        return Response(content=buf.getvalue(), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=600"})
    finally:
        cap.release()


# 视频转码流的块大小：64 KB（足够小以让前端快速首帧，足够大以减少系统调用）
_STREAM_CHUNK = 64 * 1024


@app.get("/api/video/stream")
def video_stream(path: str, t: float = 0.0, dur: float = 60.0) -> StreamingResponse:
    """从指定时间点开始剪切 + 转 WebM/VP9 边转边发，WebView2 <video> 直接 src 就能播。"""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"file missing: {path}")
    if not (0 <= t <= 24 * 3600 and 1 <= dur <= 600):
        raise HTTPException(400, f"bad t/dur: t={t} dur={dur}")

    ffmpeg = _find_ffmpeg()
    # -ss 在 -i 之前：快速定位到最近关键帧（牺牲 ±1s 精度，换秒开）；-t 截取长度
    # VP9 软编码在 720p 实时 ~25fps，单核负载 ~70%，不会卡其他搜索
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, t):.3f}",
        "-i", str(p),
        "-t", f"{dur:.3f}",
        "-c:v", "libvpx-vp9", "-b:v", "800k", "-deadline", "realtime", "-cpu-used", "5",
        "-c:a", "libopus", "-b:a", "64k",
        "-f", "webm", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=_STREAM_CHUNK * 2)
    except FileNotFoundError:
        raise HTTPException(500, "ffmpeg not executable")

    def iter_chunks():
        try:
            while True:
                chunk = proc.stdout.read(_STREAM_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            # 客户端断开/异常时确保 ffmpeg 也被杀掉
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)

    return StreamingResponse(
        iter_chunks(),
        media_type="video/webm",
        headers={
            "Cache-Control": "no-store",
            "Accept-Ranges": "none",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("trace.web:app", host="127.0.0.1", port=8765, log_level="warning")