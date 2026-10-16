# -*- coding: utf-8 -*-
"""searchtext 纯逻辑单测（不依赖模型/数据库）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace.searchtext import (
    expand_concepts, fts_patterns, fuzzy_variants, has_words, is_lookup,
    is_watermark_only, kind_hints, normalized, strip_watermarks, terms,
)


class TestNormalized:
    def test_nfkc_fullwidth(self):
        assert normalized("ＡＢＣ") == "ABC"

    def test_invisible_removed(self):
        assert normalized("a\u200bb") == "a b"        # 零宽空格 → 空格
        assert normalized("a\u200db") == "ab"         # 软连字符 → 删除
        assert "\ufeff" not in normalized("x\ufeffy")

    def test_idempotent(self):
        s = "你好 world ＡＢＣ １２３"
        assert normalized(normalized(s)) == normalized(s)


class TestTerms:
    def test_english_split(self):
        # "about" 是停用词（DigUp 口径），应被过滤
        assert terms("the clause about pets") == ["clause", "pets"]

    def test_stopwords_removed(self):
        ts = terms("find my photos of the dog")
        assert "find" not in ts and "my" not in ts and "photos" not in ts
        assert "dog" in ts

    def test_chinese_jieba(self):
        ts = terms("讲睡眠的那段播客")
        assert any("睡眠" in t for t in ts)
        assert any("播客" in t for t in ts)

    def test_dedup_preserve_order(self):
        assert terms("dog dog cat") == ["dog", "cat"]

    def test_short_english_dropped(self):
        # 单字符（<2）丢弃；"bc" 是合法 2 字符词，保留
        assert terms("a b dog") == ["dog"]
        assert terms("a bc dog") == ["bc", "dog"]

    def test_exact_code_kept(self):
        assert "inv-1024" in terms("invoice INV-1024")


class TestHasWords:
    def test_plain_text(self):
        assert has_words("hello world 你好")

    def test_digit_dump(self):
        assert not has_words("1234567890 " * 5)

    def test_mixed_ratio(self):
        assert has_words("abc123")       # 50% 字母
        assert not has_words("12345678901234567890ab")  # 字母占比 < 20%


class TestIsLookup:
    def test_codes(self):
        assert is_lookup(["K7Q2LM"])
        assert is_lookup(["INV-2041"])
        assert is_lookup(["4815162342"])

    def test_not_lookup(self):
        assert not is_lookup(["sleep", "podcast"])
        assert not is_lookup([])


class TestKindHints:
    def test_video(self):
        assert "video" in kind_hints("zebra in a video")

    def test_chinese_screenshot(self):
        assert "screenshot" in kind_hints("支付失败的截图")

    def test_none(self):
        assert kind_hints("random query") == set()


class TestFtsPatterns:
    def test_quote_escape(self):
        assert fts_patterns(['a"b']) == ['"a""b"']

    def test_plain(self):
        assert fts_patterns(["dog", "cat"]) == ['"dog"', '"cat"']


class TestFuzzyVariants:
    def test_truncation(self):
        v = fuzzy_variants("embeding")
        assert "embedin" in v and "mbeding" in v

    def test_short_word_no_variants(self):
        assert fuzzy_variants("abc") == []
        assert fuzzy_variants("ab") == []

    def test_normalized(self):
        v = fuzzy_variants("Ｓｅａｒｃｈ")
        assert "ｓｅａｒｃ" in v or "search"[:5] in "".join(v)


class TestConceptExpand:
    def test_woman(self):
        assert "woman" in expand_concepts(["女人"])

    def test_no_expansion_for_unknown(self):
        assert expand_concepts(["量子计算"]) == []

    def test_no_self(self):
        assert "女人" not in expand_concepts(["女人"])


class TestWatermark:
    def test_strip(self):
        out = strip_watermarks("摄图网 ID:12345 一只猫在草地上")
        assert "摄图网" not in out
        assert "猫" in out

    def test_watermark_only(self):
        assert is_watermark_only("摄图网 699pic.com ID:400112345")
        assert not is_watermark_only("摄图网水印旁边有真实内容比如一只猫")