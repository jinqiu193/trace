# Trace — 本地文件夹语义检索（P1：图片 + OCR + GPU）

选文件夹 → 索引 → 用自然语言搜内容（文本 / PDF / 图片 / 截图，中英混合）。
基于 Google DeepMind **EmbeddingGemma 2**（onnx-community int8 ONNX 导出，
313MB 文本 + 195MB 视觉编码器，本地推理）。

```
E:/搜索embading/trace
├── trace/                  # 包源码
│   ├── config.py           #   路径/模型参数/任务前缀/切片参数/索引指纹/排除规则
│   ├── embedder.py         #   ONNX Runtime 推理（文本 CPU / 视觉 CUDA，DLL 自注册）
│   ├── vision.py           #   Gemma4 图片预处理（patchify 2520×768 + position_ids）
│   ├── ocr.py              #   rapidocr 截图文字提取（只进 FTS，不进向量）
│   ├── readers.py          #   txt/md/docx/odt/rtf/html/pdf 解析
│   ├── slicer.py           #   480 token 贪心装包 + 段落重叠
│   ├── searchtext.py       #   NFKC 归一化/jieba 分词/停用词/hasWords/lookup 检测
│   ├── store.py            #   单一 SQLite：files + slices + FTS5(trigram) + sqlite-vec
│   ├── indexer.py          #   遍历→解析→切片→向量化→入库（增量 + 指纹 + 单文件容错）
│   ├── search.py           #   z-score 模态归一化 + coverage 融合检索（DigUp 内核）
│   ├── evals.py            #   46 条内置评测（hit@3 / MRR，按类别分解）
│   └── cli.py              #   index/search/status/dirs/forget/evals/doctor
├── models/                 # model_quantized.onnx + vision_encoder_quantized.onnx + tokenizer
├── tests/make_testbed.py   # 生成 14 个文本测试文件（长文档深埋/纯数字陷阱/PDF）
├── tests/make_screenshots.py # 生成 6 张 DigUp 同主题文字截图
└── run.py                  # 入口
```

## 快速开始

```sh
# 1. 建环境（一次性）
C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe -m venv .venv
.venv/Scripts/python.exe -m pip install "onnxruntime-gpu==1.23.2" \
    nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cuda-nvrtc-cu12 \
    tokenizers numpy sqlite-vec pillow pypdf jieba reportlab rapidocr-onnxruntime

# 2. 模型（一次性，hf-mirror）
#   models/model_quantized.onnx            + model_quantized.onnx_data（313MB）
#   models/vision_encoder_quantized.onnx   + .onnx_data（195MB）
#   models/tokenizer.json / config.json / processor_config.json

# 3. 索引与检索
.venv/Scripts/python.exe run.py index <你的文件夹>
.venv/Scripts/python.exe run.py search "a penguin"
.venv/Scripts/python.exe run.py status
.venv/Scripts/python.exe run.py evals     # 46 条内置评测
```

## 桌面端（Tauri 2 + React，按 PRD v1.0）

PRD v1.0 桌面端走 Tauri + React（详见 `PRD`：5.2 技术选型 / FR-6 / FR-7）。本仓库 `app/` 是
最小落地：Rust 主进程启动 Python 边车（`python -m trace.webserver`），React WebView 通过
`http://127.0.0.1:<随机端口>/api/...` 复用现有 trace 全部能力，CLI（`run.py`）零回归。

```sh
# 0. 装好上面 CLI 那套（venv + 模型）

# 1. 装依赖
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]"

# 2. 装 Rust 工具链（一次性，约 5–10 min）
curl -L -o /tmp/rustup-init.exe https://win.rustup.rs/x86_64
/tmp/rustup-init.exe -y --default-toolchain stable --profile minimal

# 3. dev 模式（首次会拉所有 Rust crate + 编译，约 5–10 min）
cd app
pnpm install
pnpm tauri dev

# 4. 出安装包（出 .msi + .exe）
pnpm tauri build
```

PRD 本期范围（实现到 `app/`）：

- FR-1 索引范围管理（首启选目录、增删、状态徽章）
- FR-6 全局搜索面板（防抖 100ms、↑↓ Enter 键盘、命中打分）
- FR-7 筛选（文件类型、目录子串、相似度阈值滑块）— 简化版
- FR-5 精准定位打开（走系统默认应用）

未做（后续 milestone）：FR-6 全局热键（`tauri-plugin-global-shortcut`）、
FR-8 代码搜索、FR-9 增量监听、P4 打包签名更新。

> **音视频已部分接入**（2026-10-14）：
> - 视频：opencv-python 抽帧（每 3s 一帧 + 镜头切换点），每帧一张视觉切片，
>   `location` 字段为 `HH:MM:SS` 时间戳。完整 1 小时视频默认产出 200 帧（防爆）。
> - 音频：仅登记元数据，未切片（编码器未就绪）。
> - 视频切片不带文本/关键词，纯靠视觉向量参与检索；FTS 关键词列写文件名供回退。

