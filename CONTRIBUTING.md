# Contributing to Trace

Thanks for your interest in improving Trace! This document covers the development setup and contribution workflow. 中文版本见下方。

## English

### Development setup

1. Clone the repo and create a venv (Python 3.10+):

   ```sh
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -e ".[dev]"
   ```

2. Download the EmbeddingGemma 2 ONNX models into `models/` (see [README](README.md#quick-start)).
3. Install GPU runtime packages if you index images (CUDA section in README).
4. Verify the setup: `.venv/Scripts/python.exe run.py doctor`

### Running checks

```sh
.venv/Scripts/python.exe run.py evals     # 46 built-in retrieval evals (hit@3 / MRR)
python -m pytest                          # unit tests (tests/)
```

Any change touching `trace/search.py`, `trace/indexer.py`, `trace/embedder.py` or the ranking logic **must** keep the built-in evals green. If a change intentionally changes ranking, update the affected eval expectations and explain why in the PR.

### Project layout

- `trace/` — Python package: indexing, ONNX inference, storage, fusion search, FastAPI sidecar, CLI
- `app/` — Tauri 2 + React desktop shell
- `tests/` — pytest suite + testbed/screenshot generators

### Guidelines

- Keep PRs focused; one logical change per PR
- Minimal diffs, no unrelated reformatting
- The index database schema lives in `trace/store.py` — schema changes need a fingerprint bump in `trace/config.py`
- Never commit models (`*.onnx*`), databases (`*.db*`), `sidecar-dist/`, or personal files — they are gitignored
- Commit messages: short imperative summary, details in the body when needed

---

## 简体中文

### 开发环境

1. 克隆仓库并创建虚拟环境（Python 3.10+）：

   ```sh
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -e ".[dev]"
   ```

2. 下载 EmbeddingGemma 2 ONNX 模型到 `models/`（见 [README 中文版](README.zh-CN.md#快速开始)）。
3. 索引图片需要 GPU 运行库（见 README 的 GPU 说明）。
4. 验证环境：`.venv/Scripts/python.exe run.py doctor`

### 运行检查

```sh
.venv/Scripts/python.exe run.py evals     # 46 条内置检索评测（hit@3 / MRR）
python -m pytest                          # tests/ 下的单元测试
```

凡涉及 `trace/search.py`、`trace/indexer.py`、`trace/embedder.py` 或排序逻辑的改动，**必须**保证内置评测全绿。如果改动有意改变排序结果，请同步更新评测预期并在 PR 中说明原因。

### 项目结构

- `trace/` —— Python 包：索引、ONNX 推理、存储、融合检索、FastAPI 边车、CLI
- `app/` —— Tauri 2 + React 桌面壳
- `tests/` —— pytest 测试及测试数据生成脚本

### 规范

- 一个 PR 只做一件事，保持聚焦
- 最小 diff，不夹带无关重排
- 索引库 schema 在 `trace/store.py`——schema 变更必须同步提升 `trace/config.py` 的索引指纹
- 不要提交模型（`*.onnx*`）、数据库（`*.db*`）、`sidecar-dist/` 或个人文件（均已 gitignore）
- 提交信息：祈使句简短摘要，必要时正文展开

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
参与贡献即表示你同意贡献内容以 MIT 许可证发布。
