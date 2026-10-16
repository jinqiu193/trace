"""文本归一化与查询词提取（对照 DigUp SearchText.swift 中文化）。

职责：
- normalized()：NFKC + 不可见字符/阿拉伯变体归一，关键词索引与查询同口径
- terms()：查询的内容词（去停用词、去类型词、≥2 字符、保序去重）
- has_words()：文本是否"有文字"（字母占比 ≥20%），纯数字/表格只做关键词
- is_lookup()：查询是否纯编码/数字查找（发票号、错误码），语义降权关键词加权
- kind_hints()：查询中的类型词（"视频里的"→ video），软加成不过滤

中英双语停用词；中文无空格，按 CJK 单字+连续英文数字混合切分。
"""

from __future__ import annotations

import re
import unicodedata

try:
    import jieba  # 中文分词（DigUp 承认中文不切词是其短板，此处补上）
    jieba.setLogLevel(20)
    _HAS_JIEBA = True
except ImportError:  # pragma: no cover
    _HAS_JIEBA = False

# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------

# 零宽空格 → 普通空格（词边界）；软连字符/BOM/方向标记/joiner → 删除
_INVISIBLE_DELETE = {
    "\u00ad", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2060", "\u2061",
    "\u2062", "\u2063", "\u2064", "\u2066", "\u2067", "\u2068", "\u2069",
    "\ufeff",
}
# 阿拉伯：tatweel（延长符）、harakat（短元音）、dagger alef → 删除
_ARABIC_DELETE = {"\u0640"} | {chr(c) for c in range(0x064b, 0x0660)} | {"\u0670"}
# alef 变体（أ إ آ ٱ）→ 普通 alef
_ALEF_VARIANTS = {"\u0622", "\u0623", "\u0625", "\u0671"}


def normalized(text: str) -> str:
    """NFKC 兼容分解 + 不可见/变体字符归一。"""
    text = unicodedata.normalize("NFKC", text)
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == "\u200b":
            out.append(" ")
        elif ch in _INVISIBLE_DELETE or ch in _ARABIC_DELETE:
            continue
        elif ch in _ALEF_VARIANTS:
            out.append("\u0627")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 停用词 / 类型词
# ---------------------------------------------------------------------------

STOPWORDS = {
    # 英文（DigUp 口径）
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "my", "me", "that", "this", "these", "those", "from", "about", "where",
    "what", "which", "who", "when", "is", "was", "are", "were", "be", "it",
    "its", "some", "any", "find", "show", "file", "files", "photo", "photos",
    "picture", "image", "images", "screenshot", "screenshots", "video",
    "videos", "pdf", "document", "documents", "recording", "how", "do", "i",
    # 中文
    "的", "了", "和", "与", "及", "是", "在", "有", "我", "你", "他", "她", "它",
    "这", "那", "个", "之", "于", "把", "被", "让", "向", "从", "到", "对", "为",
    "以", "其", "或", "并", "又", "再", "还", "都", "也", "不", "很", "就", "着",
    "过", "吗", "呢", "吧", "啊", "请", "帮", "找", "查", "看", "想", "要",
    "一下", "什么", "怎么", "哪里", "哪个", "多少", "可以", "里面", "时候",
    "文件", "文档", "图片", "照片", "截图", "视频", "音频", "录音", "影片", "短片",
}

# 类型词 → 文件类型（软加成，不过滤）
KIND_WORDS = {
    "screenshot": "screenshot", "screenshots": "screenshot",
    "photo": "image", "photos": "image", "picture": "image", "pictures": "image",
    "image": "image", "images": "image",
    "pdf": "pdf", "pdfs": "pdf",
    "doc": "doc", "docs": "doc", "document": "doc", "documents": "doc",
    "note": "doc", "notes": "doc",
    "video": "video", "videos": "video", "clip": "video", "movie": "video",
    "audio": "audio", "song": "audio", "podcast": "audio", "voice": "audio",
    "sound": "audio",
    # 中文类型词（搜"…的截图"也应给出类型提示）
    "截图": "screenshot", "屏幕": "screenshot", "屏摄": "screenshot",
    "图片": "image", "照片": "image", "相片": "image",
    "视频": "video", "影片": "video", "短片": "video", "录像": "video",
    "音频": "audio", "录音": "audio", "音乐": "audio", "声音": "audio",
    "文档": "doc", "笔记": "doc", "文章": "doc",
}


