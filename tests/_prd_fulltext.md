Trace
多模态本地文件语义检索工具
产品需求文档 PRD v1.0

| 项目 | 内容 |
| 产品名称 | Trace |
| 文档版本 | v1.0 |
| 文档类型 | 产品需求文档（PRD） |
| 目标平台 | Windows 10 / 11（x64，后续 |
| 核心模型 | EmbeddingGemma 2（Apache 2.0 |
| 本期范围 | 仅 Windows 桌面端，不做 |


# 目录

# 一、产品定义

## 1.1 一句话描述
用自然语言描述你记得的内容，在本地文件里精准定位到那一页 / 那一秒 / 那一行——支持照片、PDF、文档、录音、视频、代码。

## 1.2 核心定位
Trace 不是文件名搜索工具，不是 Everything 的替代品。它的战场是：
用户只记得内容的大意、画面、声音，不记得文件名、路径、甚至语言，但内容确实在本地某个文件里。
传统搜索在这个场景下基本失效。Trace 用开源多模态向量模型在用户自己的电脑上建立内容级索引，实现跨模态、跨语言的语义检索。

## 1.3 产品愿景
你的大脑只记得那段讲睡眠的播客、合同里关于宠物的条款、视频里出现斑马的那一秒——Trace 帮你找到它，并直接跳到那个位置。

## 1.4 与现有工具的边界

| 维度 | 传统桌面搜索 | Trace |
| 检索对象 | 文件名、路径、部分全 | 文件内容语义 |
| 跨模态 | 不支持 | 文本 / 图片 / 音频 / 视 |
| 跨语言 | 弱 | 100+ 语言，中→英→阿 |
| 结果精度 | 打开文件 | 打开到页 / 秒 / 行 |
| 数据流向 | 部分走云端 | 完全本地，零外发 |


# 二、目标用户与典型场景

## 2.1 用户画像
知识工作者：会议录音、PDF 合同、培训视频混杂，靠语义回忆查找
研究者 / 学生：论文 PDF、笔记截图、录音混在一起
内容创作者：素材库中用一段描述找画面、音效、BGM
开发者：在自己项目里用自然语言找函数实现（opt-in）
多语言用户：中文描述搜出英文 / 日文文档

## 2.2 场景示例

| 输入 | 结果 |
| zebra in a video | 视频跳到斑马出现的 0: |
| where they talk about sleep | 播客跳到第 14 分钟 |
| the clause about pets in the l | PDF 打开到第 7 页，条 |
| payment declined | 翻出那张支付失败的截 |
| 中文输入 | 找到孟加拉语 / 阿拉伯 |
| code: retry a failed call with | VS Code 跳到对应行 |


# 三、功能需求

## FR-1 索引范围管理
首次启动引导用户选择要索引的文件夹（支持多选、嵌套子目录勾选）
每个目录显示预估首次索引耗时和磁盘占用
支持随时增删目录、暂停 / 恢复索引
产品设计决策：绝不主动扫描全盘；系统目录、程序文件、缓存默认排除；索引范围完全由用户显式控制

## FR-2 多模态文件解析与切片
这是 Trace 最核心的工程层，直接决定搜不搜得到。

| 文件类型 | 解析方式 | 切片策略 |
| 图片 / 截图 | 整张向量化 + OCR 抽文 | 单张即一个切片 |
| PDF | 逐页渲染 | 每页独立向量；扫描件 |
| 文档 | 按段落 / 语义块切分 | txt、md、rtf、doc、docx、 |
| 音频 | 原生波形向量化，不转 | 重叠 30 秒滑窗（模型 |
| 视频 | 抽帧 + 音轨分离 | 每 3–5 秒抽一帧 + 每 |
| 代码 | 默认不索引，opt-in | 按函数 / 类 / 语义块切 |

设计理由：
音频重叠滑窗确保语义不中断
视频抽帧 + 音轨分离让描述画面和描述对话都能命中
PDF 懒加载避免首次索引卡死
代码默认关闭，避免污染普通用户的索引空间

## FR-3 向量化与存储

