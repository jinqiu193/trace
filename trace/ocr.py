"""Windows OCR：图片/截图文字提取（rapidocr-onnxruntime，复用 ORT 运行时）。

口径来自 DigUp（Rules.swift / Indexer.swift 实测教训）：
- OCR 文本**只进 FTS 关键词通道，不进向量**——截图文字 embedding 后与
  查询的 cosine 相关性差，混入向量会污染语义排序
- 因此本模块只返回纯文本字符串，由调用方决定用途
"""

from __future__ import annotations

from pathlib import Path

from . import config

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def ocr_image(path: str | Path, max_chars: int = 4000) -> str:
    """图片 OCR → 纯文本（失败/无文字返回空串，绝不影响索引主流程）。"""
    if not config.OCR_ENABLED:
        return ""
    try:
        engine = _get_engine()
        result, _ = engine(str(path))
        if not result:
            return ""
        text = " ".join(str(line[1]) for line in result if len(line) >= 2)
        return text.strip()[:max_chars]
    except Exception:
        return ""