def _cjk_pattern() -> str:
    return r"[\u4e00-\u9fff\u3400-\u4dbf]+"


# 保留兼容别名（历史代码引用）
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\.]+|" + _cjk_pattern())


def terms(text: str) -> list[str]:
    """查询的内容词：归一化 → 切分 → 去停用词 → 去重保序。

    英文/数字按词切分；中文连续段走 jieba 精确分词（无 jieba 时退化为
    整段一词——DigUp 的口径）。中文单字词保留（"票"可能正是要找的），
    英文短词（<2 字符）丢弃。
    """
    norm = normalized(text).lower()
    seen: set[str] = set()
    out: list[str] = []

    def _add(w: str) -> None:
        w = w.strip("._-")
        if len(w) < 2 or w in STOPWORDS or w in seen:
            return
        seen.add(w)
        out.append(w)

    # 英文/数字/符号段
    for m in re.finditer(r"[A-Za-z0-9_\-\.]+", norm):
        _add(m.group(0))
    # 中文连续段
    for m in re.finditer(r"[\u4e00-\u9fff\u3400-\u4dbf]+", norm):
        seg = m.group(0)
        if _HAS_JIEBA:
            for w in jieba.lcut(seg):
                _add(w)
        else:
            _add(seg)
    return out


def has_words(text: str) -> bool:
    """文本是否"说人话"：非空白字符中字母（含 CJK）占比 ≥20%。

    数据转储、常量表返回 False——只进关键词索引，不浪费向量。
    """
    letters = 0
    others = 0
    for ch in text:
        if ch.isspace():
            continue
        if ch.isalpha():
            letters += 1
        else:
            others += 1
    return letters > 0 and letters * 4 >= others


def is_lookup(ts: list[str]) -> bool:
    """查询是否纯编码/数字查找（"K7Q2LM"、"INV-2041"、"4815162342"）。

    这类词嵌入模型表达不了，语义通道降权、关键词通道加倍。
    """
    if not ts:
        return False
    return all(
        any(c.isdigit() for c in t) and (any(c.isalpha() for c in t) or len(t) >= 4)
        for t in ts
    )


def kind_hints(text: str) -> set[str]:
    """查询提到的文件类型（"…in a video"、"截图里的"）→ {file_type}。"""
    norm = normalized(text).lower()
    words = set(re.findall(r"[a-z]+", norm))
    # 中文连续段走 jieba 分词（否则整段无法命中类型词表）
    for m in re.finditer(r"[\u4e00-\u9fff]+", norm):
        seg = m.group(0)
        if _HAS_JIEBA:
            words.update(w for w in jieba.lcut(seg) if len(w) >= 2)
        else:
            words.add(seg)
    hints = set()
    for w in words:
        if w in KIND_WORDS:
            hints.add(KIND_WORDS[w])
        elif w.isascii():
            # 英文复数/变形宽松匹配
            for key, kind in KIND_WORDS.items():
                if key.startswith(w) and len(w) >= 3:
                    hints.add(kind)
                    break
    return hints


# ---------------------------------------------------------------------------
# FTS 查询构造
# ---------------------------------------------------------------------------

def fts_patterns(ts: list[str], prefix_last: bool = False) -> list[str]:
    """查询词 → FTS5 quoted pattern 列表（trigram 下即子串匹配）。

    trigram 对 <3 字符的英文词匹配不到（由语义通道兜底）；
    中文连续词（≥3 字）可正常工作。
    """
    out = []
    for i, t in enumerate(ts):
        # FTS5 字符串内引号转义
        esc = t.replace('"', '""')
        star = "*" if (prefix_last and i == len(ts) - 1) else ""
        out.append(f'"{esc}"{star}')
    return out


