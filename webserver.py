"""边车入口 — 启动 FastAPI，并把行端口打印到 stdout 给 Tauri 主进程解析。

调用：
    python webserver.py            # 默认端口 8765
    python webserver.py --port 0   # 随机端口（边车用），端口会打到 stdout 行格式 `PORT=<n>`
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _bind_listener(host: str, preferred: int) -> socket.socket:
    """绑定并 listen 端口，返回已监听的 socket（端口号从 getsockname 取）。

    2026-11-05 修：原先是"探测空闲端口 → print(PORT=) → server.run()"，
    而 uvicorn 要等 trace.web 模块 import 完（numpy + fastapi，1-2s）才真正
    bind。Rust 侧读到 PORT= 就开 WebView，前端首个请求撞上 connection
    refused，被 .catch(() => {}) 吞掉 → "全部位置"下拉整个会话为空。

    现在先 bind + listen 再上报端口：此后的连接会进内核 accept 队列，
    应用就绪后立刻被处理，不会再 connection refused。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if preferred and preferred > 0:
        try:
            sock.bind((host, preferred))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    sock.listen(128)
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace FastAPI 边车入口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-port-print", action="store_true",
                        help="不向 stdout 打印 PORT= 行（仅 CLI/Tauri 时去掉）")
    args = parser.parse_args()

    # 注意：父进程看门狗不在这里启动——必须等应用（连同 numpy 的 C 扩展）
    # 完全导入之后再起，否则会跟主线程的扩展初始化互相拖死（见
    # _start_parent_watchdog 的注释）。实际启动点在 trace/web.py 的 lifespan。
    import uvicorn

    # 先把端口占住并 listen，再上报 PORT=（见 _bind_listener 注释）
    sock = _bind_listener(args.host, args.port)
    port = sock.getsockname()[1]

    config = uvicorn.Config("trace.web:app", host=args.host, port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    if not args.no_port_print:
        print(f"PORT={port}", flush=True)
        print(f"READY=1 host={args.host} port={port}", flush=True)

    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())