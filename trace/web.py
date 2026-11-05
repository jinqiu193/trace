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

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config
from .embedder import Embedder
from .search import name_search, search_with_timing
from .store import Store
from .tasks import IndexTaskManager

log = logging.getLogger("trace.web")


# --------------------------------------------------------------------- #
# 跨站调用防护
# --------------------------------------------------------------------- #
# 边车监听 127.0.0.1 且 CORS 放开为 "*"，浏览器里任意网页都能往
# 127.0.0.1:8765 发请求（写操作会触发预检，但预检同样被放行）。只读接口
# 无所谓；删索引、重命名文件这类破坏性接口必须挡住外部站点。
#
# 判据：浏览器发起的跨源请求一定带 Origin 头，且是那个网站的地址；
# 本地工具（curl / CLI）不带 Origin；Tauri WebView 带的是本地来源。
_LOCAL_ORIGINS = {
    "null",                       # 沙箱 iframe 等不透明来源
    "tauri://localhost",          # Tauri 2 macOS/Linux
    "http://tauri.localhost",     # Tauri 2 Windows
}


def _origin_allowed(origin: str) -> bool:
    o = origin.strip().lower()
    if not o or o in _LOCAL_ORIGINS:
        return True
    if o.startswith("http://127.0.0.1:") or o.startswith("http://localhost:"):
        return True
    if o in ("http://127.0.0.1", "http://localhost"):
        return True
    return False


def require_local_origin(request: Request) -> None:
    """破坏性接口的来源校验：非本地来源直接 403。"""
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        raise HTTPException(403, f"origin not allowed: {origin}")


def _start_parent_watchdog() -> None:
    """父进程退出 → 边车自杀。

    Tauri 主进程把子进程 stdin 接成管道并一直持有写端：主进程一旦退出
    （包括 std::process::exit(0) 这种不触发 RunEvent::Exit 的路径），写端
    被 OS 关闭 → 这里读到 EOF → 边车退出。否则会留下孤儿边车：两个边车
    同时对同一批 root 跑 watcher，重复索引、还会各自缓存一份向量矩阵
    （边车的内存缓存不会因别的进程写库而失效）。

    仅在 TRACE_PARENT_WATCHDOG=1 时启用（由 Tauri 主进程设置）；
    手动 `python webserver.py` 运行时 stdin 是终端，不启用。

    2026-11-05 修：**必须在应用完全导入之后再启动这个线程**。
    原先它在 webserver.py 的 main() 里、import uvicorn 之前就起，导致它
    阻塞读 stdin 的那一刻，主线程正在 import trace.web → numpy 的 C 扩展
    初始化（DLL 加载 + 导入锁）；两者互相拖死——实测主线程卡在
    `ExtensionFileLoader.create_module` 里出不来，进程只用了 1.1s CPU、
    占 50MB，端口 listen 了但 uvicorn 从未开始服务（所有 API 请求超时）。
    现在由 lifespan 在应用导入完成后调用。
    """
    import os
    import threading

    def _watch() -> None:
        try:
            while True:
                # os.read 是裸系统调用（释放 GIL），不走 TextIOWrapper
                data = os.read(0, 1)
                if not data:      # EOF：父进程已退出
                    os._exit(0)
        except Exception:
            pass

    threading.Thread(target=_watch, name="parent-watchdog", daemon=True).start()


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
    threshold: Optional[float] = None     # z-gate 门限（None=默认 2.0；0=宽松，z≥0 即召回）
    sort: str = "relevance"               # relevance/mtime_desc/mtime_asc/size_desc/name
    mtime_after: Optional[float] = None   # 修改时间下限（epoch 秒）
    mtime_before: Optional[float] = None  # 修改时间上限
    min_size: Optional[int] = None        # 文件大小下限（字节）
    max_size: Optional[int] = None        # 文件大小上限
    mode: str = "detailed"                # detailed（全切片+命中详情）/ fast（视频每文件 2 代表片段）