def fuzzy_variants(t: str) -> list[str]:
    """词的错字容错变体：去尾 / 去头各一个字符（编辑距离 1 的截断近似）。

    trigram FTS 是子串匹配：'embeding' 的变体 'embedin' 能命中 'embedding'
    （子串成立）；中文 '复牌总结' → '总结'（若 jieba 未切出）也能救回。
    仅在精确词全部零命中时由 search 层启用，coverage 打折防止噪声抬分。
    """
    n = normalized(t).lower()
    if len(n) <= 3:
        return []
    return [n[:-1], n[1:]]


# ---------------------------------------------------------------------------
# 中英概念字典（2026-10-15）
# ---------------------------------------------------------------------------
# 解决 "搜中文搜不到英文命中的图/视频帧" 问题的轻量方案：
# 搜 "女人" 时 FTS 通道同时搜 "woman/women/female/girl/lady"，
# 索引时把 OCR 文本+文件名+目录名都写进 keywords（indexer.py 已配合），
# 于是 filename=美女.png / woman_photo.jpg 都能被中文/英文任一查询命中。
#
# 注意：这只解决"文件有文本线索"的场景。真正跨模态语义
# （搜任意图找女人）需要 VLM 生成 caption，超出本期范围。

CONCEPT_EXPAND: dict[str, list[str]] = {
    # 人
    "女人": ["woman", "women", "female", "girl", "lady"],
    "女生": ["woman", "women", "female", "girl", "lady"],
    "女孩": ["girl", "woman"],
    "妇女": ["woman", "women", "female"],
    "男人": ["man", "men", "male", "boy", "guy"],
    "男生": ["man", "men", "male", "boy", "guy"],
    "男孩": ["boy", "man"],
    "小孩": ["child", "kid", "baby", "children"],
    "孩子": ["child", "kid", "baby", "children"],
    "婴儿": ["baby", "infant", "newborn"],
    "人": ["person", "people", "human", "man", "woman"],
    # 动物
    "猫": ["cat", "kitten", "kitty"],
    "狗": ["dog", "puppy"],
    "鸟": ["bird"],
    "鱼": ["fish"],
    # 物品 / 场景
    "车": ["car", "vehicle", "auto"],
    "汽车": ["car", "vehicle", "auto"],
    "房子": ["house", "home", "building"],
    "建筑": ["building", "architecture"],
    "室内": ["indoor", "room", "inside", "interior"],
    "室外": ["outdoor", "outside", "exterior"],
    "风景": ["landscape", "scenery", "nature", "view"],
    "自然": ["nature", "natural", "landscape"],
    "食物": ["food", "meal", "dish", "cuisine"],
    "水果": ["fruit"],
    "花": ["flower", "bloom", "blossom"],
    "树": ["tree"],
    # 时间
    "日出": ["sunrise", "dawn"],
    "日落": ["sunset", "dusk"],
    "夜晚": ["night", "nighttime", "evening"],
    "白天": ["day", "daytime"],
    # 颜色
    "红色": ["red"],
    "蓝色": ["blue"],
    "绿色": ["green"],
    "黑色": ["black"],
    "白色": ["white"],
    # 情绪 / 抽象（弱相关，召回为主）
    "开心": ["happy", "smile", "joy", "smiling"],
    "笑": ["smile", "laugh", "smiling", "laughing"],
}


def expand_concepts(ts: list[str]) -> list[str]:
    """查询词中英概念扩展。返回去重的扩展词列表（不包含原词）。

    用法：search.py 拿原始 ts 跑嵌入 + 关键词召回，同时跑 ts+扩展 再做一遍关键词召回。
    """
    out: list[str] = []
    seen: set[str] = set()
    for t in ts:
        for ex in CONCEPT_EXPAND.get(t, []):
            if ex not in seen:
                seen.add(ex)
                out.append(ex)
    return out


# ---------------------------------------------------------------------------
# 素材图水印词黑名单（2026-10-15）
# ---------------------------------------------------------------------------
# 53 张 webp 素材的 snippet 全是 "摄图网 / 大图网 / 辽图网 / 搜图网" 之类水印词，
# FTS 通道按 trigram 匹配：只要查询词跟水印词有任何子串重合，覆盖率就被撑高，
# 出现"53 张图互刷关键词得分"的噪声。
# 两个层面处理：
#   D）indexer 写切片时 strip_watermarks()，让水印不进 vec/slices/slices_fts
#   C）search.py 拿到 FTS 命中后，对 snippet 完全由水印词组成的 slice 跳过

