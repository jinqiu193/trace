"""切片层（PRD FR-2 切片策略列）。

文本/PDF 文本块按 token 预算切分：段落贪心装包，超长段落按句子再硬切，
相邻块保留一个段落的重叠防语义断裂。token 计数由调用方注入（embedder 的 tokenizer）。
"""

from __future__ import annotations

import re
from typing import Callable

from . import config

_SENT_SPLIT = re.compile(r"(?<=[。！？!?\.])\s*|\n+")


def _split_long(text: str, count_fn: Callable[[str], int], budget: int) -> list[str]:
    """超长文本按句子硬切到 budget 内。"""
    pieces: list[str] = []
    buf = ""
    for sent in _SENT_SPLIT.split(text):
        if not sent:
            continue
        if count_fn(sent) > budget:  # 单句都超 budget：按字符滑窗
            step = max(1, int(len(sent) * budget / max(count_fn(sent), 1)))
            for i in range(0, len(sent), step):
                pieces.append(sent[i : i + step])
            continue
        if count_fn(buf + sent) > budget and buf:
            pieces.append(buf)
            buf = sent
        else:
            buf += sent
    if buf.strip():
        pieces.append(buf)
    return pieces


def chunk_text(
    text: str,
    count_fn: Callable[[str], int],
    max_tokens: int = config.CHUNK_MAX_TOKENS,
    overlap_tokens: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """把一段长文本切成若干块，每块 ≤ max_tokens（前缀由 embedder 另加，需预留）。"""
    budget = max_tokens - 32  # 给 "title: none | text: " 前缀留余量
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return []

    blocks: list[str] = []
    buf = ""
    carry = ""  # 上一块尾巴，作为下一块的开头重叠
    for para in paras:
        if count_fn(para) > budget:
            if buf.strip():
                blocks.append(buf)
                buf = ""
            for piece in _split_long(para, count_fn, budget):
                blocks.append(piece)
            carry = ""
            continue
        if count_fn(carry + buf + "\n\n" + para) > budget and buf:
            blocks.append(buf)
            buf = carry + para if count_fn(carry + para) <= budget else para
            carry = ""
        else:
            buf = (carry + "\n\n" + buf if carry else buf)
            buf = (buf + "\n\n" + para) if buf else para
            carry = ""
            if count_fn(buf) > overlap_tokens:
                # 取段落尾部作为下一块重叠种子
                tail_paras = re.split(r"\n\s*\n", buf)
                carry = tail_paras[-1] if count_fn(tail_paras[-1]) <= overlap_tokens else ""
    if buf.strip():
        blocks.append(buf)

    # 过滤过碎块（< 8 token 的并到前块）
    merged: list[str] = []
    for b in blocks:
        if merged and count_fn(b) < 8:
            merged[-1] += "\n\n" + b
        else:
            merged.append(b)
    return merged
