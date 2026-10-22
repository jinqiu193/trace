"""全局配置：路径、模型参数、任务前缀、检索权重。

模型规格来自 onnx-community/embeddinggemma-2-ONNX 的 config.json 与
config_sentence_transformers.json（2026-10 导出）：
- 文本主干 hidden 512 → 投影 768 维，24 层，滑窗 512，有效上下文 8192
- 查询前缀 "task: search result | query: "，文档前缀 "title: none | text: "
- 相似度 cosine，池化方式为均值池化（模型卡口径）
"""

import os
from pathlib import Path

# ---- 路径 ----
# 环境变量可覆盖（TRACE_DB_PATH / TRACE_MODELS_DIR / TRACE_PROJECT_ROOT），
# 供安装到 Program Files 等无写权限目录时把数据迁到 %APPDATA%。
BASE_DIR = Path(os.environ.get("TRACE_PROJECT_ROOT",
                               Path(__file__).resolve().parent.parent))
MODELS_DIR = Path(os.environ.get("TRACE_MODELS_DIR", BASE_DIR / "models"))
DB_PATH = Path(os.environ.get("TRACE_DB_PATH", BASE_DIR / "trace.db"))

MODEL_ONNX = MODELS_DIR / "model_quantized.onnx"           # int8 文本模型（313MB）
TOKENIZER_JSON = MODELS_DIR / "tokenizer.json"
VISION_ONNX = MODELS_DIR / "vision_encoder_quantized.onnx" # 视觉编码器
AUDIO_ONNX = MODELS_DIR / "onnx" / "audio_encoder_quantized.onnx"  # 音频编码器（HF onnx/ 子目录）

# ---- WeMM-Embedding-2B (GGUF) ----
WEMM_DIR = BASE_DIR / "WeMM-Embedding-2B-GGUF"
WEMM_MODEL = WEMM_DIR / "WeMM-Embedding-2B-Q4_K_M.gguf"
WEMM_MMPROJ = WEMM_DIR / "mmproj-WeMM-Embedding-2B-BF16.gguf"
WEMM_EMBED_DIM = 2048

# ---- 模型参数 ----
EMBED_DIM = 768
EMBED_DIM_WEMM = 2048      # WeMM-Embedding-2B 输出维度
MAX_TOKENS = 8192          # 四模态共用上下文
MAX_TOKENS_WEMM = 4096     # WeMM 上下文（llama_cpp 建议值）
IMAGE_TOKENS = 280         # 每张图约 280 tokens
VIDEO_FRAME_TOKENS = 70    # 视频帧 70 tokens（DigUp 口径，省 4× 推理/存储）
AUDIO_TOKENS_PER_SEC = 25  # 音频 25 tokens/秒

# HuggingFace 模型仓库（首次启动时按需下载）
HF_REPO = "onnx-community/embeddinggemma-2-ONNX"
HF_REVISION = "main"
AUDIO_FILE = "onnx/audio_encoder_quantized.onnx"     # ~340MB（在 onnx/ 子目录）
AUDIO_DATA_FILE = "onnx/audio_encoder_quantized.onnx_data"
HF_ENDPOINT = "https://hf-mirror.com"                # 国内镜像，失败回退默认

# ---- 任务前缀（config_sentence_transformers.json）----
QUERY_PREFIX = "task: search result | query: "
DOC_PREFIX = "title: none | text: "
CODE_QUERY_PREFIX = "task: code retrieval | query: "

# ---- WeMM-Embedding-2B 前缀 ----
WEMM_QUERY_PREFIX = "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: "
WEMM_DOC_PREFIX = ""

# ---- 跨模态检索 image/audio 索引时的可选 text prefix ----
# EG2 spec 写"image/audio 不加 prefix"，但实测跨模态 cos 偏低（text↔image ~0.60 vs text↔text ~0.85），
# 加 prefix 把 image 嵌入"拉"到 text 一侧能显著缩小间隙。
# 183 张图 × 10 query 测试：DOC_PREFIX 使 99.6% 的 (q,img) 对 cos 涨，平均 +0.040。
# ""  → 走 spec 原文（默认，保守，行为不变）
# DOC_PREFIX    → "title: none | text: "，推荐选项，DOC 胜率 65%
# QUERY_PREFIX   → "task: search result | query: "，DOC 胜率 35%（生产代码路径实测）
#
# 启用后会改变 image/audio 切片向量；FINGERPRINT 已包含此开关值，切向量会失效需重索引。
IMAGE_INDEX_PREFIX = ""

# ---- 多模态特殊 token（tokenizer_config.json，Gemma4 口径）----
# 图片嵌入序列：<bos> <|image> <|image|>×N <image|> <eos>（DigUp/mtmd 同构，无 task 前缀）
BOI_TOKEN = "<|image>"       # begin of image
IMAGE_TOKEN = "<|image|>"    # 软 token 占位（重复 N 次）
EOI_TOKEN = "<image|>"       # end of image