WATERMARK_TERMS: set[str] = {
    # 国内素材平台（中英）
    "摄图网", "摄图", "699pic", "699pic.com",
    "千图网", "千图", "58pic", "58pic.com",
    "包图网", "包图", "ibaotu", "ibaotu.com",
    "图精灵", "picpsd",
    "觅知网", "miznetwork",
    "觅元素", "51yuansu",
    "红动中国", "dobeimg",
    "站酷", "zcool", "zcool.com",
    "熊猫办公", "docer", "tukuppt", "tukuppt.com",
    "我图网", "woto", "wotopia",
    "汇图网", "huitu", "huitu.com",
    "大图网", "datu", "datuwang",
    "搜图网", "sotu", "sotuwang",
    "辽图网", "liaotu",
    "新图网", "xintu",
    "图觅", "tumee",
    "90设计", "90sheji", "90sheji.com",
    "花瓣网", "huaban", "huaban.com",
    "堆糖", "duitang", "duitang.com",
    # 国外
    "shutterstock", "shutterstock.com",
    "istock", "istockphoto", "getty", "gettyimages",
    "adobe stock", "stock.adobe", "adobestock",
    "123rf", "depositphotos", "dreamstime", "alamy",
    "unsplash", "unsplash.com", "pexels", "pexels.com",
    "pixabay", "pixabay.com", "freepik", "freepik.com",
    # 通用版权 / 兜底
    "版权所有", "侵权必究", "all rights reserved", "©", "copyright",
}


# 水印词通常附带 ID/编号（如 "摄图网 ID:12345"、"摄图·699pic.com"）。
# 用宽松正则：任一 WATERMARK_TERMS 出现就切。
_WATERMARK_ID_RE = re.compile(
    r"(?<![a-zA-Z])(?i:id|no)\s*[:：]?\s*[\d\-]+|编号\s*[:：]?\s*[\d\-]+"
)

# 替换必须"最长优先"：WATERMARK_TERMS 是 set，迭代顺序随 hash 种子随机。
# 若 "699pic" 先于 "699pic.com" 替换，会残留 ".com"（ascii token），
# 导致 is_watermark_only 判定随机翻转（PYTHONHASHSEED 竞态）。
_WATERMARK_TERMS_BY_LEN: tuple[str, ...] = tuple(
    sorted(WATERMARK_TERMS, key=len, reverse=True)
)


def strip_watermarks(text: str) -> str:
    """从文本里删掉水印词。删后空白折叠。

    用于 indexer 写切片前 / 搜索结果高亮前。
    """
    if not text:
        return ""
    out = text
    for w in _WATERMARK_TERMS_BY_LEN:
        if w in out:
            out = out.replace(w, " ")
    out = _WATERMARK_ID_RE.sub(" ", out)
    # 折叠多余空白；保留中英标点
    out = re.sub(r"\s+", " ", out).strip()
    return out


def is_watermark_only(s: str) -> bool:
    """一段文本是否"基本就是水印"。

    启发：去掉标点/空白/水印词后，剩下的中文+英文 token 数 ≤1。
    用于 search.py：FTS 命中里纯水印的 slice 视为噪声不计 coverage。
    """
    if not s:
        return True
    cleaned = s.lower()
    for w in _WATERMARK_TERMS_BY_LEN:
        cleaned = cleaned.replace(w, " ")
    cleaned = _WATERMARK_ID_RE.sub(" ", cleaned)
    # 去掉空白、标点、数字、ascii 符号
    cleaned = re.sub(r"[\s\W_0-9]+", "", cleaned, flags=re.UNICODE)
    # 中文单字不算"内容词"（很多水印截断后会留个孤字）
    chinese_chars = sum(1 for ch in cleaned if "\u4e00" <= ch <= "\u9fff")
    ascii_tokens = len(re.findall(r"[A-Za-z]{2,}", cleaned))
    return chinese_chars <= 1 and ascii_tokens == 0