## 架构（对照 PRD 第五章 / DigUp）

单一 SQLite（`trace.db`）= files + slices + FTS5(trigram 关键词) + sqlite-vec(cosine)。

检索三层（DigUp 内核移植）：

1. **语义通道**：全量 cosine → 按 modality 分组去均值 → 全体 z-score（zGate 2.0）。
   模态间天然分差（文本 0.75–0.86 vs 图像音频 0.67–0.78），不归一化则文档永远
   压过图片
2. **关键词通道**：逐词 FTS（bm25 文件名列权重 3.0）+ 中文 2 字短词 LIKE 兜底；
   coverage = 查询词命中比例（≥0.5 候选）
3. **融合**：`1·max(z,0) + 2·coverage^1.5 + kindHint(+1)`；lookup 查询（纯编码/
   数字）语义权 0.5、词权翻倍；strongCount 0.5 折叠弱匹配

索引侧：标题 prompt（`title: <文件名> | text:`）、hasWords 过滤（纯数字文本只进
FTS 不进向量）、FINGERPRINT 变化检测（防模型升级混库）、WAL、单文件容错。

## 多模态

- **图片**（Gemma4 口径）：保持宽高比 resize（2520 patch 上限，边长对齐 48 的
  整数倍）→ ×1/255 → patchify 16×16×3 → 视觉编码器 → image_features [N,512] 喂
  主模型；序列 `<bos> <|image> <|image|>×N <image|> <eos>`（无 task 前缀，
  DigUp/mtmd 同构）
- **截图 OCR**：rapidocr 提取文字进 keywords 通道（DigUp 实测教训：OCR 文本
  embedding 后与查询相关性差，混入向量会污染排序；只做关键词）
- **音频/视频**：编码器已下载（audio_encoder_quantized.onnx 340MB），
  30 秒滑窗/抽帧切片策略已定，下期接入

## GPU（RTX 3070 Laptop，驱动 576.28）

| 组件 | provider | 实测 |
|---|---|---|
| 视觉编码器 | CUDA | **0.5s/图**（CPU 约 5s，近 10 倍） |
| 文本主模型 | CPU | 0.3s/query |

两条硬约束（2026-10-14 实测，README 留存备查）：

1. **DLL 注册**：onnxruntime-gpu pip 包不自带 CUDA 运行库，且 ORT 的 provider
   bridge 只认 PATH——`os.add_dll_directory` 对它无效，必须
   `SetEnvironmentVariableW('PATH', nvidia_dirs)`（embedder 已实现，自适应
   cu12/cu13 布局）
2. **文本模型 CUDA 受阻**：ORT 1.23.2 的 CUDA EP 在该模型
   `/model/layers.0/attn/scores/Reshape` 读到未初始化 shape 常量（垃圾值每次
   运行都不同，禁图优化无效）；ORT 1.31 需 CUDA 13 而驱动最高 12.9（DLL 初始化
   失败 WinError 1114）；ORT ≤1.22 不认 GatherBlockQuantized 新算子。
   解法：驱动升级到 ≥580 后换 ORT 1.31，或等 ORT 修复

## 实测基准（2026-10-14）

- **评测 hit@3 = 46/46，MRR = 0.946**：doc 10 / exact 7 / long 5 / pdf 3 /
  zh 6 / image 9 / screenshot 6 全满分；MRR 未满分的 4 条均为"截图 vs 同内容
  文本"的合理竞争（如 `K7Q2LM` 查询 OCR 截图排第 1）
- 查询响应 560–640ms（文本 query 0.3s + 全量 cosine 扫描）
- 索引：29 文件（14 文本 + 15 图）140s（CPU 视觉编码器时期；GPU 后约 40s）
- 语义区分度：首位 cos 0.72–0.81，次位 0.52–0.64

## 与 DigUp 的差异（参考实现：ARahim3/DigUp，macOS Swift + llama.cpp）

| | DigUp | Trace |
|---|---|---|
| 运行时 | llama.cpp (Metal) | ONNX Runtime（本机无 Rust 工具链，GitHub 不通） |
| 中文分词 | 不切词（README 自认短板） | jieba |
| 2 字中文词 | trigram 匹配不到 | LIKE 兜底通道 |
| 图片 prompt | mtmd marker | 同构特殊 token 序列（已验证 cos 区分度） |

## 已知边界

- 文本 query 0.3s 超 PRD 0.2s 目标（GPU 修复后预期进 50ms 级）
- 音频/视频：视频帧切片（见上）；音频元数据占位，未切片；HEIC 图片需 pillow-heif
- OCR 为 rapidocr（中英），纯英文场景可换 PaddleOCR 提升生僻字