# ---- 混合检索权重（PRD FR-4：score = α·cos + β·keyword + γ·recency）----
W_COSINE = 0.60
W_KEYWORD = 0.25
W_RECENCY = 0.15

# ---- 切片参数（PRD FR-2）----
CHUNK_MAX_TOKENS = 480     # 文本块目标上限（留足前缀余量）
CHUNK_OVERLAP_TOKENS = 64  # 相邻块重叠，防语义断裂
AUDIO_WINDOW_SEC = 30      # 音频 30 秒窗口（EmbeddingGemma 2 audio 最多 30s）
AUDIO_STRIDE_SEC = 25      # 步长 25 秒 → 5 秒重叠（DigUp：避免边缘词漏掉）
# ---- 音频窗口数自适应（2026-10-18 v2）：N = clamp(ceil(dur/SPAN), MIN, MAX) ----
AUDIO_MAX_WINDOWS = int(os.environ.get("TRACE_AUDIO_MAX_WINDOWS", "10"))
    # 窗数上限（原"固定 5 窗"改为自适应：短音频 ≥4 窗，2h ≈6 窗，超长 ≤10）
AUDIO_MIN_WINDOWS = 4
AUDIO_SPAN_SEC = float(os.environ.get("TRACE_AUDIO_SPAN", "1200"))
    # 每段时长 20 分钟一窗；2h → 6 窗
AUDIO_SILENCE_FWD_SEC = float(os.environ.get("TRACE_AUDIO_SILENCE_FWD", "600"))
    # 目标点静音时向前探测补采的最大范围（秒）；整段静音则放弃该窗
VIDEO_FRAME_INTERVAL_SEC = 10  # tick 模式：每 10 秒抽一帧（scene 模式关闭时的回退口径）
VIDEO_SEEK_MODE = os.environ.get("TRACE_VIDEO_SEEK", "1") != "0"
    # seek 抽帧（默认启用）：只 seek 到 N 个目标时间点各取一帧，不解全片。
    # v2（2026-10-18）：N 随时长自适应 + 关键帧密度加权（场景切换密集区多采）。
    # TRACE_VIDEO_SEEK=0 回退场景模式。
VIDEO_SCENE_MODE = os.environ.get("TRACE_VIDEO_SCENE", "1") != "0"
    # 场景模式（seek 关闭时的回退）：skip_frame=NONKEY 只解码关键帧
    # + aHash 场景检测 + 超上限等距抽稀。TRACE_VIDEO_SCENE=0 再回退 tick 均匀采样。
VIDEO_SCENE_MAX_SLICES = int(os.environ.get("TRACE_VIDEO_SCENE_MAX", "5"))
    # 场景模式产出帧数上限（回退模式专用）
# ---- 视频帧数自适应（2026-10-18 v2）：N = clamp(ceil(dur/SPAN), MIN, MAX) ----
VIDEO_MAX_SLICES = int(os.environ.get("TRACE_VIDEO_MAX_SLICES", "12"))
    # 帧数上限（原"固定 5 帧"改为自适应：短视频 ≥5 帧，2h ≈8 帧，超长 ≤12）
VIDEO_MIN_SLICES = 5
VIDEO_SLICE_SPAN_SEC = float(os.environ.get("TRACE_VIDEO_SPAN", "900"))
    # 每 15 分钟一帧；2h → 8 帧
VIDEO_KEYFRAME_WEIGHT = os.environ.get("TRACE_VIDEO_KF_WEIGHT", "1") != "0"
    # 关键帧密度加权：demux 只读关键帧时间戳（不解码，秒级），编码器通常在
    # 场景切换处插 I 帧 → 用其密度分布把采样点拉向内容变化密集区；
    # 恒定 GOP 视频密度均匀，自动退化为等距，无副作用。=0 回退纯等距。
VIDEO_FRAME_MAX_PIXEL = 512    # 抽帧前长边缩到 512（CPU 跑 512 比 1024 快 ~4×，召回损失小）
VIDEO_AHASH_SIDE = 8          # aHash 边长（DigUp：8×8 = 64 bit）
VIDEO_AHASH_TOLERANCE = 4     # XOR 后 ≤4 bit 差异视为重复帧（DigUp 口径）

# ---- 性能与后台行为（2026-10-16 工程化）----
EMBED_BATCH = int(os.environ.get("TRACE_EMBED_BATCH", "16"))     # 批量推理条数
BATCH_COMMIT_FILES = int(os.environ.get("TRACE_BATCH_COMMIT", "20"))  # 每 N 文件一次事务
FTS_IN_CHUNK = 500            # SQL IN(...) 参数分批上限（旧 sqlite 999 限制兼容）
WATCH_ENABLED = os.environ.get("TRACE_WATCH", "1") != "0"       # 文件变更监听（轮询）
WATCH_INTERVAL_SEC = float(os.environ.get("TRACE_WATCH_INTERVAL", "60"))
WATCH_RESCAN_THRESHOLD = 50   # 变更数超过此值改走整目录增量（比逐文件快）

