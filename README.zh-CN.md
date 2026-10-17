# Trace — 基于 Google EmbeddingGemma 2 的本地多模态语义检索

[English](README.md) | **简体中文**

[![GitHub Release](https://img.shields.io/github/v/release/jinqiu193/trace)](https://github.com/jinqiu193/trace/releases)
[![License: MIT](https://img.shields.io/github/license/jinqiu193/trace)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/jinqiu193/trace/total)](https://github.com/jinqiu193/trace/releases)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4?logo=windows&logoColor=white)](#快速开始)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](#快速开始)
[![Powered by EmbeddingGemma 2](https://img.shields.io/badge/powered%20by-EmbeddingGemma%202-4285F4)](#)
[![100% Offline](https://img.shields.io/badge/100%25-offline-2ea44f)](#)
[![评测 hit@3 46/46](https://img.shields.io/badge/eval-hit%403%2046%2F46-success)](#实测基准2026-10-14rtx-3070-laptop)

选文件夹 → 索引 → 用自然语言搜**内容**（文本 / PDF / 图片 / 截图 / 视频，中英混合）。**全本地、全离线，数据不出机器。**

Trace 是一个完全本地的文件语义搜索引擎，基于 Google DeepMind 的 **[EmbeddingGemma 2](https://developers.googleblog.com/en/embedding-gemma/)** 嵌入模型（onnx-community int8 ONNX 导出：313MB 文本模型 + 195MB 视觉编码器），通过 ONNX Runtime 本地推理。无需联网、无需 API Key。

## 核心特性

- **由 Google EmbeddingGemma 2 驱动**——SigLIP 风格视觉编码器 + 文本解码器的紧凑多模态嵌入模型，本地 int8 量化推理
- **真正的内容搜索**——搜的是文件*里面*的东西，而不是文件名：`txt / md / docx / odt / rtf / html / PDF`、图片、截图、视频
- **中英混合查询**——jieba 分词 + trigram FTS，补齐其他工具在中文场景的短板
- **一次查询跨模态检索**——文档、照片、截图（OCR 关键词）、视频片段（按帧、带时间戳）在同一排序中竞争
- **整个索引就是一个 SQLite 文件**——`trace.db` = files 表 + slices 表 + FTS5(trigram BM25) + sqlite-vec(cosine)
- **融合排序**——语义分按模态 z-score 归一化后，与关键词 coverage 融合（DigUp 内核移植）
- **消费级 GPU 上很快**——RTX 3070 Laptop 视觉编码 0.5s/图（约为 CPU 的 10 倍），查询端到端 0.6s

## 工作原理

```
文件夹 ──索引──► 解析（readers / OCR / 抽帧）
                  │
                  ├─► 切片（480 token 贪心装包 + 段落重叠）
                  ├─► 文本向量 ── EmbeddingGemma 2 文本模型（CPU，int8 ONNX）
                  ├─► 图片向量 ── EmbeddingGemma 2 视觉编码器（CUDA，int8 ONNX）
                  └─► OCR 关键词 ── rapidocr（截图；只进 FTS）
                  │
                  ▼
      单一 SQLite（trace.db）：files + slices + FTS5(trigram) + sqlite-vec
                  │
                  ▼ 检索 ──► 1. 语义通道：cosine → 按模态去均值 → 全体 z-score
                  │             （zGate 2.0）
                  │          2. 关键词通道：逐词 FTS（bm25，文件名权重 3.0）
                  │             + 中文 2 字短词 LIKE 兜底
                  │          3. 融合：1·max(z,0) + 2·coverage^1.5 + kindHint
                  ▼
            排序结果 + 定位打开
```

为什么要按模态归一化？文本相似度天然在 0.75–0.86，图像在 0.67–0.78——不归一化的话文档永远压过图片。Trace 还使用 `title: <文件名> | text:` 标题前缀，以及 hasWords 过滤（纯数字文本只进 FTS 不进向量），保持嵌入空间干净。

## 多模态细节

- **图片**（Gemma4 口径）：保持宽高比 resize（2520 patch 上限，边长对齐 48 的整数倍）→ ×1/255 → patchify 16×16×3 → 视觉编码器 → `image_features [N,512]` 喂主模型；序列 `<bos> <|image> <|image|>×N <image|> <eos>`（无 task 前缀）
- **截图 OCR**：rapidocr（中英）提取文字，只进关键词通道。实测教训：OCR 文本 embedding 后与查询相关性差，混入向量会污染排序，所以只做 FTS 关键词
- **视频**：opencv 抽帧（每 3s 一帧 + 镜头切换点，每小时上限 200 帧防爆），每帧一个视觉切片，`location` 字段为 `HH:MM:SS` 时间戳
- **音频**：编码器已下载（audio_encoder_quantized.onnx 340MB），30 秒滑窗切片策略已定，待接入；HEIC 图片需 pillow-heif

## 快速开始

环境要求：Windows，Python 3.10+；NVIDIA GPU 可选（图片索引强烈建议）。

```sh
# 1. 建环境（一次性）
python -m venv .venv
.venv/Scripts/python.exe -m pip install "onnxruntime-gpu==1.23.2" \
    nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cuda-nvrtc-cu12 \
    tokenizers numpy sqlite-vec pillow pypdf jieba reportlab rapidocr-onnxruntime

# 2. 模型（一次性）——EmbeddingGemma 2 int8 ONNX（Hugging Face 或 hf-mirror.com）
#   models/model_quantized.onnx            + model_quantized.onnx_data（313MB）
#   models/vision_encoder_quantized.onnx   + .onnx_data（195MB）
#   models/tokenizer.json / config.json / processor_config.json

# 3. 索引与检索
.venv/Scripts/python.exe run.py index <你的文件夹>
.venv/Scripts/python.exe run.py search "a penguin"
.venv/Scripts/python.exe run.py status
.venv/Scripts/python.exe run.py evals     # 46 条内置评测
```

CLI 命令：`index / search / status / dirs / forget / evals / doctor`（见 `trace/cli.py`）。

## 桌面端（Tauri 2 + React）

`app/` 是最小桌面壳：Rust 主进程启动 Python 边车（`python -m trace.webserver`，FastAPI），React WebView 通过 `http://127.0.0.1:<随机端口>/api/...` 复用 Trace 全部能力，CLI 零回归。

```sh
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]"

cd app
pnpm install
pnpm tauri dev      # 开发模式
pnpm tauri build    # 出 .msi + NSIS .exe 安装包
```

已实现：索引范围管理（FR-1）、全局搜索面板（100ms 防抖 + 键盘导航，FR-6）、筛选（文件类型/目录/相似度阈值，FR-7）、系统默认应用精准定位打开（FR-5）。全局热键、代码搜索、文件监听在路线图中。

## 实测基准（2026-10-14，RTX 3070 Laptop）

| 指标 | 结果 |
|---|---|
| 内置评测 | **hit@3 = 46/46，MRR = 0.946**（doc 10 / exact 7 / long 5 / pdf 3 / zh 6 / image 9 / screenshot 6） |
| 查询响应 | 560–640ms 端到端（文本 query 0.3s + 全量 cosine 扫描） |
| 视觉编码 | CUDA 0.5s/图（CPU 约 5s） |
| 索引速度 | 29 文件（14 文本 + 15 图）GPU 约 40s |

首位 cos 0.72–0.81 vs 次位 0.52–0.64，语义区分度健康。

## GPU 说明（RTX 3070 Laptop，驱动 576.28）

| 组件 | provider | 实测 |
|---|---|---|
| 视觉编码器 | CUDA | **0.5s/图**（CPU 约 5s，近 10 倍） |
| 文本主模型 | CPU | 0.3s/query |

两条硬约束（留档备查）：

1. **DLL 注册**：onnxruntime-gpu pip 包不自带 CUDA 运行库，且 ORT 的 provider bridge 只认 PATH——`os.add_dll_directory` 对它无效，必须 `SetEnvironmentVariableW('PATH', nvidia_dirs)`（embedder 已实现，自适应 cu12/cu13 布局）
2. **文本模型 CUDA 受阻**：ORT 1.23.2 的 CUDA EP 在该模型 `/model/layers.0/attn/scores/Reshape` 读到未初始化 shape 常量；ORT 1.31 需 CUDA 13 而驱动最高 12.9；ORT ≤1.22 不认 GatherBlockQuantized 新算子。解法：驱动升级到 ≥580 后换 ORT 1.31，或等 ORT 修复

## 项目结构

```
trace/                    # Python 包
├── config.py             #   路径/模型参数/任务前缀/切片参数/索引指纹
├── embedder.py           #   ONNX Runtime 推理（文本 CPU / 视觉 CUDA）
├── vision.py             #   Gemma4 图片预处理（patchify + position_ids）
├── ocr.py                #   rapidocr 截图文字提取
├── readers.py            #   txt/md/docx/odt/rtf/html/pdf 解析
├── slicer.py             #   480 token 贪心装包 + 段落重叠
├── searchtext.py         #   NFKC 归一化/jieba 分词/停用词
├── store.py              #   单一 SQLite：files + slices + FTS5 + sqlite-vec
├── indexer.py            #   遍历→解析→切片→向量化→入库（增量+指纹+容错）
├── search.py             #   z-score 模态归一化 + coverage 融合检索
├── evals.py              #   46 条内置评测（hit@3 / MRR）
├── webserver.py          #   桌面端 FastAPI 边车
└── cli.py                #   index/search/status/dirs/forget/evals/doctor
app/                      # Tauri 2 + React 桌面壳
models/                   # ONNX 模型（单独下载，已 gitignore）
run.py                    # CLI 入口
```

## 致谢与相关项目

- [EmbeddingGemma 2](https://developers.googleblog.com/en/embedding-gemma/) —— Google DeepMind 紧凑多模态嵌入模型；Trace 使用 onnx-community 的 int8 ONNX 导出
- [DigUp](https://github.com/ARahim3/DigUp) —— macOS/Swift 参考实现，Trace 移植其检索内核（并补上 jieba 分词、中文 2 字 LIKE 兜底通道、mtmd 同构图片 prompt）
- [sqlite-vec](https://github.com/asg017/sqlite-vec)、[FTS5](https://www.sqlite.org/fts5.html)、[jieba](https://github.com/fxsjy/jieba)、[rapidocr](https://github.com/RapidAI/RapidOCR)、[Tauri](https://tauri.app)

## 许可证

[MIT](LICENSE) © 2026 jinqiu193
