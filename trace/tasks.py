"""后台索引任务管理：单任务模型 + 进度快照 + 取消。

此前 /api/index 同步持锁直到整轮完成，期间 status/roots/搜索全部排队，
UI 呈现"应用挂了"。现在索引跑在专用线程，进度写入受锁保护的快照，
前端轮询 /api/index/status 渲染进度条并可取消。

锁策略依赖 Store 内置 RLock（方法粒度）：索引线程与搜索线程交错执行，
互不阻塞长事务。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from .log import get_logger

log = get_logger("trace.tasks")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class IndexTaskManager:
    """同时只允许一个索引任务（后续到排队拒绝）；状态快照线程安全。"""

    def __init__(self, state):
        """state: 提供 get_store() / get_embedder() 的容器（web.State）。"""
        self._state = state
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._status: dict = {
            "running": False, "kind": "idle", "root": "", "full": False,
            "by": "", "progress": None, "result": None, "error": None,
            "started_at": None, "finished_at": None,
        }

    # ------------------------------------------------------------------ #
    def is_busy(self) -> bool:
        with self._lock:
            return self._status["running"]

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def brief(self) -> dict:
        """/api/status 附带的概要（轻量）。"""
        with self._lock:
            s = self._status
            if not s["running"]:
                return {"running": False}
            p = s.get("progress") or {}
            return {
                "running": True, "kind": s["kind"], "root": s["root"], "by": s["by"],
                "scanned": p.get("scanned", 0), "total": p.get("total", 0),
                "indexed": p.get("indexed", 0), "errors": p.get("errors", 0),
            }

    def cancel(self) -> bool:
        with self._lock:
            if not self._status["running"]:
                return False
        self._cancel.set()
        return True

    # ------------------------------------------------------------------ #
    def start_root(self, root: str, full: bool = False, by: str = "user") -> bool:
        """整目录索引（后台线程）。已在跑 → 拒绝（返回 False）。"""
        return self._launch(kind="index_root", root=root, full=full, by=by,
                            paths=None, roots=None)

    def start_roots(self, roots: list[str], full: bool = False,
                    by: str = "user") -> bool:
        """多个 root 顺序索引（重建全库用）。已在跑 → 拒绝。"""
        roots = [r for r in roots if r]
        if not roots:
            return True
        return self._launch(kind="index_roots", root=roots[0], full=full, by=by,
                            paths=None, roots=roots)

    def start_paths(self, paths: list[str], root: str = "", by: str = "watcher") -> bool:
        """定向增量（watcher 变更路径）。"""
        if not paths:
            return True
        return self._launch(kind="index_paths", root=root, full=False, by=by,
                            paths=paths, roots=None)

    def _launch(self, kind: str, root: str, full: bool, by: str, paths,
                roots) -> bool:
        # 用户显式任务可抢占 watcher 自动任务（自动同步随时可重来，用户操作优先）。
        # 抢占 = 置 cancel 后锁外等线程退出（_run 收尾要拿锁，锁内 join 会死锁）。
        if by == "user":
            for _ in range(60):  # 60 × 0.5s，最多等 30s
                with self._lock:
                    if not self._status["running"]:
                        break
                    if self._status["by"] != "watcher":
                        return False  # 另一个用户任务在跑，不抢
                    self._cancel.set()
                t = self._thread
                if t is not None and t.is_alive():
                    t.join(timeout=0.5)
            else:
                log.warning("用户任务抢占 watcher 超时（30s 未退出），拒绝启动")
                return False
        with self._lock:
            if self._status["running"]:
                return False
            self._cancel = threading.Event()
            self._status = {
                "running": True, "kind": kind, "root": root, "full": full, "by": by,
                "progress": None, "result": None, "error": None,
                "started_at": _now(), "finished_at": None,
            }
            cancel = self._cancel
            status_ref = self._status

        def _progress(stats: dict) -> None:
            with self._lock:
                status_ref["progress"] = stats

        def _run() -> None:
            from .indexer import Indexer
            t0 = time.time()
            try:
                store = self._state.get_store()
                embedder = self._state.get_embedder()
                indexer = Indexer(store, embedder, verbose=False)
                if kind == "index_root":
                    result = indexer.index_root(root, full=full,
                                                progress=_progress, cancel=cancel)
                elif kind == "index_roots":
                    result = {"roots": 0, "scanned": 0, "indexed": 0,
                              "skipped": 0, "errors": 0, "dedup": 0,
                              "cancelled": False}
                    for r in (roots or []):
                        if cancel is not None and cancel.is_set():
                            result["cancelled"] = True
                            break
                        with self._lock:
                            status_ref["root"] = r
                        sub = indexer.index_root(r, full=full,
                                                 progress=_progress, cancel=cancel)
                        result["roots"] += 1
                        for k in ("scanned", "indexed", "skipped", "errors", "dedup"):
                            result[k] += sub.get(k, 0)
                        if sub.get("cancelled"):
                            result["cancelled"] = True
                            break
                else:
                    result = indexer.index_paths(paths, progress=_progress, cancel=cancel)
                result["seconds"] = round(time.time() - t0, 1)
                with self._lock:
                    status_ref["result"] = result
                    status_ref["progress"] = result
                log.info("后台索引完成 kind=%s root=%s by=%s：扫描 %d 索引 %d 去重 %d 跳过 %d 错误 %d 用时 %.1fs",
                         kind,
                         f"{len(roots)} roots" if roots else (root or f"{len(paths or [])} paths"),
                         by,
                         result.get("scanned", 0), result.get("indexed", 0),
                         result.get("dedup", 0), result.get("skipped", 0),
                         result.get("errors", 0),
                         result.get("seconds", 0.0))
            except Exception as e:
                log.exception("后台索引失败")
                with self._lock:
                    status_ref["error"] = f"{type(e).__name__}: {e}"
            finally:
                with self._lock:
                    status_ref["running"] = False
                    status_ref["finished_at"] = _now()

        self._thread = threading.Thread(target=_run, name="trace-index-task", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> bool:
        """等当前任务结束（测试/关停用）。"""
        t = self._thread
        if t is None:
            return True
        t.join(timeout=timeout)
        return not t.is_alive()