# ---- 索引指纹（DigUp fingerprint：模型/参数变化 → 索引不可混用，须重建）----
# v2（2026-10-18）：只含"算法语义版本"，不含具体帧数/窗数 N——N 由视频时长
# 自适应决定（VIDEO_SLICE_SPAN_SEC 等），调参只影响新索引文件，不再强制
# 全库重建。per-file 判定见 indexer（files.extraction 列）。
_FP_IMG_PREFIX = f"img-pref={IMAGE_INDEX_PREFIX or 'none'}"
_FP_MODE = (
    ("seek-v2-kfw" if VIDEO_KEYFRAME_WEIGHT else "seek-v2-equi") if VIDEO_SEEK_MODE
    else (f"scene{VIDEO_SCENE_MAX_SLICES}" if VIDEO_SCENE_MODE
          else f"tick{VIDEO_FRAME_INTERVAL_SEC}")
)
_FP_FRAME = _FP_MODE + f"/ahash{VIDEO_AHASH_SIDE}/{VIDEO_AHASH_TOLERANCE}/maxpx{VIDEO_FRAME_MAX_PIXEL}"
_FP_AUDIO = f"audio-v2-rmsfwd{int(AUDIO_SILENCE_FWD_SEC)}/win{AUDIO_WINDOW_SEC}"
FINGERPRINT = (
    "embeddinggemma-2-onnx-int8"
    "| prompt-title-v1"          # 文档向量带文件名标题
    "| haswords-v1"              # 纯数字文本只进关键词
    f"| chunk{CHUNK_MAX_TOKENS}/ov{CHUNK_OVERLAP_TOKENS}"
    f"| frame{VIDEO_FRAME_TOKENS}/{_FP_FRAME}"
    f"| {_FP_AUDIO}"
    f"| {_FP_IMG_PREFIX}"
)


def extraction_fingerprint(kind: str) -> str:
    """per-file 抽取指纹：该文件切片所依赖的算法/模型版本。

    与全局 FINGERPRINT 的区别：按文件类型裁剪——文本文件不受视频/音频
    算法升级影响，图片不受音频算法影响。incremental 索引用它逐文件判定
    是否需要重索（files.extraction != 本值 → 该文件重索）。
    """
    core = ("embeddinggemma-2-onnx-int8"
            "| prompt-title-v1| haswords-v1"
            f"| chunk{CHUNK_MAX_TOKENS}/ov{CHUNK_OVERLAP_TOKENS}"
            f"| {_FP_IMG_PREFIX}")
    if kind in ("video",):
        return core + f"| frame{VIDEO_FRAME_TOKENS}/{_FP_FRAME}| {_FP_AUDIO}"
    if kind == "audio":
        return core + f"| {_FP_AUDIO}"
    if kind == "image":
        return core + "| ocr-v1"
    if kind == "code":
        # code 与 text 切片算法一致，但 modality/file_type 不同——独立指纹
        # 让旧库中按 text 索引的代码文件在增量任务里自动重索为 code。
        return core + "| code-v1"
    return core

# ---- 索引排除规则（PRD 4.1 隐私硬排除）----
OCR_ENABLED = True           # 图片/截图 OCR 文字检索（失败自动降级跳过）
OCR_MAX_CHARS = 4000         # 单图 OCR 文本上限（防整页扫描件炸 FTS）
EXCLUDE_DIR_NAMES = {
    ".ssh", ".gnupg", "node_modules", ".git", "__pycache__",
    "AppData", "$RECYCLE.BIN", "System Volume Information",
    ".cache", "Cache", "Caches",
}
EXCLUDE_FILE_SUFFIXES = {
    ".key", ".pem", ".pfx", ".p12", ".crt", ".keystore",
    ".kdbx", ".log", ".tmp", ".bak",
}
EXCLUDE_FILE_NAMES = {"credentials", "passwords", "secrets", ".env", "id_rsa", "id_ed25519"}

# ---- 文本类可索引扩展名（其余类型走对应 reader）----
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".html", ".htm", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
}
# 源代码文件：独立 file_type="code"，查询侧用 code retrieval 任务前缀
# （embed_query_code）。数据/配置格式（json/yaml/html 等）仍归 text。
CODE_EXTS = {
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".groovy",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh",
    ".cs", ".go", ".rs", ".swift", ".m", ".mm",
    ".rb", ".php", ".pl", ".pm", ".lua", ".r", ".dart",
    ".ex", ".exs", ".erl", ".hrl", ".hs", ".ml", ".fs", ".clj", ".lisp",
    ".vue", ".svelte",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd",
    ".css", ".scss", ".less", ".sass",
    ".asm", ".s", ".vb", ".pas", ".f", ".f90", ".cmake", ".gradle",
}
DOC_EXTS = {".docx", ".odt", ".rtf"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".tif", ".tiff"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v"}
