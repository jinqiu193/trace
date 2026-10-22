# Trace —— 本地文件夹多模态语义检索工具（P0 内核）
# 架构成文档《Trace产品需求文档》第五章：解析 → 切片 → 向量化 → 存储 → 混合检索

__version__ = "0.1.0"

# HEIC/HEIF 解码（iPhone 照片）：pillow-heif 注册后 PIL 的 Image.open
# 即可读 .heic（vision 预处理 / OCR / 预览缩略图全链路共用 PIL）。
# 未安装时静默跳过——.heic 文件按 metadata_only 降级，不影响其他格式。
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass
