"""命令行入口：trace index / search / status / dirs / forget / evals / doctor。

用法（在 E:/搜索embading/trace 下）：
    python run.py doctor                      # 环境自检（模型/ORT/数据库）
    python run.py index <目录> [<目录>...]     # 索引文件夹（可多个）
    python run.py index --full                # 忽略增量，全量重建
    python run.py search "讲睡眠的那段播客"    # 混合检索
    python run.py search "invoice 1024" --type pdf --limit 5
    python run.py status                      # 索引统计
    python run.py dirs / forget <目录>        # 管理索引范围
    python run.py evals                       # 跑内置迷你评测集
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config
from .embedder import Embedder
from .indexer import Indexer
from .search import search_with_timing
from .store import Store


def _print_result(i: int, r: dict) -> None:
    tag = ""
    if r.get("weak_fallback"):
        tag += "  （语义无强匹配，兜底）"
    elif not r.get("strong", True):
        tag += "  （弱匹配）"
    loc = f"  @{r['location']}" if r.get("location") else ""
    print(f"{i:>2}. [{r['score']:.3f}] z={r['z']:+.2f} cos={r['cos_sim']:.3f} cov={r['coverage']:.1f}"
          f"  {r['path']}{loc}{tag}")
    if r["snippet"]:
        print(f"      └ {r['snippet']}")


def cmd_doctor(store, embedder, args) -> int:
    print("== Trace 环境自检 ==")
    ok = True
    for label, path in [("文本模型(int8)", config.MODEL_ONNX), ("tokenizer", config.TOKENIZER_JSON)]:
        exists = path.exists()
        size = f"{path.stat().st_size/1e6:.1f} MB" if exists else "-"
        print(f"  [{'OK' if exists else '缺失'}] {label}: {path} ({size})")
        ok = ok and exists
    if ok:
        info = embedder.info()
        print(f"  [OK] ONNX Runtime: provider={info['provider']}")
        print(f"       inputs={info['inputs']}  outputs={info['outputs']}")
    else:
        print("  [!!] 模型文件缺失，请先按 README 下载")
        return 1
    print(f"  [OK] 数据库: {config.DB_PATH}")
    return 0


def cmd_index(store, embedder, args) -> int:
    dirs = args.dirs or store.list_roots()
    if not dirs:
        print("没有指定目录，且尚未设置任何索引范围。用法：python run.py index <目录>")
        return 1
    indexer = Indexer(store, embedder, verbose=True)
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            print(f"[跳过] 不是目录：{d}")
            continue
        print(f"== 索引 {p} {'(full)' if args.full else '(增量)'} ==")
        t0 = time.time()
        stats = indexer.index_root(str(p), full=args.full)
        print(f"   完成：扫描 {stats['scanned']}｜新索引 {stats['indexed']}｜未变跳过 {stats['skipped']}"
              f"｜仅元数据 {stats['metadata_only']}｜清理 {stats['removed']}｜错误 {stats['errors']}"
              f"｜耗时 {stats['seconds']}s")
    return 0


def cmd_search(store, embedder, args) -> int:
    res = search_with_timing(store, embedder, args.query, limit=args.limit,
                             file_type=args.type, dir_filter=args.dir)
    if not res["results"]:
        print(f"没有命中（{res['ms']}ms）。可先 python run.py index <目录>，或 python run.py status 查看索引量。")
        return 0
    print(f"查询「{args.query}」→ {len(res['results'])} 条结果（{res['ms']}ms）")
    for i, r in enumerate(res["results"], 1):
        _print_result(i, r)
    return 0


def cmd_status(store, embedder, args) -> int:
    s = store.stats()
    print("== 索引状态 ==")
    for k, v in s.items():
        print(f"  {k:<22} {v}")
    roots = store.list_roots()
    if roots:
        print("  索引范围：")
        for r in roots:
            print(f"    - {r}")
    return 0


def cmd_dirs(store, embedder, args) -> int:
    roots = store.list_roots()
    if not roots:
        print("（空）用 python run.py index <目录> 添加")
    for r in roots:
        print(f"  {r}")
    return 0


def cmd_forget(store, embedder, args) -> int:
    ok = store.remove_root(args.dir)
    print(f"已移除索引范围：{args.dir}" if ok else f"不在索引范围中：{args.dir}")
    return 0


def cmd_evals(store, embedder, args) -> int:
    from .evals import run_evals

    return run_evals(store, embedder)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trace", description="Trace 本地文件夹语义检索（P0 CLI）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("index", help="索引目录")
    p.add_argument("dirs", nargs="*", help="要索引的目录（缺省用已记录的索引范围）")
    p.add_argument("--full", action="store_true", help="忽略增量全量重建")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="混合检索")
    p.add_argument("query", help="自然语言查询")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--type", dest="type", default=None,
                   choices=["text", "pdf", "image", "audio", "video", "code"])
    p.add_argument("--dir", dest="dir", default=None, help="路径子串过滤")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("status", help="索引统计")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("dirs", help="列出索引范围")
    p.set_defaults(func=cmd_dirs)

    p = sub.add_parser("forget", help="移除索引范围")
    p.add_argument("dir")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("evals", help="内置迷你评测集")
    p.set_defaults(func=cmd_evals)

    args = parser.parse_args(argv)

    store = Store()
    embedder = None
    if args.cmd in ("index", "search", "doctor", "evals"):
        try:
            embedder = Embedder()
        except Exception as e:
            print(f"[错误] 模型加载失败：{e}")
            print("先跑 python run.py doctor 检查模型文件是否就位。")
            return 1
    try:
        return args.func(store, embedder, args)
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