class SimilarBody(BaseModel):
    sid: int                              # 起始切片 id（More like this）
    limit: int = 20
    offset: int = 0
    file_type: Optional[str] = None
    dir: Optional[str] = None
    sort: str = "relevance"
    mode: str = "detailed"


class IndexBody(BaseModel):
    path: str = Field(..., min_length=1)
    full: bool = False


class RootBody(BaseModel):
    path: str = Field(..., min_length=1)


class SavedQueryBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    query: str = ""
    filters: dict = Field(default_factory=dict)


class RenameItem(BaseModel):
    path: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


class RenameBody(BaseModel):
    items: list[RenameItem] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = State()
    state.store = Store(check_same_thread=False)
    app.state.trace = state
    # 父进程看门狗：必须等应用（连同 numpy 的 C 扩展）完全导入之后再起，
    # 否则会和主线程的扩展初始化互相拖死（见 _start_parent_watchdog 注释）
    if os.environ.get("TRACE_PARENT_WATCHDOG") == "1":
        _start_parent_watchdog()
    watcher = None
    if config.WATCH_ENABLED:
        from .watcher import RootWatcher
        watcher = RootWatcher(state, state.tasks)
        watcher.start()

    def _warm() -> None:
        # 后台预热：Embedder 构造（冷进程 ~9s：模型磁盘冷读 + dll 初始化），
        # 否则全部压在用户首次搜索上。
        #
        # 2026-11-05 修：**这里绝不做重量级 C 扩展导入**（原来还有
        # `from scipy.signal import stft`）。后台线程导入 C 扩展时会持有 GIL
        # 并卡在原生等待里，而主线程此时要拿 GIL 去启动 anyio 的工作线程
        # （anyio.run_sync_in_worker_thread → Thread.start）→ 永久死锁：
        # 现象是端口 listen 了但 uvicorn 永不开始服务，所有 API 请求超时，
        # py-spy 看到主线程停在 Thread.start、预热线程停在
        # ExtensionFileLoader.create_module。
        # 现在：C 扩展一律在主线程导入（见 embedder.py 的模块级 import），
        # 后台线程只做模型加载（不再涉及 import）。
        try:
            state.get_embedder()
        except Exception:
            pass

    threading.Thread(target=_warm, name="embedder-warmup", daemon=True).start()
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
    q = body.query
    # name: 前缀 → 纯文件名子串搜索（Everything 风格精确模式，不走向量）
    if q.lower().startswith("name:"):
        return name_search(s.store, q[5:].strip(), limit=body.limit,
                           offset=max(0, body.offset), file_type=body.file_type,
                           dir_filter=body.dir, sort=body.sort,
                           mtime_after=body.mtime_after, mtime_before=body.mtime_before,
                           min_size=body.min_size, max_size=body.max_size)
    # code: 前缀 → 代码专搜：code retrieval 任务前缀向量 + 限定代码类型
    # （用户显式选了类型过滤时尊重用户选择）
    if q.lower().startswith("code:"):
        embedder = s.get_embedder()
        return search_with_timing(s.store, embedder, q[5:].strip(),
                                  limit=body.limit,
                                  offset=max(0, body.offset),
                                  file_type=body.file_type or "code",
                                  dir_filter=body.dir,
                                  z_gate=body.threshold,
                                  sort=body.sort,
                                  mtime_after=body.mtime_after,
                                  mtime_before=body.mtime_before,
                                  min_size=body.min_size,
                                  max_size=body.max_size,
                                  mode=body.mode,
                                  with_facets=True,
                                  code=True)
    embedder = s.get_embedder()
    return search_with_timing(s.store, embedder, q,
                              limit=body.limit,
                              offset=max(0, body.offset),
                              file_type=body.file_type,
                              dir_filter=body.dir,
                              z_gate=body.threshold,
                              sort=body.sort,
                              mtime_after=body.mtime_after,
                              mtime_before=body.mtime_before,
                              min_size=body.min_size,
                              max_size=body.max_size,
                              mode=body.mode,
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
    mode: str = Form("detailed"),
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
                        text=text or "", mode=mode)
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
                      sort=body.sort, with_facets=True, mode=body.mode)
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
def index(body: IndexBody, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {body.path}")
    # 先起任务再登记：409（已有任务在跑）时不登记目录，避免"报错却已入库"。
    # 首索中断仍保留范围（start 成功即已 add，watcher 可续）。
    if not s.tasks.start_root(body.path, full=body.full, by="user"):
        raise HTTPException(409, "an index task is already running")
    s.store.add_root(body.path)
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
def add_root(body: RootBody, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    p = Path(body.path)
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {body.path}")
    s.store.add_root(body.path)
    return {"ok": True, "path": body.path}


@app.delete("/api/roots/{path:path}")
def remove_root(path: str, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    ok = s.store.remove_root(path)
    return {"ok": ok, "path": path}


@app.delete("/api/files")
def delete_file(path: str, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    ok = s.store.delete_file(path)
    return {"ok": ok, "path": path}


# --------------------------------------------------------------------- #
# 批量重命名：把选中的结果改名，并同步更新索引
# --------------------------------------------------------------------- #
_BAD_NAME_CHARS = set('/\\:*?"<>|')


@app.post("/api/files/rename")
def rename_files(body: RenameBody, request: Request) -> dict:
    """批量改名。就地改名（不移动目录）。

    逐项返回结果，失败不影响其他项；改名后立即同步索引路径，
    搜索当场就能命中新文件名。
    """
    require_local_origin(request)
    s = _state()
    results: list[dict] = []
    for item in body.items:
        src = Path(item.path)
        name = (item.new_name or "").strip()
        entry = {"path": item.path, "new_name": name}
        try:
            if not name or name in (".", ".."):
                raise ValueError("文件名不能为空")
            if any(ch in _BAD_NAME_CHARS for ch in name):
                raise ValueError("文件名不能包含路径分隔符或 \\/:*?\"<>| ")
            if not src.exists():
                raise FileNotFoundError(f"文件不存在：{src}")
            dst = src.with_name(name)
            if dst == src:
                entry.update(ok=True, skipped=True)
                results.append(entry)
                continue
            if dst.exists():
                raise FileExistsError(f"目标已存在：{dst.name}")
            src.rename(dst)
            s.store.rename_path(str(src), str(dst))
            entry.update(ok=True, new_path=str(dst))
        except Exception as e:
            entry.update(ok=False, error=str(e))
            log.warning("改名失败 %s -> %s: %s", item.path, name, e)
        results.append(entry)
    return {"ok": all(r.get("ok") for r in results) if results else True, "results": results}


# --------------------------------------------------------------------- #
# 保存的查询（智能文件夹）
# --------------------------------------------------------------------- #
@app.get("/api/saved-queries")
def list_saved_queries() -> dict:
    s = _state()
    return {"items": s.store.list_saved_queries()}


@app.post("/api/saved-queries")
def save_saved_query(body: SavedQueryBody, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    try:
        item = s.store.save_saved_query(body.name, body.query, body.filters)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "item": item}


@app.delete("/api/saved-queries/{qid}")
def delete_saved_query(qid: int, request: Request) -> dict:
    require_local_origin(request)
    s = _state()
    return {"ok": s.store.delete_saved_query(qid)}


@app.post("/api/reindex")
def reindex_all(request: Request, full: bool = False) -> dict:
    require_local_origin(request)
    s = _state()
    roots = s.store.list_roots()
    if not roots:
        return {"results": []}
    # 所有 root 在一个后台任务里顺序跑完（cancel 可中断，进度按当前 root 展示）。
    # 默认增量（指纹跳过已索引文件，秒级）；full=True 才全量重嵌（慢，慎用）
    if not s.tasks.start_roots(roots, full=full, by="user"):
        raise HTTPException(409, "an index task is already running")
    return {"ok": True, "queued": len(roots), "roots": roots, "full": full,
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
# 视频流：ffmpeg 从指定时间点剪切后流式发给 WebView2
# - H.264 源：直接 remux（-c:v copy），毫秒级首帧、零转码开销
# - 其他编码：libx264 veryfast 转码（比 VP9 软编快数倍，兼容性最好）
# 输出 fragmented MP4：边转边发，<video> 无需下载完即可起播
# --------------------------------------------------------------------- #
def _find_ffmpeg() -> str:
    """定位 ffmpeg 可执行文件。优先级：PATH > 项目 bundled > imageio-ffmpeg 包。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    cand = Path(__file__).parent.parent / "tools" / "ffmpeg.exe"
    if cand.exists():
        return str(cand)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise HTTPException(500, "ffmpeg not found; install ffmpeg / imageio-ffmpeg, or place ffmpeg.exe in PATH")


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


@app.get("/api/file")
def file_stream(path: str) -> "FileResponse":
    """通用文件流式输出（音频/PDF 内嵌预览用）。

    FileResponse 自带 Range/206 支持——HTML5 <audio> 拖动进度条必需。
    仅本地侧车 + WebView2 使用，路径可见性与 /api/raw 一致。
    """
    from fastapi.responses import FileResponse

    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"file missing: {path}")
    return FileResponse(p, filename=p.name, headers={
        "Cache-Control": "no-store",
        "Accept-Ranges": "bytes",
    })


_MAX_TEXT_BYTES = 4 * 1024 * 1024  # 4MB 文本上限，防超大日志拖垮 WebView


@app.get("/api/text")
def text_content(path: str) -> dict:
    """文本文件内容（内嵌预览用）：编码探测 + 超限截断。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"file missing: {path}")
    raw = p.read_bytes()[:_MAX_TEXT_BYTES]
    enc = "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")  # Windows 中文环境最常见回退
            enc = "gb18030"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    return {
        "text": text,
        "encoding": enc,
        "truncated": p.stat().st_size > _MAX_TEXT_BYTES,
        "size": p.stat().st_size,
    }


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


def _video_codec_fourcc(path: Path) -> str:
    """cv2 快速读视频轨 fourcc（avc1/h264…）。打不开返回空串（走转码分支）。"""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return ""
    try:
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        return "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00").lower()
    finally:
        cap.release()


# H.264 的 fourcc 变体（MP4 里是 avc1/avc3；mkv/avi 里常见 h264/x264）
_H264_FOURCCS = {"avc1", "avc3", "h264", "x264", "davc"}


@app.get("/api/video/stream")
def video_stream(path: str, t: float = 0.0, dur: float = 60.0) -> StreamingResponse:
    """从指定时间点起剪切 dur 秒，fragmented MP4 边发边播。

    H.264 源直接 remux（CPU 几乎为零、首帧毫秒级）；其他编码（HEVC/VP9/AV1…）
    用 libx264 veryfast 转码。音频统一转 AAC（MP4 容器兼容性最好）。
    """
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"file missing: {path}")
    if not (0 <= t <= 24 * 3600 and 1 <= dur <= 600):
        raise HTTPException(400, f"bad t/dur: t={t} dur={dur}")

    ffmpeg = _find_ffmpeg()
    is_h264 = _video_codec_fourcc(p) in _H264_FOURCCS
    # -ss 在 -i 之前：快速定位（remux 时对齐到最近关键帧，误差 ≤ 一个 GOP）
    if is_h264:
        vcodec = ["-c:v", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                  "-pix_fmt", "yuv420p"]
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, t):.3f}",
        "-i", str(p),
        "-t", f"{dur:.3f}",
        *vcodec,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "-",
    ]
    log.info("video_stream %s remux=%s", p.name, is_h264)
    try:
        # GUI 进程树内无控制台可继承，ffmpeg 作为 console 程序会弹新窗口——必须抑制
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = 0x0800_0000  # CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=_STREAM_CHUNK * 2, **popen_kwargs)
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
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store",
            "Accept-Ranges": "none",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("trace.web:app", host="127.0.0.1", port=8765, log_level="warning")