"""EmbeddingGemma 2 向量化层（ONNX Runtime）。

职责（对应 PRD FR-3）：
- 加载 int8 文本 ONNX 模型 + tokenizer.json（+ 可选视觉编码器）
- 查询加 "task: search result | query: " 前缀，文档加 "title: none | text: " 前缀
- 均值池化 + L2 归一化 → 768 维单位向量
- 图片模态：<bos> <|image> <|image|>×N <image|> <eos>（无 task 前缀，DigUp/mtmd 口径）

provider 策略（2026-10-14 实测）：
- 文本主模型：CPU（ORT 1.23.2 CUDA EP 在该模型 attention Reshape 节点读到
  未初始化 shape 常量，必崩；ORT 1.22 不认 GatherBlockQuantized 新算子）
- 视觉编码器：CUDA 优先，失败回退 CPU（0.5s vs 5s）

输入/输出签名在初始化时自省，兼容 optimum 导出的两种形态：
- 输出含 text_embeds / sentence_embedding → 直接使用
- 仅 last_hidden_state → 按 attention_mask 均值池化
"""

from __future__ import annotations

import threading

import numpy as np

from . import config


def _register_cuda_dlls() -> None:
    """Windows：注册 pip nvidia-* 包的 DLL 目录（ORT CUDA EP 需要）。

    onnxruntime-gpu pip 包只带 onnxruntime_providers_cuda.dll，CUDA runtime /
    cuDNN / cuBLAS 等需从 extras 安装，且必须在 import onnxruntime **之前**
    用 add_dll_directory 注册，否则 CUDAExecutionProvider 静默回退 CPU。

    布局随版本变化（cu12: nvidia/cublas/bin；cu13: nvidia/cu13/bin/x86_64），
    这里自适应扫描 nvidia/*/bin 与 nvidia/*/bin/x86_64。

    两种手段并用（2026-10 实测）：
    - os.add_dll_directory：对 Python/ctypes 的加载有效
    - SetEnvironmentVariableW('PATH', ...)：ORT 的 provider bridge 用
      LoadLibrary 标准搜索，只认 PATH，add_dll_directory 对它无效——
      少了这一步 CUDAExecutionProvider 会静默回退 CPU

    版本匹配：驱动 576.28（CUDA 12.9）需 ORT ≤1.23.2 + nvidia-*-cu12；
    ORT 1.31 的 CUDA EP 编译于 CUDA 13.x，DLL 初始化直接失败
    （WinError 1114），除非驱动升级到 ≥580。
    """
    import glob
    import os
    import sys

    if sys.platform != "win32" or os.environ.get("TRACE_NO_CUDA_DLLS"):
        return
    try:
        roots = {sys.prefix, getattr(sys, "base_prefix", sys.prefix)}
        try:
            import site

            roots.update(site.getsitepackages())
        except Exception:
            pass
        dirs: list[str] = []
        for root in roots:
            for pattern in ("Lib/site-packages/nvidia/*/bin",
                            "Lib/site-packages/nvidia/*/bin/x86_64",
                            "lib/site-packages/nvidia/*/bin",
                            "nvidia/*/bin"):
                for p in sorted(glob.glob(os.path.join(root, pattern))):
                    if os.path.isdir(p) and p not in dirs:
                        dirs.append(p)
        for p in dirs:
            os.add_dll_directory(p)
        if dirs:
            import ctypes

            cur = os.environ.get("PATH", "")
            ctypes.windll.kernel32.SetEnvironmentVariableW(
                "PATH", os.pathsep.join(dirs + [cur]))
            # cuDNN 9 子库（cudnn_engines_precompiled64_9.dll 等）在 Conv 节点
            # 首次运行时才 lazy 加载，走 LoadLibrary 传统搜索——既不认
            # add_dll_directory，也找不到上面刚改的 PATH（2026-10-17 实测：
            # 注册后 Conv 仍报 "Could not locate cudnn_engines_precompiled64_9.dll"
            # 且整个 session 静默 fallback CPU）。预加载全家 dll 进进程后，
            # 后续同 dll 的 LoadLibrary 直接命中已加载模块。
            for p in dirs:
                for dll in sorted(glob.glob(os.path.join(p, "cudnn*.dll"))):
                    try:
                        ctypes.CDLL(dll)
                    except OSError:
                        pass
    except Exception:
        pass