| 项 | 选型 |
| 模型 | EmbeddingGemma 2（Apache 2.0 |
| 推理引擎 | llama.cpp，按硬件自动选 |
| 量化 | 8-bit GGUF，约 740MB，平 |
| 向量维度 | 768 维 |
| 存储 | 单一 SQLite 文件，同时 |
| 模型版本绑定 | 索引记录 model_version； |


## FR-4 混合检索（语义 + 精确词）
设计原则：向量擅长意思像，不擅长精确串，两者必须结合。
检索流程：
用户输入文本 → 仅加载文本编码器（约 250MB）→ 生成 query 向量
向量最近邻搜索（cosine similarity）
同步在 FTS5 关键词索引中匹配精确词（发票号、错误码、文件名、OCR 文字）
混合排序：score = α·cosine_sim + β·keyword_hit + γ·recency + δ·file_type_pref
跨模态结果混合排序返回
关键词索引覆盖范围：截图 OCR 文字、PDF 全文文本、文档正文、音频文件名与目录名、代码标识符。

## FR-5 精准定位打开
这是 Trace 区别于所有向量数据库工具的关键体验。

| 类型 | 打开行为 |
| 视频 | 调用 mpv / VLC --start-time= |
| 音频 | 同上，跳到对应秒 |
| PDF | 调用默认 PDF 阅读器打 |
| 图片 | 预览 + explorer /select 定 |
| 文档 | 打开并滚动到命中段落 |
| 代码 | 在 VS Code / Cursor / JetBrai |


## FR-6 全局搜索面板
全局热键呼出（默认 Ctrl+Shift+Space，可自定义）
启动时检测热键冲突并提示
输入即搜，停止输入后约 0.1 秒出结果
快捷键：↑↓ 移动、Enter 打开、Space 预览、Win+Enter 资源管理器定位、Ctrl+O 打开大窗口

## FR-7 大窗口与筛选
图片网格视图
按类型（文档 / 图片 / 音频 / 视频 / 代码）筛选
按时间范围、目录、语言筛选
相似度阈值滑块

## FR-8 代码搜索（opt-in）
默认关闭，在设置中对指定仓库开启
每个仓库预先告知预估索引耗时和占用
用 code: 前缀触发语义代码查找
结果在编辑器跳行

## FR-9 增量索引与系统适配
文件系统监听（ReadDirectoryChangesW），新文件落地约 1–2 秒内可搜
文件修改自动重新切片向量化
删除自动清理向量和关键词索引
电源策略：电池模式 / 低电量自动暂停索引，插电恢复
索引任务作为独立辅助进程运行，干完即退，不常驻占 GPU

## FR-10 命令行工具 trace.exe
trace index <dir>：索引指定目录
trace search query：搜索
trace evals：跑内置评测集
trace status：查看索引状态

# 四、非功能需求

## 4.1 隐私与安全（最高优先级）
只读、不改、不移动、不上传
索引内容绝不出本机
仅两个时刻联网：首次下载模型权重（约 865MB）、每日更新检查（不带任何文件信息，可关闭）
无账号体系、无遥测、无统计上报
敏感目录硬排除：~/.ssh、密钥文件、证书、密码管理器数据库、app 缓存、node_modules、dataset 类文件夹、仅云端的 OneDrive 文件
代码仓库（含 .git / package.json）默认只索引截图，除非用户显式开启代码搜索

## 4.2 性能目标

| 指标 | 目标 |
| 查询响应 | 停止输入后 < 0.2 秒 |
| 首次索引（大型下载夹 | ≤ 1 小时 |
| 增量索引 | 新文件 < 2 秒可搜 |
| 查询时内存 | < 500MB（仅加载文本编 |
| 索引峰值内存 | < 2GB |
| 模型推理 | CUDA 优先，其次 Vulkan， |


## 4.3 中文与多语言
已知风险：EmbeddingGemma 2 中文能力非最强，需工程手段弥补
应对组合：FTS5 + jieba 分词覆盖中文精确匹配；PaddleOCR 处理中文截图与扫描件；FTS5 trigram 索引兜底模糊匹配
中文语义召回依赖向量模型本身，通过评测集持续监控

## 4.4 兼容性
Windows 10 / 11，x64 优先，后续考虑 ARM64
N 卡走 CUDA，有核显走 Vulkan，无加速走 CPU
中文 Windows 为首要适配环境

# 五、技术架构

