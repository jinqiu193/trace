"""边车入口 — 启动 FastAPI，并把行端口打印到 stdout 给 Tauri 主进程解析。

调用：
    python webserver.py            # 默认端口 8765
    python webserver.py --port 0   # 随机端口（边车用），端口会打到 stdout 行格式 `PORT=<n>`
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _free_port(preferred: int) -> int:
    """如果 preferred 为 0 或被占用，则返回系统空闲端口。"""
    if preferred and preferred > 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace FastAPI 边车入口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-port-print", action="store_true",
                        help="不向 stdout 打印 PORT= 行（仅 CLI/Tauri 时去掉）")
    args = parser.parse_args()

    import uvicorn

    port = _free_port(args.port)

    config = uvicorn.Config("trace.web:app", host=args.host, port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    if not args.no_port_print:
        print(f"PORT={port}", flush=True)
        print(f"READY=1 host={args.host} port={port}", flush=True)

    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())