class Embedder:
    def __init__(self, model_path=None, tokenizer_path=None, prefer_gpu: bool = True,
                 with_vision: bool = True):
        _register_cuda_dlls()
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path = model_path or config.MODEL_ONNX
        tokenizer_path = tokenizer_path or config.TOKENIZER_JSON

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # 文本主模型固定 CPU：ORT 1.23.2 的 CUDA EP 在该模型
        # /model/layers.0/attn/scores/Reshape 节点读到未初始化 shape 常量
        # （垃圾值 0x1318xxxx，每次运行都不同，图优化开关无效），
        # 待 ORT 修复或驱动升级 CUDA 13 后再切回。
        # 视觉编码器无此问题，走 CUDA（0.5s vs CPU 5s，近 10 倍）。
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.provider = self.session.get_providers()[0]

        # 视觉编码器（可选加载：文本检索不依赖它）
        self.vision_session = None
        if with_vision and config.VISION_ONNX.exists():
            try:
                self.vision_session = ort.InferenceSession(
                    str(config.VISION_ONNX), so,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
                # 预热：第一次跑真实帧时偶发 cudnn_ops64_9.dll 加载失败
                # （驱动 576.28 + ORT 1.23.2 + cu13 组合下，CUDA context 冷启
                # 期间 cudnnCreate* 调用句柄失效；先喂一个零张量让 cuDNN 完成
                # 懒加载 + 内核编译缓存，后续 embed_image_pil 就能稳定命中）
                try:
                    self.vision_session.run(
                        None,
                        {"pixel_values": np.zeros((1, 2520, 768), dtype=np.float32),
                         "pixel_position_ids": np.zeros((1, 2520, 2), dtype=np.int64)},
                    )
                except Exception:
                    pass
            except Exception:
                self.vision_session = ort.InferenceSession(
                    str(config.VISION_ONNX), so, providers=["CPUExecutionProvider"])

        # 音频编码器（按需加载 + 懒下载）
        self.audio_session = None
        self._audio_lock = threading.Lock()
        if with_vision:  # 复用 with_vision 标志：表示"多模态索引模式"
            self._maybe_load_audio_session(so)

        self.tok = Tokenizer.from_file(str(tokenizer_path))
        self.tok.enable_truncation(config.MAX_TOKENS)

        self.input_names = [i.name for i in self.session.get_inputs()]
        self.output_names = [o.name for o in self.session.get_outputs()]
        # 池化输出名（若导出端已做池化）
        self._pooled_out = next(
            (n for n in self.output_names if n in ("text_embeds", "sentence_embedding", "pooler_output")),
            None,
        )
        self._hidden_out = next(
            (n for n in self.output_names if "last_hidden" in n or "hidden_states" in n),
            self.output_names[0] if self.output_names else None,
        )
        # 批量推理状态：None=未测，True=可用，False=模型不支持（回退逐条）
        self._batch_ok: bool | None = None

    # ------------------------------------------------------------------ #
    def _run(self, text: str, prefix: str) -> np.ndarray:
        enc = self.tok.encode(prefix + text, add_special_tokens=True)
        ids = np.asarray([enc.ids], dtype=np.int64)
        mask = np.asarray([enc.attention_mask], dtype=np.int64)
        return self._infer(ids, mask)[0]

    def _infer(self, ids: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """[n, seq] ids/mask → [n, dim] L2 归一化向量（n=1 即单条）。"""
        # 多模态特征外挂输入（vision/audio 编码器产出，文本推理喂空张量）
        empty_feat = {
            "image_features": np.zeros((0, 512), dtype=np.float32),
            "video_features": np.zeros((0, 512), dtype=np.float32),
            "audio_features": np.zeros((0, 512), dtype=np.float32),
        }
        feeds = {}
        for name in self.input_names:
            if name in ("input_ids", "ids"):
                feeds[name] = ids
            elif name in ("attention_mask", "mask"):
                feeds[name] = mask
            elif name == "token_type_ids":
                feeds[name] = np.zeros_like(ids)
            elif name == "position_ids":
                feeds[name] = np.cumsum(mask, axis=1) - 1
            elif name in empty_feat:
                feeds[name] = empty_feat[name]
            # 其余输入缺省不喂，由 ONNX 默认值/可选输入处理
        outs = self.session.run(None, feeds)
        named = dict(zip(self.output_names, outs))

        if self._pooled_out is not None:
            vecs = np.asarray(named[self._pooled_out], dtype=np.float32)
            vecs = vecs.reshape(vecs.shape[0], -1)
        else:
            hidden = np.asarray(named[self._hidden_out], dtype=np.float32)  # [n, seq, hidden]
            m = mask.astype(np.float32)[:, :, None]
            vecs = (hidden * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1.0)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.maximum(norms, 1e-9)
        return vecs.astype(np.float32)

    def _run_batch(self, texts: list[str], prefixes: list[str]) -> np.ndarray:
        """批量推理：pad 到批内最长序列，一次 session.run 吞 EMBED_BATCH 条。

        2026-10-16 前的"批量"只是 Python 层 for 循环逐条推理，吞吐被单条
        延迟锁死；这里是真 batch（padding 位置 attention_mask=0，池化时
        权重为零，数值上与逐条等价——由 _selftest_batch 启动校验兜底）。
        """
        n = len(texts)
        if n == 0:
            return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
        encs = [self.tok.encode(p + t, add_special_tokens=True)
                for p, t in zip(prefixes, texts)]
        maxlen = max(len(e.ids) for e in encs)
        ids = np.zeros((n, maxlen), dtype=np.int64)
        mask = np.zeros((n, maxlen), dtype=np.int64)
        for i, e in enumerate(encs):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = e.attention_mask
        return self._infer(ids, mask)

    def _selftest_batch(self) -> bool:
        """批量自检：同一条文本在 batch 内出现两次 + 与单条对比，cos ≥ 0.999
        才启用批量路径。模型导出若固定了 batch=1 或池化口径异常，这里会
        立即发现并永久回退逐条（正确性优先于吞吐）。"""
        probe = "trace batch selftest probe text 睡眠航海日志"
        prefix = config.DOC_PREFIX
        try:
            batch = self._run_batch([probe, probe], [prefix, prefix])
            single = self._run(probe, prefix)
            ok = (batch.shape == (2, single.shape[0])
                  and float(np.dot(batch[0], single)) > 0.999
                  and float(np.dot(batch[1], single)) > 0.999)
        except Exception:
            ok = False
        self._batch_ok = ok
        if not ok:
            import logging
            logging.getLogger("trace.embedder").warning(
                "批量推理自检未通过，回退逐条推理（吞吐较低但结果一致）")
        return ok

    # ------------------------------------------------------------------ #
    def embed_query(self, text: str) -> np.ndarray:
        return self._run(text, config.QUERY_PREFIX)

    def embed_query_code(self, text: str) -> np.ndarray:
        return self._run(text, config.CODE_QUERY_PREFIX)

    def embed_document(self, title: str, text: str) -> np.ndarray:
        """单块文档嵌入（DigUp Prompts.document 口径）。

        模型卡文档 prompt 为 "title: <标题> | text: <正文>"——标题给模型
        内容线索（"sailing_log" 的段落比无标题更容易被"航海日志查询"命中）。
        """
        t = (title or "").strip()
        prefix = f"title: {t if t else 'none'} | text: "
        return self._run(text, prefix)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """批量嵌入文档块（无标题），返回 [n, 768] f32。"""
        return self.embed_documents_titled([("", t) for t in texts])

    def embed_documents_titled(self, items: list[tuple[str, str]]) -> np.ndarray:
        """批量嵌入带标题的文档块 [(title, text), ...]，返回 [n, 768] f32。

        批量路径（EMBED_BATCH 条/次，自检通过时）；否则逐条（老路径）。
        """
        if not items:
            return np.zeros((0, config.EMBED_DIM), dtype=np.float32)
        if self._batch_ok is None:
            self._selftest_batch()
        prefixes = []
        for t, _ in items:
            t = (t or "").strip()
            prefixes.append(f"title: {t if t else 'none'} | text: ")
        if not self._batch_ok:
            return np.vstack([self._run(x, p) for p, (_, x) in zip(prefixes, items)])
        texts = [x for _, x in items]
        out = []
        for i in range(0, len(texts), config.EMBED_BATCH):
            out.append(self._run_batch(texts[i:i + config.EMBED_BATCH],
                                       prefixes[i:i + config.EMBED_BATCH]))
        return np.vstack(out).astype(np.float32)

    # ------------------------------------------------------------------ #
    # 图片模态（Gemma4 视觉编码器 → image_features → 主模型）
    # ------------------------------------------------------------------ #
    def _encode_vision_features(self, pv: np.ndarray, pos: np.ndarray) -> np.ndarray:
        """视觉编码器：pixel_values/pixel_position_ids → [N, 512] image_features。"""
        if self.vision_session is None:
            raise RuntimeError("视觉编码器未加载（models/vision_encoder_quantized.onnx 缺失）")
        feats = self.vision_session.run(
            None, {"pixel_values": pv[None], "pixel_position_ids": pos[None]}
        )[0]
        feats = np.asarray(feats, dtype=np.float32)
        if feats.ndim == 3:
            feats = feats[0]
        return feats

    def _run_with_image_features(self, feats: np.ndarray, suffix: str = "") -> np.ndarray:
        """image_features → 主模型 → 768 维向量。

        IMAGE_INDEX_PREFIX（config）为非空时，会在 BOI 前拼一段文本 token，
        把 image 嵌入"拉"到 text 一侧以缩小跨模态间隙。
        merge/cumsum 按 token id 定位，对前缀 token 不敏感。
        suffix：EOI 之后追加的查询文本（图+文联合查询，"像这张图但要…"），
        索引/纯图路径留空。
        """
        n = feats.shape[0]
        prefix = config.IMAGE_INDEX_PREFIX
        seq = prefix + config.BOI_TOKEN + config.IMAGE_TOKEN * n + config.EOI_TOKEN
        if suffix:
            seq = seq + suffix
        enc = self.tok.encode(seq, add_special_tokens=True)
        ids = np.asarray([enc.ids], dtype=np.int64)
        mask = np.asarray([enc.attention_mask], dtype=np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": mask,
            "image_features": feats,
            "video_features": np.zeros((0, 512), dtype=np.float32),
            "audio_features": np.zeros((0, 512), dtype=np.float32),
        }
        outs = self.session.run(None, feeds)
        named = dict(zip(self.output_names, outs))
        vec = np.asarray(named[self._pooled_out], dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)

    # ------------------------------------------------------------------ #
    # 批量前向（索引流水线专用）：主模型 CPU 批量实测 2.3x（B=8）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _audio_token_seq(n: int) -> str:
        prefix = config.IMAGE_INDEX_PREFIX  # 跨模态对齐（与 image 共用同一开关）
        BOA = "<|audio>"         # 256000
        AUDIO_T = "<|audio|>"    # 258881（占位，merge 按此 id 替换）
        EOA = "<audio|>"         # 258883
        return (prefix + BOA + AUDIO_T * n + EOA) if prefix else (BOA + AUDIO_T * n + EOA)

    def _run_pooled_many(self, modality: str, feats_list: list[np.ndarray]) -> list[np.ndarray]:
        """主模型批量前向：image_features / audio_features 列表 → 768 维向量列表。

        等长 token 序列分到同一批；不等长自动按长度分组（不 pad，
        保证输出与逐条前向数值一致，实测偏差 0.000000）。
        单批失败时该组逐条退化，不炸整个流水线。
        """
        if not feats_list:
            return []
        enc_list = []
        for feats in feats_list:
            n = feats.shape[0]
            if modality == "image":
                seq = (config.IMAGE_INDEX_PREFIX + config.BOI_TOKEN
                       + config.IMAGE_TOKEN * n + config.EOI_TOKEN)
            else:
                seq = self._audio_token_seq(n)
            enc_list.append(self.tok.encode(seq, add_special_tokens=True))

        out: list[np.ndarray] = [np.zeros(0, dtype=np.float32)] * len(feats_list)
        by_len: dict[int, list[int]] = {}
        for i, enc in enumerate(enc_list):
            by_len.setdefault(len(enc.ids), []).append(i)

        feats_key = "image_features" if modality == "image" else "audio_features"
        for _L, idxs in by_len.items():
            B = len(idxs)
            ids_b = np.zeros((B, _L), dtype=np.int64)
            mask_b = np.zeros((B, _L), dtype=np.int64)
            for r, i in enumerate(idxs):
                enc = enc_list[i]
                ids_b[r] = enc.ids
                mask_b[r] = enc.attention_mask
            feeds = {
                "input_ids": ids_b,
                "attention_mask": mask_b,
                feats_key: np.concatenate([feats_list[i] for i in idxs], axis=0).astype(np.float32),
            }
            for k in ("image_features", "video_features", "audio_features"):
                if k != feats_key:
                    feeds[k] = np.zeros((0, 512), dtype=np.float32)
            try:
                outs = self.session.run(None, feeds)
                named = dict(zip(self.output_names, outs))
                if self._pooled_out is None or self._pooled_out not in named:
                    raise RuntimeError("pooled output missing")
                vecs = np.asarray(named[self._pooled_out], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                for r, i in enumerate(idxs):
                    v = vecs[r].reshape(-1)
                    nv = np.linalg.norm(v)
                    out[i] = (v / nv).astype(np.float32) if nv > 0 else v.astype(np.float32)
            except Exception:
                for i in idxs:
                    out[i] = (self._run_with_image_features(feats_list[i]) if modality == "image"
                              else self._run_with_audio_features(feats_list[i]))
        return out

    def embed_images_pil_batch(self, imgs) -> list[np.ndarray]:
        """多帧批量嵌入：预处理 + vision 逐条（批量实测反而慢）+ 主模型批量。"""
        from . import vision
        feats_list = []
        for im in imgs:
            pv, pos, _ = vision.preprocess_pil(im)
            feats_list.append(self._encode_vision_features(pv, pos))
        return self._run_pooled_many("image", feats_list)

    def embed_audio_pcm_batch(self, pcms, sr: int = 16000) -> list:
        """多窗口音频批量嵌入；编码器未加载/特征失败的窗口返回 None。"""
        feats_list = self._encode_audio_features_batch(pcms, sr=sr)
        valid = [(i, f) for i, f in enumerate(feats_list) if f is not None]
        out: list = [None] * len(pcms)
        if valid:
            vecs = self._run_pooled_many("audio", [f for _, f in valid])
            for (i, _), v in zip(valid, vecs):
                out[i] = v
        return out

    def embed_image(self, path) -> np.ndarray:
        """图片嵌入：<bos> <|image> <|image|>×N <image|> <eos>（无 task 前缀）。

        与 DigUp/mtmd 口径一致（其对 mlx-vlm bf16 参考 cosine ≥ 0.999）。
        视觉编码器输出 [N, 512] 直接喂主模型 image_features 输入。
        """
        from . import vision
        pv, pos, _ = vision.preprocess(path)
        return self._run_with_image_features(self._encode_vision_features(pv, pos))

    def embed_image_pil(self, img) -> np.ndarray:
        """视频帧等"内存里"的 PIL.Image → 768 维向量。"""
        from . import vision
        pv, pos, _ = vision.preprocess_pil(img)
        return self._run_with_image_features(self._encode_vision_features(pv, pos))

    def embed_image_text_pil(self, img, text: str) -> np.ndarray:
        """图+文联合查询：图块在前、查询文字在 EOI 后（"像这张图，但要…"）。

        与索引口径一致（纯图 BOI..EOI 无文本前缀），文字只追加在尾部，
        不改变图片 token 的对齐方式，跨模态 gap 与纯图查询相同。
        """
        from . import vision
        pv, pos, _ = vision.preprocess_pil(img)
        return self._run_with_image_features(
            self._encode_vision_features(pv, pos), suffix=(text or "").strip())

    # ------------------------------------------------------------------ #
    # 音频模态（Gemma4 音频编码器 → audio_features → 主模型）
    # ------------------------------------------------------------------ #
    def _maybe_load_audio_session(self, so) -> None:
        """首次访问时加载 audio encoder；缺失则从 HF 镜像下载（国内可访问）。"""
        if self.audio_session is not None:
            return
        with self._audio_lock:
            if self.audio_session is not None:
                return
            import onnxruntime as ort
            if not config.AUDIO_ONNX.exists():
                ok = self._download_audio_encoder()
                if not ok:
                    return  # 留 None，embed_audio_pcm 走 fallback
            try:
                self.audio_session = ort.InferenceSession(
                    str(config.AUDIO_ONNX), so,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            except Exception:
                try:
                    self.audio_session = ort.InferenceSession(
                        str(config.AUDIO_ONNX), so,
                        providers=["CPUExecutionProvider"])
                except Exception:
                    self.audio_session = None

    def _download_audio_encoder(self) -> bool:
        """从 HF 镜像下载 audio_encoder_quantized.onnx + .data。"""
        import os
        try:
            from huggingface_hub import hf_hub_download
        except Exception:
            return False
        # 优先镜像，失败回退默认
        for endpoint in (config.HF_ENDPOINT, None):
            try:
                kwargs = {"repo_id": config.HF_REPO, "filename": config.AUDIO_FILE,
                          "revision": config.HF_REVISION, "local_dir": str(config.MODELS_DIR)}
                if endpoint:
                    kwargs["endpoint"] = endpoint
                p = hf_hub_download(**kwargs)
                if p and os.path.exists(p):
                    # audio 用 external data 格式：可能还有 .data 文件被同仓库下到同目录
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _pcm_to_mel(pcm_int16: np.ndarray, sr: int = 16000) -> np.ndarray:
        """int16 PCM → log-mel spectrogram (match preprocessor_config.json).

        配置：frame_length=320, hop_length=160, fft_length=512, n_mels=128,
        fmax=8000, fmin=0, preemphasis=0.0, htk_flavor=True.
        返回: [T, 128] float32。

        实现走 scipy.signal（librosa 在我们环境里 _soundfile 坏），
        mel 滤波器组用 HTK 公式手算（与 librosa htk=True 数值一致）。
        """
        if pcm_int16.size == 0:
            return np.zeros((0, 128), dtype=np.float32)
        from scipy.signal import stft
        x = pcm_int16.astype(np.float32) / 32768.0

        # STFT：win=320, hop=160, n_fft=512, hann 窗（HTK 默认）
        _, _, Z = stft(x, fs=sr, window="hann", nperseg=320, noverlap=160,
                       nfft=512, boundary=None, padded=False, axis=-1)
        # Z shape: [257, T] 复数
        power = (np.abs(Z) ** 2).astype(np.float32)  # [257, T]

        # HTK mel 滤波器组（128 阶，fmin=0, fmax=8000）
        fmin, fmax = 0.0, 8000.0
        n_mels = 128
        n_freqs = 257
        mel_min = 2595.0 * np.log10(1.0 + fmin / 700.0)
        mel_max = 2595.0 * np.log10(1.0 + fmax / 700.0)
        mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
        hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
        bin_freqs = np.linspace(0, sr / 2, n_freqs)  # 257 个频点

        # HTK 三角滤波器
        fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
        for m in range(n_mels):
            lo, mid, hi = hz_points[m], hz_points[m + 1], hz_points[m + 2]
            for k in range(n_freqs):
                f = bin_freqs[k]
                if f < lo or f > hi:
                    continue
                if f <= mid and mid > lo:
                    fb[m, k] = (f - lo) / (mid - lo)
                elif f > mid and hi > mid:
                    fb[m, k] = (hi - f) / (hi - mid)

        # log-mel
        mel = fb @ power  # [128, T]
        # 数值保护：避免 log(0)
        mel = np.maximum(mel, 1e-10)
        ref = float(mel.max())
        log_mel = 10.0 * np.log10(mel / ref)
        log_mel = np.maximum(log_mel, log_mel.max() - 80.0)  # top_db=80
        return log_mel.T.astype(np.float32)  # [T, 128]

    def _encode_audio_features(self, pcm_int16: np.ndarray, sr: int = 16000) -> np.ndarray | None:
        """音频窗口 → [N, 512] audio_features。

        ONNX 签名（onnx-community/embeddinggemma-2-ONNX，2026-10 实测）：
          in:  input_features        [B, T, 128] float32
               input_features_mask   [B, T] bool  （True=有效，False=padding）
          out: audio_features        [N, 512] float32
        """
        if self.audio_session is None:
            return None
        feats_in = self._pcm_to_mel(pcm_int16, sr=sr)  # [T, 128]
        if feats_in.size == 0:
            return None
        T = feats_in.shape[0]
        mask = np.ones((T,), dtype=bool)
        in_names = {i.name for i in self.audio_session.get_inputs()}
        feeds = {
            "input_features": feats_in[None].astype(np.float32),  # [1, T, 128]
        }
        if "input_features_mask" in in_names:
            feeds["input_features_mask"] = mask[None]  # [1, T]
        try:
            outs = self.audio_session.run(None, feeds)
        except Exception:
            return None
        feats = np.asarray(outs[0], dtype=np.float32)
        if feats.ndim == 3:
            feats = feats[0]
        if feats.ndim == 2 and feats.shape[-1] == 512:
            return feats
        return None  # 输出形状不匹配主模型期望

    def _encode_audio_features_batch(self, pcms, sr: int = 16000) -> list:
        """多窗口音频特征：等长 mel 窗合并为一次 session.run。

        实测（2026-10-18，CPU）：批量 6 窗 13.7s vs 逐条 20.4s（~1.5x），
        输出按 [B×N, 512] 顺序拼接，reshape(B, N, 512) 与逐条 max_diff=0。
        mel 长度不同的窗自动分组各跑各的；推理异常回退逐条。
        """
        if self.audio_session is None:
            return [None] * len(pcms)
        mels: list = []
        for p in pcms:
            try:
                m = self._pcm_to_mel(p, sr=sr)
            except Exception:
                m = None
            mels.append(m if m is not None and m.size > 0 else None)
        out: list = [None] * len(pcms)
        by_len: dict[int, list[int]] = {}
        for i, m in enumerate(mels):
            if m is not None:
                by_len.setdefault(int(m.shape[0]), []).append(i)
        need_mask = "input_features_mask" in {i.name for i in self.audio_session.get_inputs()}
        for T, idxs in by_len.items():
            B = len(idxs)
            feeds = {"input_features": np.stack([mels[i] for i in idxs]).astype(np.float32)}
            if need_mask:
                feeds["input_features_mask"] = np.ones((B, T), dtype=bool)
            try:
                outs = self.audio_session.run(None, feeds)
                feats = np.asarray(outs[0], dtype=np.float32)
                if feats.ndim == 3:
                    feats = feats.reshape(-1, feats.shape[-1])
                if feats.ndim != 2 or feats.shape[-1] != 512 or feats.shape[0] % B != 0:
                    raise ValueError(f"unexpected batch output {feats.shape}")
                segs = feats.reshape(B, feats.shape[0] // B, 512)
                for r, i in enumerate(idxs):
                    out[i] = segs[r]
            except Exception:
                for i in idxs:
                    out[i] = self._encode_audio_features(pcms[i], sr=sr)
        return out

    def _run_with_audio_features(self, feats: np.ndarray) -> np.ndarray | None:
        """audio_features → 主模型 → 768 维向量。

        与 image 同构：<bos> <|audio|> <|audio|>×N <|audio|> <eos>，
        实际有效 token 由 audio_features 形状决定，文本部分只占位。
        """
        if feats.ndim != 2 or feats.shape[-1] != 512:
            # 编码器未做投影（输出 [T, 128] 之类），主模型暂时吃不下；
            # 走 fallback：放弃这一窗口（避免污染主模型输入）
            return None
        n = feats.shape[0]
        # 模型合并模块仅在 id=258881 (audio_token) 处做 Replace，
        # 而 BOA=256000 / EOA=258883 是结构性 token（不进 cumsum，不需 feature）。
        # 之前 BOA/EOA 都错写成 258881，导致 cumsum 把 27 个 token 全计为 audio，
        # 引发 Gather OOB（27 个位置 vs n 个 feature）。
        BOA = "<|audio>"         # 256000
        AUDIO_T = "<|audio|>"    # 258881（占位，merge 按此 id 替换）
        EOA = "<audio|>"         # 258883
        prefix = config.IMAGE_INDEX_PREFIX  # 跨模态对齐（与 image 共用同一开关）
        if prefix:
            seq = prefix + BOA + AUDIO_T * n + EOA
        else:
            seq = BOA + AUDIO_T * n + EOA
        enc = self.tok.encode(seq, add_special_tokens=True)
        ids = np.asarray([enc.ids], dtype=np.int64)
        mask = np.asarray([enc.attention_mask], dtype=np.int64)
        feeds = {
            "input_ids": ids,
            "attention_mask": mask,
            "image_features": np.zeros((0, 512), dtype=np.float32),
            "video_features": np.zeros((0, 512), dtype=np.float32),
            "audio_features": feats,
        }
        try:
            outs = self.session.run(None, feeds)
        except Exception:
            return None
        named = dict(zip(self.output_names, outs))
        if self._pooled_out is None or self._pooled_out not in named:
            return None
        vec = np.asarray(named[self._pooled_out], dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)

    def embed_audio_pcm(self, pcm_int16: np.ndarray, sr: int = 16000) -> np.ndarray | None:
        """单窗口音频 → 768 维向量。失败（编码器未加载/推理异常）返回 None。"""
        if self.audio_session is None:
            return None
        feats = self._encode_audio_features(pcm_int16, sr=sr)
        if feats is None:
            return None
        return self._run_with_audio_features(feats)

    def info(self) -> dict:
        return {
            "provider": self.provider,
            "vision_provider": (self.vision_session.get_providers()[0]
                                if self.vision_session else None),
            "inputs": self.input_names,
            "outputs": self.output_names,
            "model": str(config.MODEL_ONNX.name),
            "vision": self.vision_session is not None,
        }


class WeMMEmbedder:
    """WeMM-Embedding-2B GGUF 嵌入器（llama_cpp）。

    输出 2048 维 mean-pooled 向量（L2 归一化）。
    支持文本检索；视觉/音频模态暂不支持（返回 None）。
    """

    def __init__(self, model_path=None, n_ctx: int = 4096, n_threads: int = 4):
        import llama_cpp

        model_path = model_path or config.WEMM_MODEL
        self.model_path = str(model_path)
        self.n_ctx = n_ctx
        self.n_threads = n_threads

        self.llm = llama_cpp.Llama(
            model_path=self.model_path,
            embedding=True,
            pooling_type=llama_cpp.LLAMA_POOLING_TYPE_MEAN,
            n_ctx=n_ctx,
            n_threads=n_threads,
            verbose=False,
        )
        self._dim = config.EMBED_DIM_WEMM
        self.vision_session = None
        self.audio_session = None

        # 简单 tokenizer 包装（用于 indexer 的字符计数）
        class _SimpleTok:
            def __init__(self, llm):
                self.llm = llm
            def encode(self, text, add_special_tokens=True):
                ids = self.llm.tokenize(text.encode("utf-8"), add_bos=add_special_tokens)
                return type("Encoding", (), {"ids": list(ids), "attention_mask": [1] * len(ids)})()
        self.tok = _SimpleTok(self.llm)

    def _embed(self, text: str) -> np.ndarray:
        emb = self.llm.create_embedding(text)
        data = emb["data"][0]["embedding"]
        vec = np.array(data, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(config.WEMM_QUERY_PREFIX + text)

    def embed_query_code(self, text: str) -> np.ndarray:
        return self._embed(config.WEMM_QUERY_PREFIX + text)

    def embed_document(self, title: str, text: str) -> np.ndarray:
        t = (title or "").strip()
        if t:
            return self._embed(f"{t}\n{text}")
        return self._embed(text)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vecs = [self._embed(t) for t in texts]
        return np.vstack(vecs).astype(np.float32)

    def embed_documents_titled(self, items: list[tuple[str, str]]) -> np.ndarray:
        if not items:
            return np.zeros((0, self._dim), dtype=np.float32)
        vecs = [self.embed_document(t, x) for t, x in items]
        return np.vstack(vecs).astype(np.float32)

    def embed_image(self, path) -> np.ndarray:
        return None

    def embed_image_pil(self, img) -> np.ndarray:
        return None

    def embed_audio_pcm(self, pcm_int16: np.ndarray, sr: int = 16000) -> np.ndarray | None:
        return None

    def info(self) -> dict:
        return {
            "provider": "llama_cpp",
            "model": "WeMM-Embedding-2B-GGUF",
            "dim": self._dim,
            "vision": False,
            "audio": False,
        }