## 5.1 整体流水线
索引：用户选文件夹 → 解析层（Pillow / PDFium / FFmpeg / PaddleOCR）→ 切片层（图片 / PDF页 / 文本块 / 视频帧 / 30s音频窗）→ EmbeddingGemma 2（llama.cpp CUDA·Vulka
查询：输入文本 → 仅加载文本编码器 → query 向量 → 向量最近邻 + FTS5 精确词命中加权 → 定位到 page / timestamp / line。

## 5.2 技术选型

| 层 | 选型 | 理由 |
| 前端框架 | Tauri + React | 轻量、跨前端生态、Rus |
| 后端核心 | Rust | 性能、内存安全、llama. |
| 图像解析 | Pillow / Windows Imaging Compo | 格式覆盖广 |
| PDF | PDFium | 逐页渲染 + 文本提取 |
| 音视频 | FFmpeg (libav) | 抽帧、音轨分离、格式 |
| OCR | PaddleOCR | 中文识别率远超 Tesserac |
| 中文分词 | jieba-rs | Rust 原生，FTS5 分词器 |
| 向量模型 | EmbeddingGemma 2（8-bit GGUF | 开源、四模态、本地可 |
| 推理引擎 | llama.cpp | CUDA / Vulkan / CPU 全支持 |
| 存储 | SQLite + sqlite-vec + FTS5 | 单文件、零运维、向量 |
| 文件监听 | ReadDirectoryChangesW | Windows 原生 |
| 视频跳转 | mpv / VLC CLI | 支持 --start-time 参数 |
| 全局热键 | RegisterHotKey + Tauri plugin | 系统级热键 |


## 5.3 核心数据表
文件元数据表 files：file_id（主键）、path（唯一）、name、size、mtime、file_type（pdf / image / audio / video / text / code）、mod_date、indexed_at。
切片与向量表 slices：slice_id、file_id（外键）、modality（image / pdf / text / audio / video / code）、location（如 page:7 / sec:42 / line:128）、vector（768 维 f32 二进制）
关键词索引：CREATE VIRTUAL TABLE slices_fts USING fts5(text_snippet, keywords, content=slices, content_rowid=slice_id, tokenize=trigram)。
向量索引：CREATE VIRTUAL TABLE vec_index USING vec0(slice_id INTEGER PRIMARY KEY, embedding FLOAT[768])。

# 六、风险与应对

| 风险 | 影响 | 应对 |
| 中文语义召回偏弱 | 中文描述搜中文内容精 | jieba + PaddleOCR + FTS5 兜 |
| 模型 740MB，CPU 推理慢 | 首次索引耗时过长 | 8-bit 量化；CUDA / Vulkan  |
| 长视频切片语义断裂 | 命中位置偏移 | 重叠滑窗 + 合并相邻命 |
| 热键冲突 | 用户无法呼出 | 可自定义 + 启动时检测 |
| 向量空间随模型升级变 | 旧索引失效 | model_version 字段 + 升级 |
| 无独立 benchmark | 质量不可量化 | 内置 trace evals，自建中 |
| 文件监听漏事件 | 增量索引丢失 | 定时全量校验（每日一 |


# 七、不做的事（Out of Scope）
全盘自动扫描
云端索引 / 上传任何文件数据
大模型生成 / 对话 / RAG 问答（只做检索，不做生成）
修改、移动、删除用户文件
macOS / Linux 版（本期仅 Windows）
账号体系、云同步、团队协作
移动端

# 八、里程碑

| 阶段 | 周期 | 交付物 |
| P0 内核 | 4 周 | 解析 + 切片 + EmbeddingGem |
| P1 检索 | 3 周 | 混合检索、精准定位打 |
| P2 体验 | 3 周 | 大窗口、筛选、增量索 |
| P3 代码搜索 | 2 周 | opt-in 代码索引 + 编辑 |
| P4 打磨 | 2 周 | 安装包、签名、更新检 |


# 九、成功标准
首次索引大型目录 ≤ 1 小时，增量 < 2 秒
查询响应 < 0.2 秒
跨语言召回率（自建评测集）达到可用水平
精确串（发票号 / 错误码）命中率约 100%
隐私审计：零文件数据外发
中文场景日常可用