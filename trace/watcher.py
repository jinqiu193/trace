"""文件变更监听（轮询式，零外部依赖）。

DigUp 原版用 FSEvents 事件驱动；Windows 侧等价物是 watchdog（Python）或
notify（Rust）。为避免给边车加重依赖、且轮询天然自带防抖（变更后等
下个周期，写大文件的中间状态不会触发），这里用 mtime+size 快照对比：

  DB 快照 (path → size,mtime)  vs  磁盘 stat

- 少量变更（≤ WATCH_RESCAN_THRESHOLD）→ 定向 index_paths（不整目录遍历）
- 大批变更（git checkout / 解压）→ 整目录增量 index_root
- 消失的文件 → 直接删索引

间隔 WATCH_INTERVAL_SEC（默认 60s，TRACE_WATCH_INTERVAL 可调），
TRACE_WATCH=0 关闭。
"""

from __future__ import annotations

import threading

from . import config
from .log import get_logger

log = get_logger("trace.watcher")


def _scan_disk(root: str) -> dict[str, tuple[int, float]]:
    """磁盘侧 {path: (size, mtime)}。目录遍历规则与 indexer 一致。"""
    from .indexer import _iter_files
    out: dict[str, tuple[int, float]] = {}
    try:
        for p in _iter_files(__import__("pathlib").Path(root)):
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p)] = (st.st_size, st.st_mtime)
    except (PermissionError, OSError) as e:
        log.warning("扫描 %s 失败：%s", root, e)
    return out


class RootWatcher(threading.Thread):
    """daemon 线程：周期性比对 roots 的磁盘/索引快照，触发增量任务。"""

    def __init__(self, state, tasks, interval: float | None = None):
        super().__init__(name="trace-watcher", daemon=True)
        self._state = state
        self._tasks = tasks
        self._interval = interval if interval is not None else config.WATCH_INTERVAL_SEC
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("文件监听已启动（轮询 %.0fs）", self._interval)
        # 启动后先等一个周期：避免和首启全量索引抢锁
        while not self._stop.wait(self._interval):
            if self._tasks.is_busy():
                continue
            try:
                self._poll_once()
            except Exception:
                log.exception("watcher 轮询异常")
        log.info("文件监听已停止")

    def _poll_once(self) -> None:
        store = self._state.get_store()
        for root in store.list_roots():
            db_state = store.file_states(root)
            if not db_state:
                continue  # 该 root 尚未索引过，交给用户手动首索
            disk = _scan_disk(root)
            changed = [p for p, sv in disk.items() if db_state.get(p) != sv]
            deleted = [p for p in db_state if p not in disk]
            if not changed and not deleted:
                continue
            if deleted:
                n = store.delete_files(deleted)
                log.info("watcher：清理 %d 个已消失文件（%s）", n, root)
            if not changed:
                continue
            if len(changed) > config.WATCH_RESCAN_THRESHOLD:
                log.info("watcher：%d 个文件变更（%s），走整目录增量", len(changed), root)
                self._tasks.start_root(root, full=False, by="watcher")
            else:
                log.info("watcher：%d 个文件变更（%s），定向增量：%s …",
                         len(changed), root, changed[0])
                self._tasks.start_paths(changed, root=root, by="watcher")
            return  # 一次只触发一个任务，其余下个周期处理