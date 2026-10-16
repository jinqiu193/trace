"""slicer 切片逻辑单测（token 计数用假函数，不依赖模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace.slicer import _split_long, chunk_text


def fake_count(text: str) -> int:
    """模拟 tokenizer：1 个 CJK 字符 = 1 token，4 个英文字符 ≈ 1 token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return cjk + rest // 4 + (1 if rest % 4 else 0)


class TestChunkText:
    def test_short_text_single_block(self):
        assert chunk_text("hello world", fake_count) == ["hello world"]

    def test_empty(self):
        assert chunk_text("", fake_count) == []
        assert chunk_text("   \n\n  ", fake_count) == []

    def test_multi_para_respects_budget(self):
        paras = [f"段落{i}的内容。" * 20 for i in range(5)]   # 每段约 140 token
        text = "\n\n".join(paras)
        blocks = chunk_text(text, fake_count, max_tokens=480, overlap_tokens=64)
        assert len(blocks) > 1
        for b in blocks:
            assert fake_count(b) <= 480 + 64  # 重叠种子可能略超

    def test_content_preserved(self):
        text = "\n\n".join(f"第{i}段的独特标记{i * 7}号" for i in range(20))
        blocks = chunk_text(text, fake_count, max_tokens=100, overlap_tokens=32)
        joined = "".join(blocks)
        for i in range(20):
            assert f"独特标记{i * 7}号" in joined

    def test_tiny_blocks_merged(self):
        # <8 token 的碎块并入前块
        text = "很长的段落" * 100 + "\n\n" + "短"
        blocks = chunk_text(text, fake_count)
        assert all(fake_count(b) >= 8 for b in blocks)


class TestSplitLong:
    def test_single_sentence_over_budget(self):
        sent = "这是一个非常非常长的句子" * 100
        pieces = _split_long(sent, fake_count, 50)
        assert len(pieces) > 1
        for p in pieces:
            assert fake_count(p) <= 50 or len(pieces) == 1

    def test_short_passthrough(self):
        assert _split_long("短句。第二句。", fake_count, 100) == ["短句。第二句。"]