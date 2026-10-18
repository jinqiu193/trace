"""生成内置评测用的测试文件夹 tests/testbed/。

结构对照 DigUp evals/synthetic.json（文本类子集）：
- 原有 6 文件（lease / podcast / video / screenshot / code / invoice）
- 新增 doc 类：groceries / tokyo_itinerary / flight_booking / traceback
- 新增 long 类：sailing_log（100 段深埋）/ team_notes（60 段）/ sensor_readings（纯数字陷阱）
- 新增 pdf 类：annual_notes_2026.pdf（reportlab 生成，4 页）

换机器 / 换模型版本后结果可直接对比。
"""

from pathlib import Path

TESTBED = Path(__file__).resolve().parent / "testbed"

# ---------------------------------------------------------------------------
# 基础文本文件
# ---------------------------------------------------------------------------

FILES = {
    "lease.txt": """住房租赁合同（节选）

第七条 宠物条款
承租人经出租人书面同意后，可饲养体重 10 公斤以下的家养宠物（猫、狗各一只）。
宠物造成设施损坏的，承租人应负责修复或赔偿。其余宠物一律不得带入房屋。

第八条 付款方式
租金按月支付，每月 5 日前转入出租人指定账户。水电燃气费按实际用量分摊。""",

    "podcast_notes.txt": """播客《深夜书房》第 42 期笔记

本期嘉宾是睡眠研究者。嘉宾提到，褪黑素分泌受蓝光抑制，睡前半小时应远离屏幕。
第 14 分钟开始详细讲解睡眠周期：深睡期大脑清理代谢废物，REM 期巩固记忆。
嘉宾建议固定起床时间比固定入睡时间更有效。""",

    "video_notes.txt": """家庭录像整理笔记

2026-03 动物园春游：
0:42 画面出现斑马群，小指斑马走近围栏；
1:15 长颈鹿进食；
3:08 大象喷水，孩子笑得很开心。
摄像机：GoPro 12，横屏 4K30。""",

    "screenshot_notes.txt": """工作截图存档说明

payment_declined.png：支付失败页面截图，报错码 E-3071，银行返回"余额不足"。
当时正准备续费服务器，后来换了备用卡支付成功。
另一张 timeout.png 是超时提示，与本单无关。""",

    "code_notes.txt": """utils/retry.py 设计笔记

def call_with_retry(fn, max_attempts=3):
    # 失败后指数退避重试：1s, 2s, 4s
    for attempt in range(max_attempts):
        try:
            return fn()
        except TransientError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(2 ** attempt)

仅对瞬时错误重试，业务错误直接抛出。""",

    "invoice_notes.txt": """差旅报销记录

发票号 INV-1024，金额 3,280 元，住宿费，开票日期 2026-09-18，酒店：仁济路亚朵。
发票号 INV-1025，金额 460 元，高铁票，上海虹桥—杭州东。
报销单已提交财务，状态：审核中。""",

    # ---- doc 类 ----
    "groceries.txt": """购物清单（周六补货）

生鲜：牛奶 2 升、鸡蛋 12 个、菠菜一把、三文鱼排两块、
西红柿 6 个、土豆 3 斤。
日用：洗衣液、垃圾袋、纸巾。
提醒：顺便取干洗的衣服，买猫粮。""",

    "tokyo_itinerary.md": """# 东京一日游行程

## 上午
- 07:30 羽田机场落地，买 Suica 交通卡
- 08:30 浅草寺，雷门拍照，仲见世通吃人形烧
- 10:00 换乘到镰仓：高德院大佛（The Great Buddha），
  青铜坐像高 11.3 米，可进佛身内部参观（20 日元）

## 下午
- 13:00 镰仓小町通午餐（银鱼饭）
- 15:00 江之电到藤泽，海边散步
- 17:30 回新宿，居酒屋晚餐

## 备注
- 大佛区域禁飞无人机
- 回酒店末班车 23:48""",

    "flight_booking.txt": """航班预订确认

预订码：K7Q2LM
航线：上海浦东 PVG → 东京羽田 HND，NH920，10 月 21 日 09:15 起飞。
返程：10 月 25 日 HND → PVG，NH921。
乘客：2 人（含一名儿童）。
酒店：新宿格拉斯丽酒店，3 晚，含早。
注意：羽田国际航站楼需提前 2.5 小时到。""",

    "traceback_notes.txt": """线上问题排查记录

现象：新镜像跑 pip install 报错：
ERROR: Could not install packages due to an OSError:
externally-managed-environment
（Ubuntu 24.04 的 PEP 668 保护）

解决：容器内改用 python3 -m venv .venv 后再安装，
或加 --break-system-packages（仅限一次性容器）。

另一次 timeout.png 是 DNS 超时，与本问题无关。""",
}

# ---------------------------------------------------------------------------
# 长文档：sailing_log（100 段，目标句深埋 41/66/88/100 段）
# ---------------------------------------------------------------------------

SAILING_FILLER = [
    "清晨出发，海面平静，东南风二级。检查了燃油和淡水储备。",
    "沿岸航行，看到几艘渔船。调整帆索，航向保持东北。",
    "午后天气转阴，气压下降，决定提前寻找避风锚地。",
    "记录航迹：昨天一夜通过船载雷达标定了三处暗礁。",
    "机舱例行检查：机油位正常，皮带无裂纹，冷却水循环良好。",
    "晚餐简单，煮了面条。夜里安排值班表，两人一轮。",
    "今天练习了进出港操作，码头风力比想象中大。",
    "清洗甲板，发现左舷护舷木有磨损，记入待修清单。",
    "阅读灯塔值守日志，核对本周灯号表。",
    "潮汐计算错误导致差点搁浅，下次提前一小时出发。",
]

