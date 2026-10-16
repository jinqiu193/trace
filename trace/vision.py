"""Gemma4 图片预处理（Python 移植自 transformers.js Gemma4ImageProcessor）。

链路（与 ONNX 导出端 processor_config.json 对齐）：
1. RGB 转换
2. 保持宽高比 resize：目标像素 = max_patches(2520) × patch²(256)，边长对齐
   pooling×patch=48 的整数倍
3. × 1/255 rescale
4. patchify：16×16×3 = 768 维/patch（HWC 顺序），zero-pad 到 max_patches
5. position_ids：(col, row)，pad -1

产出：pixel_values [2520, 768] f32、pixel_position_ids [2520, 2] i64、
num_soft_tokens = floor(num_patches / 9)。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import config

PATCH_SIZE = 16
POOLING = 3
MAX_SOFT_TOKENS = 280
MAX_PATCHES = MAX_SOFT_TOKENS * POOLING * POOLING   # 2520
PATCH_DIM = PATCH_SIZE * PATCH_SIZE * 3             # 768
SIDE_MULT = POOLING * PATCH_SIZE                    # 48


def target_size(height: int, width: int) -> tuple[int, int]:
    """保持宽高比的目标尺寸（JS get_aspect_ratio_preserving_size 直译）。"""
    target_px = MAX_PATCHES * PATCH_SIZE ** 2
    factor = math.sqrt(target_px / (height * width))
    target_h = math.floor(factor * height / SIDE_MULT) * SIDE_MULT
    target_w = math.floor(factor * width / SIDE_MULT) * SIDE_MULT
    max_side = math.floor(MAX_PATCHES / POOLING ** 2) * SIDE_MULT
    if target_h == 0 and target_w == 0:
        raise ValueError(f"image too thin to embed: {height}x{width}")
    if target_h == 0:
        target_h = SIDE_MULT
        target_w = min(math.floor(width / height) * SIDE_MULT, max_side)
    elif target_w == 0:
        target_w = SIDE_MULT
        target_h = min(math.floor(height / width) * SIDE_MULT, max_side)
    return target_h, target_w


def _resize_bicubic_float(arr: np.ndarray, th: int, tw: int) -> np.ndarray:
    """float32 三通道 bicubic resize（逐通道 mode='F'，避免 uint8 量化损失）。"""
    h, w = arr.shape[:2]
    if (h, w) == (th, tw):
        return arr
    out = np.empty((th, tw, arr.shape[2]), dtype=np.float32)
    for c in range(arr.shape[2]):
        ch = Image.fromarray(arr[:, :, c], mode="F")
        ch = ch.resize((tw, th), Image.BICUBIC)
        out[:, :, c] = np.asarray(ch, dtype=np.float32)
    return out


def preprocess(path: str | Path) -> tuple[np.ndarray, np.ndarray, int]:
    """图片 → (pixel_values, pixel_position_ids, num_soft_tokens)。

    pixel_values:      [MAX_PATCHES, 768] f32（0 padding）
    pixel_position_ids: [MAX_PATCHES, 2] i64（-1 padding）
    num_soft_tokens:    实际软 token 数（视觉编码器输出行数）
    """
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return preprocess_pil(img)


def preprocess_pil(img: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    """PIL.Image → (pixel_values, pixel_position_ids, num_soft_tokens)。

    与 preprocess(path) 共用同一预处理逻辑（视频帧等"内存里"的图像走这条）。
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    th, tw = target_size(h, w)
    arr = np.asarray(img, dtype=np.float32)
    arr = _resize_bicubic_float(arr, th, tw)
    arr *= 1.0 / 255.0

    n_h, n_w = th // PATCH_SIZE, tw // PATCH_SIZE
    n = n_h * n_w
    patches = (
        arr.reshape(n_h, PATCH_SIZE, n_w, PATCH_SIZE, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(n, PATCH_DIM)
    )

    pv = np.zeros((MAX_PATCHES, PATCH_DIM), dtype=np.float32)
    pv[:n] = patches
    cols = np.tile(np.arange(n_w, dtype=np.int64), n_h)
    rows = np.repeat(np.arange(n_h, dtype=np.int64), n_w)
    pos = np.full((MAX_PATCHES, 2), -1, dtype=np.int64)
    pos[:n, 0] = cols
    pos[:n, 1] = rows
    return pv, pos, n // (POOLING * POOLING)


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in config.IMAGE_EXTS
