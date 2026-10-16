"""内置评测集（对照 DigUp evals/synthetic.json 移植 + 中文化）。

tag 分解：doc / pdf-page / exact / long / zh / image / screenshot。
image 类用 Wikimedia Commons 真实照片（查询移植 DigUp synthetic）；
screenshot 类用 PIL 生成的文字截图（视觉语义 + OCR 关键词双通道验证）。
"""

from __future__ import annotations

from pathlib import Path

from . import config

TESTBED = config.BASE_DIR / "tests" / "testbed"

# (查询, 期望命中的文件名, tag, 备注)
EVAL_SET = [
    # ---- 原有 6 条（中英混合）----
    ("the clause about pets in the lease", "lease.txt", "doc", "合同宠物条款"),
    ("where they talk about sleep", "podcast_notes.txt", "doc", "播客睡眠那一期"),
    ("zebra in a video", "video_notes.txt", "doc", "视频斑马画面（文本内提及）"),
    ("payment declined error", "screenshot_notes.txt", "doc", "支付失败截图记录"),
    ("retry a failed call with backoff", "code_notes.txt", "doc", "代码：重试退避"),
    ("发票号 1024 的报销单", "invoice_notes.txt", "exact", "中文精确串"),
    # ---- doc 类（DigUp synthetic 移植）----
    ("grocery list", "groceries.txt", "doc", "购物清单"),
    ("day trip to see the great buddha", "tokyo_itinerary.md", "doc", "大佛一日游"),
    ("my flight to Japan", "flight_booking.txt", "doc", "日本航班预订"),
    ("reminder to pick up milk and eggs", "groceries.txt", "doc", "牛奶鸡蛋提醒"),
    ("python package not installed", "traceback_notes.txt", "doc", "pip 环境报错"),
    # ---- exact 类（编码/数字查找，关键词通道）----
    ("card_declined", "screenshot_notes.txt", "exact", "支付失败精确词"),
    ("E-3071", "screenshot_notes.txt", "exact", "报错码"),
    ("K7Q2LM", "flight_booking.txt", "exact", "预订码"),
    ("INV-1024", "invoice_notes.txt", "exact", "发票号（带前缀）"),
    ("externally-managed-environment", "traceback_notes.txt", "exact", "长连字精确串"),
    ("4815162342", "sensor_readings.txt", "exact", "纯数字文档中的长数字"),
    # ---- long 类（深埋长文档）----
    ("the boat engine broke down and we fixed it", "sailing_log.txt", "long", "发动机故障段"),
    ("dolphins swimming alongside the boat at sunset", "sailing_log.txt", "long", "海豚段落"),
    ("sailing blind in thick fog", "sailing_log.txt", "long", "浓雾盲航（文档末尾）"),
    ("M-417", "sailing_log.txt", "long", "日志编号"),
    ("moving the team to an office in Portugal", "team_notes.md", "long", "迁往葡萄牙办公室"),
    # ---- pdf-page 类 ----
    ("company revenue growth", "annual_notes_2026.pdf", "pdf", "营收增长（第1页）"),
    ("climbing mount fuji", "annual_notes_2026.pdf", "pdf", "爬富士山（第2页）"),
    ("how to bake bread at home", "annual_notes_2026.pdf", "pdf", "家庭烤面包（第3页）"),
    # ---- zh 类（中文查询，jieba 分词 + 语义）----
    ("讲睡眠的那段播客", "podcast_notes.txt", "zh", "中文语义"),
    ("含宠物条款的合同", "lease.txt", "zh", "中文语义"),
    ("支付失败的截图", "screenshot_notes.txt", "zh", "中文语义"),
    ("东京一日游行程", "tokyo_itinerary.md", "zh", "中文语义"),
    ("发动机故障的航海日志", "sailing_log.txt", "zh", "中文+长文档"),
    ("浓雾里盲航的那段记录", "sailing_log.txt", "zh", "中文+长文档"),
    # ---- image 类（真实照片，视觉编码器语义通道；查询移植 DigUp synthetic）----
    ("a penguin", "penguin.jpg", "image", "企鹅"),
    ("black and white striped animal", "zebra.jpg", "image", "斑马"),
    ("bird of prey", "eagle.jpg", "image", "鹰"),
    ("string instrument", "violin.jpg", "image", "小提琴"),
    ("planet earth from space", "earth.jpg", "image", "太空看地球"),
    ("thunderstorm", "lightning.jpg", "image", "雷暴"),
    ("yellow flower", "sunflower.jpg", "image", "向日葵"),
    ("christmas cookie", "gingerbread.jpg", "image", "姜饼人"),
    ("desert plant", "cactus.jpg", "image", "仙人掌"),
    # ---- screenshot 类（文字截图：视觉语义 + OCR 关键词双通道）----
    ("payment declined error", "shot_payment_error.png", "screenshot", "支付报错截图"),
    ("python package not installed", "shot_python_traceback.png", "screenshot", "traceback 截图"),
    ("my flight to Japan", "shot_flight_booking.png", "screenshot", "航班预订截图"),
    ("chat about moving the design review", "shot_chat_meeting.png", "screenshot", "聊天截图"),
    ("rainy weather forecast", "shot_weather.png", "screenshot", "天气预报截图"),
    ("swift code for vector search", "shot_swift_code.png", "screenshot", "代码截图"),
]


def run_evals(store, embedder) -> int:
    if not TESTBED.is_dir():
        print(f"测试文件夹不存在：{TESTBED}")
        print("先运行：python tests/make_testbed.py")
        return 1
    print(f"== 内置评测集（hit@3 / MRR，{len(EVAL_SET)} 条）==")
    hits = 0
    rr = 0.0
    by_tag: dict[str, list[int]] = {}
    for query, want, tag, note in EVAL_SET:
        from .search import search_with_timing

        res = search_with_timing(store, embedder, query, limit=5, dir_filter="testbed")
        paths = [Path(r["path"]).name for r in res["results"]]
        rank = paths.index(want) + 1 if want in paths else 0
        hit = 0 < rank <= 3
        hits += 1 if hit else 0
        rr += 1.0 / rank if rank else 0.0
        by_tag.setdefault(tag, []).append(1 if hit else 0)
        mark = "✓" if hit else "✗"
        print(f"  {mark} [{tag:>4}] 「{query}」→ 期望 {want}｜实际 {paths[:3]}（{note}）")
    n = len(EVAL_SET)
    print(f"\nhit@3 = {hits}/{n}   MRR = {rr/n:.3f}")
    print("按类别：")
    for tag, marks in sorted(by_tag.items()):
        print(f"  {tag:<6} {sum(marks)}/{len(marks)}")
    return 0
