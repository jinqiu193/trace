# Trace — Local Multimodal Semantic Search, Powered by Google EmbeddingGemma 2

**English** | [简体中文](README.zh-CN.md)

[![GitHub Release](https://img.shields.io/github/v/release/jinqiu193/trace)](https://github.com/jinqiu193/trace/releases)
[![License: MIT](https://img.shields.io/github/license/jinqiu193/trace)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/jinqiu193/trace/total)](https://github.com/jinqiu193/trace/releases)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4?logo=windows&logoColor=white)](#quick-start)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](#quick-start)
[![Powered by EmbeddingGemma 2](https://img.shields.io/badge/powered%20by-EmbeddingGemma%202-4285F4)](#)
[![100% Offline](https://img.shields.io/badge/100%25-offline-2ea44f)](#)
[![Eval: hit@3 46/46](https://img.shields.io/badge/eval-hit%403%2046%2F46-success)](#benchmarks)

Pick a folder → index it → search its **content** in plain language — text documents, PDFs, images, screenshots, videos, audio and source code, with mixed Chinese/English queries. **100% local, 100% offline.**

Trace is a fully-local semantic search engine for your files, built on Google DeepMind's **[EmbeddingGemma 2](https://developers.googleblog.com/en/embedding-gemma/)** embedding model (int8-quantized ONNX export: 313 MB text model + 195 MB vision encoder). No cloud, no API keys, no data ever leaves your machine.

## Highlights

- **Powered by Google EmbeddingGemma 2** — state-of-the-art compact multimodal embeddings (SigLIP-style vision encoder + text decoder), running locally via ONNX Runtime
- **True content search** — searches what's *inside* your files, not just filenames: `txt / md / docx / odt / rtf / html / PDF`, images (incl. iPhone HEIC), screenshots, videos, audio, and source code
- **Mixed Chinese/English queries** — jieba tokenization + trigram FTS covers CJK edge cases that other tools miss
- **Multimodal retrieval in one query** — a single query ranks documents, photos, screenshots (with OCR keywords), video moments (frame-level, timestamped) and audio segments (30 s windows, timestamped) together
- **Code-aware search** — the `code:function or class` prefix routes queries through a code-retrieval task embedding and restricts results to source files; `name:keyword` gives Everything-style exact filename matching
- **Search by image / image+text / More like this** — drop an image anywhere or paste with Ctrl+V, optionally refine with extra words; every result offers one-click "find similar"
- **Engineered incremental indexing** — true batched inference (2.3× at B=8), content-hash dedup cloning (zero re-embedding for identical copies), per-file extraction fingerprints (algorithm upgrades re-index file-by-file), 60 s file-change polling
- **One SQLite file as the whole index** — `trace.db` = file table + slice table + FTS5 (trigram BM25) + sqlite-vec (cosine)
- **Fusion ranking** — semantic scores are z-score normalized per modality, then fused with keyword coverage (DigUp-style kernel)
- **Blazing on consumer GPUs** — vision encoding ~0.5 s/image on an RTX 3070 Laptop (~10× CPU); query latency ~0.6 s end-to-end

## How It Works

```
folder ──index──► parse (readers/OCR/frame sampling)
                    │
                    ├─► slices (480-token greedy packing + paragraph overlap)
                    ├─► text embeddings  ── EmbeddingGemma 2 text model (CPU, int8 ONNX, true-batch 2.3×)
                    ├─► image embeddings ── EmbeddingGemma 2 vision encoder (CUDA, int8 ONNX)
                    ├─► audio embeddings ── EmbeddingGemma 2 audio encoder (30 s windows, batched)
                    └─► OCR keywords ── rapidocr (screenshots; FTS only)
                    │
                    ▼
        single SQLite (trace.db): files + slices + FTS5(trigram) + sqlite-vec
                    │
                    ▼ search ──► 1. semantic channel: cosine → per-modality
                    │               de-meaning → global z-score (zGate 2.0)
                    │            2. keyword channel: per-word FTS (bm25, filename
                    │               weight 3.0) + 2-char CJK LIKE fallback
                    │            3. fusion: 1·max(z,0) + 2·coverage^1.5 + kindHint
                    ▼
             ranked results with previews
```

Why per-modality normalization? Text similarity naturally scores 0.75–0.86 while images score 0.67–0.78 — without z-score normalization, documents would always bury images. Trace also uses a `title: <filename> | text:` prompt prefix and a hasWords filter (numeric-only text goes to FTS, never to vectors) to keep the embedding space clean.

## Multimodal Details

- **Images** — aspect-preserving resize (≤2520 patches, side aligned to 48) → ×1/255 → 16×16×3 patchify → vision encoder → `image_features [N, 512]` fed to the main model, with the special-token sequence `<bos> <|image> <|image|>×N <image|> <eos>` (no task prefix)
- **Screenshots** — rapidocr (CJK + Latin) extracts text into the keyword channel only. Lesson learned the hard way: OCR text embedded as vectors pollutes ranking, so it feeds FTS keywords only
- **Video** — PyAV seek-based sampling: seeks to N target timestamps and grabs one frame each without decoding the whole file; the frame budget adapts to duration (5 for short clips, ~8 for 2 h, capped at 12) and keyframe-density weighting pulls samples toward scene-change hotspots (encoders insert I-frames at cuts; demux reads timestamps only, zero decoding), falling back to even spacing for constant-GOP files; frames are resized to 512 px on the long side and located by `HH:MM:SS` timestamps. The audio track is indexed alongside in 30 s windows
- **Audio** — 30 s sliding windows with a 25 s stride (5 s overlap so edge words aren't lost); window count adapts to duration (≥4 for short clips, ~6 for 2 h, capped at 10); when a target point is silent, Trace probes forward in 60 s steps for the nearest audible segment instead of dropping the window. Supports mp3 / wav / m4a / aac / flac / ogg / wma / opus
- **Image formats** — jpg / jpeg / png / webp / bmp / gif / tif / tiff / **heic** (pillow-heif registers into PIL, so iPhone photos index directly)

## Quick Start

Requirements: Windows, Python 3.10+, NVIDIA GPU optional but recommended for images.

```sh
# 1. Environment (once)
python -m venv .venv
.venv/Scripts/python.exe -m pip install "onnxruntime-gpu==1.23.2" \
    nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 \
    nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cuda-nvrtc-cu12 \
    tokenizers numpy sqlite-vec pillow pillow-heif pypdf jieba reportlab \
    rapidocr-onnxruntime av opencv-python

# 2. Models (once) — EmbeddingGemma 2 int8 ONNX from Hugging Face
#    (hf-mirror.com works too)
#   models/model_quantized.onnx            + model_quantized.onnx_data   (313 MB)
#   models/vision_encoder_quantized.onnx   + .onnx_data                  (195 MB)
#   models/onnx/audio_encoder_quantized.onnx + .onnx_data                (340 MB, for audio)
#   models/tokenizer.json / config.json / processor_config.json

# 3. Index & search
.venv/Scripts/python.exe run.py index <your-folder>
.venv/Scripts/python.exe run.py search "a penguin"
.venv/Scripts/python.exe run.py status
.venv/Scripts/python.exe run.py evals     # 46 built-in eval cases
```

CLI commands: `index / search / status / dirs / forget / evals / doctor` (see `trace/cli.py`).

## Desktop App (Tauri 2 + React)

`app/` is a minimal desktop shell: the Rust main process launches the Python sidecar (`python -m trace.webserver`, FastAPI), and the React WebView talks to it via `http://127.0.0.1:<random-port>/api/...`, reusing all of Trace's capabilities with zero CLI regression.

```sh
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]"

cd app
pnpm install
pnpm tauri dev      # dev mode
pnpm tauri build    # produce .msi + NSIS .exe installers
```

Desktop features implemented: index-scope management (FR-1), global search panel with 100 ms debounce + keyboard navigation (FR-6), filters by file type / directory / mtime / similarity threshold (FR-7), open-in-system-app precise locating (FR-5), `name:` exact filename search, `code:` code-aware search, search-by-image (drag & drop / Ctrl+V paste / image+text refinement), More-like-this recommendations, in-app previews (video seek / image zoom & paging / text highlight / audio playback), duplicate-image detection, fast/detailed search modes, and 60 s file-change polling for incremental indexing. A global hotkey is on the roadmap.

## Benchmarks

Measured 2026-10-14, RTX 3070 Laptop GPU (driver 576.28):

| Metric | Result |
|---|---|
| Built-in evals | **hit@3 = 46/46, MRR = 0.946** (doc 10 / exact 7 / long 5 / pdf 3 / zh 6 / image 9 / screenshot 6) |
| Query latency | 560–640 ms end-to-end (text query 0.3 s + full cosine scan) |
| Vision encoding | 0.5 s/image on CUDA (≈5 s on CPU) |
| Text batch inference | true batching (padding+mask, startup self-check fallback), 2.3× at B=8 |
| Indexing | 29 files (14 text + 15 images) in ~40 s with GPU |

Top-1 cosine 0.72–0.81 vs top-2 0.52–0.64 — the semantic space has healthy separation. GPU notes and the CUDA-EP caveat for the text model (ORT 1.23.2 / driver constraints) are documented in the [Chinese README](README.zh-CN.md#gpu).

## Project Layout

```
trace/                    # Python package
├── config.py             #   paths / model params / task prefixes / fingerprints
├── embedder.py           #   ONNX Runtime inference (text CPU / vision CUDA)
├── vision.py             #   Gemma4 image preprocessing (patchify + position_ids)
├── ocr.py                #   rapidocr screenshot text extraction
├── readers.py            #   txt/md/code/docx/odt/rtf/html/pdf parsers + video frames + audio windows
├── slicer.py             #   480-token greedy packing + paragraph overlap
├── searchtext.py         #   NFKC normalization / jieba / stopwords
├── store.py              #   single SQLite: files + slices + FTS5 + sqlite-vec
├── indexer.py            #   walk → parse → slice → embed → store (incremental)
├── search.py             #   z-score fusion retrieval kernel
├── evals.py              #   46 built-in evals (hit@3 / MRR)
├── webserver.py          #   FastAPI sidecar for the desktop app
└── cli.py                #   index/search/status/dirs/forget/evals/doctor
app/                      # Tauri 2 + React desktop shell
models/                   # ONNX models (downloaded separately, gitignored)
run.py                    # CLI entry
```

## Credits & Related

- [EmbeddingGemma 2](https://developers.googleblog.com/en/embedding-gemma/) — Google DeepMind's compact multimodal embedding model; Trace uses the onnx-community int8 ONNX export
- [DigUp](https://github.com/ARahim3/DigUp) — the macOS/Swift reference implementation whose retrieval kernel Trace ports (with jieba tokenization, a CJK 2-char LIKE fallback channel, and an mtmd-equivalent image prompt)
- [sqlite-vec](https://github.com/asg017/sqlite-vec), [FTS5](https://www.sqlite.org/fts5.html), [jieba](https://github.com/fxsjy/jieba), [rapidocr](https://github.com/RapidAI/RapidOCR), [Tauri](https://tauri.app)

## License

[MIT](LICENSE) © 2026 jinqiu193