SAILING_SPECIAL = {
    41: "船行至外海，发动机突然停机——后来查明是燃油滤清器堵塞。",
    42: "我们随船携带了备用滤芯，二十分钟换好，发动机恢复正常，继续航行。",
    66: "夕阳下，一群海豚跟着船尾浪嬉戏了将近十分钟，全员上甲板围观。",
    88: "海事日志编号 M-417：本次航段油耗与计划偏差在 3% 以内。",
    100: "最后一天遇上浓雾，能见度不足五十米，凭雷达和 AIS 盲航三小时才安全进港。",
}

# ---------------------------------------------------------------------------
# 长文档：team_notes（60 段，"Portugal 办公室" 深埋 41 段附近）
# ---------------------------------------------------------------------------

TEAM_FILLER = [
    "本周站会同步：埋点系统升级按计划推进，暂无阻塞。",
    "设计评审通过了新版搜索界面，下周进入开发。",
    "客服团队反馈批量导出偶发超时，已定位到连接池配置。",
    "招聘进展：高级后端候选人进入终面两人。",
    "安全合规季度检查完成，两项低危项已整改。",
    "数据看板新增留存率指标，口径与上月对齐。",
    "移动端发版 3.2.1，修复了闪退问题。",
    "客户成功团队上季度续约率 94%，创历史新高。",
    "内部工具链迁移完成一半，剩余模块下月收尾。",
    "财务共享季度预算执行报告，各项支出在阈值内。",
]

TEAM_SPECIAL = {
    41: "管理层决议：下季度把远程团队整体迁往葡萄牙里斯本的联合办公点，",
    42: "涉及 12 名成员，预算含搬迁补贴与六个月临时住宿，IT 同步保障网络与设备。",
}


def _long_doc(filler: list[str], special: dict[int, str], total: int) -> str:
    paras: list[str] = []
    for i in range(1, total + 1):
        if i in special:
            paras.append(special[i])
        else:
            paras.append(filler[(i - 1) % len(filler)])
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# 纯数字陷阱：sensor_readings（has_words=False → 只走关键词通道）
# ---------------------------------------------------------------------------

def _sensor_readings() -> str:
    lines = ["传感器读数存档（每秒一条，2026-09-14 凌晨批处理）"]
    import random

    rng = random.Random(1024)
    for i in range(2, 201):
        if i == 139:
            lines.append("2026-09-14T02:18:44Z sensor=42 raw=4815162342 status=OK")
        else:
            lines.append(f"2026-09-14T02:{i//60:02d}:{i%60:02d}Z sensor=7 raw={rng.randint(10**8, 10**9-1)} status=OK")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PDF：annual_notes_2026.pdf（4 页：营收 / 富士山 / 面包 / 杂项）
# ---------------------------------------------------------------------------

def _make_pdf() -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pages = [
        ("Annual Notes 2026 — Revenue", [
            "Company revenue growth reached 18% year over year in fiscal 2026.",
            "The quarterly financial report shows revenue grew by 12 percent in Q3 alone.",
            "Subscription income is now the largest single source, ahead of licensing.",
        ]),
        ("Annual Notes 2026 — Trip", [
            "Team retreat in October: climbing Mount Fuji via the Yoshida trail.",
            "We started at the fifth station at 2 a.m. and reached the summit by sunrise.",
            "The Yoshida trail is the most popular route, with mountain huts every hour.",
        ]),
        ("Annual Notes 2026 — Kitchen", [
            "How to bake bread at home: flour, water, salt, yeast, and patience.",
            "Mix, knead ten minutes, let it double, shape, then bake at 230 degrees.",
            "Steam in the first ten minutes gives the crust its color.",
        ]),
        ("Annual Notes 2026 — Misc", [
            "Office moves to a bigger floor in December.",
            "New laptops for everyone in engineering.",
            "Holiday party planning has started; suggestions welcome.",
        ]),
    ]

    c = canvas.Canvas(str(TESTBED / "annual_notes_2026.pdf"), pagesize=A4)
    w, h = A4
    for title, lines in pages:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(2 * cm, h - 2.5 * cm, title)
        c.setFont("Helvetica", 11)
        y = h - 4 * cm
        for line in lines:
            c.drawString(2 * cm, y, line)
            y -= 0.8 * cm
        c.showPage()
    c.save()


def main() -> None:
    TESTBED.mkdir(parents=True, exist_ok=True)
    for name, content in FILES.items():
        (TESTBED / name).write_text(content, encoding="utf-8")
    (TESTBED / "sailing_log.txt").write_text(
        "帆船航海日志 2025（节选）\n\n" + _long_doc(SAILING_FILLER, SAILING_SPECIAL, 100), encoding="utf-8")
    (TESTBED / "team_notes.md").write_text(
        "# 团队周记 2026\n\n" + _long_doc(TEAM_FILLER, TEAM_SPECIAL, 60), encoding="utf-8")
    (TESTBED / "sensor_readings.txt").write_text(_sensor_readings(), encoding="utf-8")
    _make_pdf()
    n = len(list(TESTBED.iterdir()))
    print(f"测试文件夹已生成：{TESTBED}（{n} 个文件）")


if __name__ == "__main__":
    main()
