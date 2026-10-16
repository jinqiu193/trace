"""统一日志工厂：控制台 + 可选文件，替代散落各处的 print。

用法：
    from .log import get_logger
    log = get_logger("trace.indexer")
    log.info("…")

级别由环境变量 TRACE_LOG_LEVEL 控制（默认 INFO，边车里 WARNING 太吵的模块自行下调）。
TRACE_LOG_FILE=/path/to/log.txt 时追加文件 handler（带轮转）。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    root = logging.getLogger("trace")
    level = os.environ.get("TRACE_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    if not root.handlers:
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", "%H:%M:%S")
        con = logging.StreamHandler(sys.stderr)
        con.setFormatter(fmt)
        root.addHandler(con)
        path = os.environ.get("TRACE_LOG_FILE")
        if path:
            try:
                fh = RotatingFileHandler(path, maxBytes=2_000_000,
                                         backupCount=2, encoding="utf-8")
                fh.setFormatter(fmt)
                root.addHandler(fh)
            except OSError:
                pass


def get_logger(name: str) -> logging.Logger:
    _configure()
    if not name.startswith("trace"):
        name = f"trace.{name}"
    return logging.getLogger(name)