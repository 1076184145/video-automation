# Video Automation

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-orange.svg)](https://ffmpeg.org/)

**English: [README.md](README.md)**

把本地长视频整理成可以审核的短视频，并生成转写文本、字幕、封面和导出文件。除非你主动使用外部 AI 服务，否则所有处理都在自己的电脑上完成。

![Video Automation 仪表板](docs/assets/dashboard.png)

> Video Automation 只接收本地视频文件，不提供 URL 下载和直播录制。软件不会修改原视频，平台登录和自动发布默认关闭。

## 5 分钟开始使用

排除确认受损的内容：在任务的「剪辑审核 → 剪辑片段 → 排除受损时间段」输入原视频起止时间，保存剪辑点后重生成预览。支持撤销，不修改源文件；已有成片需另外重新生成。时间戳警告不等于坏帧。「保留片段文本」即时跟随剪辑变化；完整原始转写仍可展开编辑。缺少词级时间戳的跨切点句子会标注“部分保留”。

### Windows 桌面版（推荐）

1. 从 [GitHub Releases](https://github.com/1076184145/video-automation/releases) 下载最新 Windows 安装包或压缩包。
2. 安装或解压后，运行 `VideoAutomationLite.exe`。
3. 打开 **健康检查**。如果缺少 FFmpeg 或 FFprobe，点击 **一键修复环境**。
4. 打开 **新建任务**，添加一个本地视频；也可在录播文件列表点击 **全选加入批量**（每批最多 30 个，自动去重）。删除可逐个点击 ×，或勾选/全选后点击 **删除所选**，确认后将未被任务引用的录播移入 `.deleted_recordings` 回收区，失败项保留并报告。按 `restore.json` 中的原路径手动移回并重命名 `payload.deleted` 即可恢复，不立即释放磁盘空间。
5. 选择 **极速模式**、**抖音** 或 **B 站** 等预设，然后开始处理。
6. 打开完成的任务，审核结果并下载 `final.mp4`。

基础流程不需要 API Key。工作台支持浅色/深色主题与窄屏布局，审片箱采用紧凑筛选，新建任务中的批量加入与删除操作分区展示。

### 从源码运行

需要准备：

- Python 3.11 或更高版本
- 已加入 `PATH` 的 FFmpeg 和 FFprobe
- Git
- 可选：NVIDIA 显卡，用于加速转写和渲染

Windows PowerShell：

```powershell
git clone https://github.com/1076184145/video-automation.git
cd video-automation
py -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-transcription-faster.txt
.\venv\Scripts\python.exe .\run_worker.py --serve
```

macOS 或 Linux：

```bash
git clone https://github.com/1076184145/video-automation.git
cd video-automation
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements-transcription-faster.txt
./venv/bin/python run_worker.py --serve
```

推荐命令安装默认配置使用的轻量 Faster-Whisper 环境；`requirements.txt` 保留 OpenAI Whisper CLI 回退。FunASR 加 Faster-Whisper 回退使用 `requirements-transcription-funasr.txt`，桌面打包、Pillow、Demucs 等可选组件仍在 `requirements-optional.txt`。

WSL 必须单独创建 Linux 虚拟环境，不能直接复用 Windows 的虚拟环境。

浏览器打开 [http://127.0.0.1:8765/#/](http://127.0.0.1:8765/#/)。使用期间不要关闭运行服务的终端窗口。

### 可选：本地 Hugging Face AI

请按可用显存选择模型，不要照搬某一台电脑的实际配置。下面是假设同一时间只加载一个模型、并预留 1–2 GB 显存的保守起点：

| 可用显存 | 本地文本模型 | 本地参考图模型 | 建议内存 |
|---|---|---|---|
| 仅 CPU 或低于 6 GB | 1B–3B GGUF，Q4 | CPU 卸载或外部服务；512–768 px | 16 GB+ |
| 8 GB | 7B–8B GGUF，Q4 | 2B–4B，4 bit；最高 768 px | 24–32 GB |
| 12–16 GB | 12B–14B GGUF，Q4/Q5 | 4B–8B，4 bit；768–1024 px | 32 GB+ |
| 24 GB 以上 | 20B–32B GGUF，Q4/Q5 | 8B–12B，4/8 bit；最高 1024 px | 64 GB+ |

可从 Hugging Face 的[文本生成模型列表](https://huggingface.co/models?pipeline_tag=text-generation&sort=trending)和[图生图模型列表](https://huggingface.co/models?pipeline_tag=image-to-image&sort=trending)筛选，并逐一核对许可证和运行时兼容性；运行环境使用 `requirements-local-ai.txt` 和 [llama.cpp](https://github.com/ggml-org/llama.cpp/releases)。实际模型 ID、路径、权重、清单、缓存、日志和评测只保存在被忽略的本地 `.env`、`models/`、`config/` 及运行目录中，Git 只保留通用建议。

## 日常使用流程

1. **导入：** 拖入本地视频，或从 `input/recordings` 选择已有文件。
2. **选择：** 选择工作流预设，只开启自己需要的选项。
3. **处理：** 软件检查视频、转写语音、建议剪辑点，并生成选中的产物。
4. **审核：** 预览片段，修改剪辑点或转写文本，需要时重新运行。
5. **导出：** 下载最终视频、字幕、封面或手动发布包。

预设只是推荐起点：

| 预设 | 适合场景 |
|---|---|
| **极速模式** | 尽快生成成片，减少可选分析 |
| **只分析** | 只要转写和检测结果，不急着导出完整视频 |
| **抖音** | 竖屏短视频 |
| **B 站** | 常规 B 站视频 |
| **YouTube Shorts** | 竖屏 Shorts 视频 |

## 功能

本地流程已经包含：

- 单个或批量导入视频
- 任务进度、重启恢复和持久化任务队列
- 分阶段进度、持久化重试，以及可靠的取消和删除操作
- 使用 Whisper 兼容本地后端进行语音转写
- 静音、静止画面、场景切换和坏帧检查
- 剪辑建议、转写文本编辑、字幕行数硬限制，以及可横向滚动的剪辑表
- 本地有界剪辑边界精修：避免截断词语，同时不增加无效画面覆盖
- 浏览器预览和完整质量的 `final.mp4`
- 竖屏 `1080x1920` 输出和内嵌字幕
- 项目、可复用配方、创作者设置和审核版本
- 路由、弹窗、列表和折叠区均提供克制的进入/退出动效，并支持“减少动态效果”
- Premiere Pro 与剪映/CapCut 交接文件
- 支持平台的手动上传包

可选功能：

- AI 封面、字幕翻译、标题简介和语义高光建议；服务商不可用时，封面和文案会回退为本地规则生成
- NVIDIA CUDA/NVENC 加速，并通过内核文件锁在多进程间互斥使用 GPU
- 可选开启的平台多画幅变体与分段并行渲染（`PLATFORM_VARIANTS_ENABLED`、`RENDER_SEGMENT_PARALLEL_ENABLED`）
- Faster-Whisper 本地转写（默认 `medium` 主模型、`small` 回退模型）；已有 FunASR 配置仍兼容
- Demucs 音频分离
- 单独配置的发布连接器；手动发布包始终可以作为备用方案

外部 AI 功能需要对应服务商的 Key，本地 Hugging Face 模式不需要。在 **设置**
中新填的密钥会保存到操作系统凭据库，
私有 `.env` 只保留引用；已有的 `.env` 明文密钥可以通过设置页警告一键迁移。
只有系统凭据库写入并回读成功后才会删除明文；迁移失败时原值仍保留在 `.env`。
**健康检查**会显示工具在本机实际解析到的路径，可修改的目录与工具配置位于**设置**。
全部配置项见 [`.env.example`](.env.example)。

语义高光会覆盖采样整段转写并返回精确时间区间；高光剪辑和封面上下文直接使用这些
区间，不再退化为整段结构剪辑。使用支持参考图的 OpenRouter 图片模型时，应用会在
本地比较多个高分语义区间，优先选择主体清晰居中、没有重复或分屏干扰的一帧；只有
你明确运行外部封面功能时，才会把该帧连同封面提示发送给服务商。
语义服务调用失败时会保留上一次可用的 `highlights.json`，并只把服务商、模型、状态
和稳定错误码写入 `highlights_attempt.json`；该文件不保存提示词、转写文本或 API Key。
结构化输出会做 schema 校验，支持修复重试和可选的回退服务商（`LLM_FALLBACK_PROVIDER`）。

转写在隔离子进程中运行，并带有阶段心跳、无进展超时、进程树清理和临时后端熔断。某个模型失败后会及时进入配置的回退模型，不会继续占住队列直到旧的按视频时长计算的超时结束。

转写语言默认自动检测。只有在单个任务或整批素材确定使用同一种语言时，才建议固定
`zh`、`en`、`ja` 或 `ko`；强制选择错误语言会生成看似正常但内容错误的字幕。
生成字幕前会移除无效的 Unicode 替换字符、限制明显的解码循环、折叠重复短语式的
解码退化，并丢弃“判定为静音且置信度很低”的转写段落。

## 重要输出文件

每个任务保存在 `processing/jobs/<任务名>/`。

| 文件 | 用途 |
|---|---|
| `final.mp4` | 完整质量的最终视频（启用多画幅变体时另有 `variants/<平台>.mp4`） |
| `web_preview.mp4` | 体积较小的浏览器预览 |
| `transcript.txt` / `.srt` | 转写文本和字幕 |
| `cuts.json` | 建议或编辑后的剪辑片段 |
| `highlights.json` / `highlight_cut.json` | 语义高光结论和受目标时长约束的渲染剪辑 |
| `highlights_attempt.json` | 最近一次语义服务调用状态和不含敏感内容的错误码 |
| `clip_refinement.json` | 剪辑边界检查的尝试记录、评分与恢复状态 |
| `highlight_thumbnail.jpg` | 为封面生成在本地抽取的内容参考帧 |
| `cover_*.jpg` | 生成或选中的封面 |
| `publish_packages/` | 手动上传需要的视频和文案 |
| `project_exports/` | Premiere Pro 或剪映/CapCut 交接文件 |

## 常见问题

**提示缺少 FFmpeg 或 FFprobe**

打开 **健康检查**，点击 **一键修复环境**。源码用户也可以运行：

```powershell
.\venv\Scripts\python.exe .\run_worker.py --health
```

Python AI 运行库缺失时，**健康检查**会提供可复制的安装命令和安全回退按钮，
不会在后台静默安装重型依赖。

**第一次处理很慢**

语音模型第一次使用时可能需要下载和初始化。后续任务会复用本地模型文件，启动通常更快。

**点击取消后，运行中的任务没有立刻停止**

取消采用协作式流程：任务会先显示为**正在取消**，后台同时终止当前转写或渲染子进程并释放资源。如果应用曾被强制关闭，重新启动后可以使用任务页面提供的恢复或删除操作处理陈旧任务。

**CUDA 或显卡处理失败**

在 **设置** 中选择更小的语音模型，或把转写和渲染切换到 CPU。

**AI 按钮提示缺少 Key**

这不影响本地剪辑流程。只有需要对应 AI 功能时才配置服务商 Key。

**任务文件在哪里？**

打开 `processing/jobs/`。不要把这个目录、`.env`、日志、私人视频或生成文件提交到 Git。

## 开发者命令

可选的句子 ID 自动切片：[无人值守高光说明](docs/UNATTENDED_HIGHLIGHTS.md#中文说明)（`--unattended-highlights`，独立输出 30–75 秒 MP4）。`HIGHLIGHT_GRAPH_ENABLED=true` 开启多角度候选，`HIGHLIGHT_LLM_CHECKER_ENABLED=true` 开启全文审核后再去重；两项默认关闭，失败均保留明确状态。

```powershell
# 查看全部 CLI 参数
.\venv\Scripts\python.exe .\run_worker.py --help

# 输出机器可读的健康检查结果
.\venv\Scripts\python.exe .\run_worker.py --health --json

# 处理一个本地视频
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\video.mp4" --profile douyin --progress

# 仅预览 30 天前已完成任务可清理的中间文件
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --cleanup-mode intermediates --dry-run

# 只回收已完成任务的音频缓存和临时文件
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --cleanup-mode intermediates

# 运行 Python 测试
.\venv\Scripts\python.exe -m unittest discover -s tests
```

本地 Web 服务默认只监听 `127.0.0.1:8765`。非回环地址默认会被拒绝，只有显式
设置 `API_ALLOW_REMOTE=true` 才能启动；这个开关不是身份验证，远程使用仍必须配置
防火墙、带认证的反向代理和 HTTPS。参与开发前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 隐私和功能边界

- 视频、任务文件和服务商 Key 默认保存在你的电脑上。
- 使用外部 AI 功能时，只会把必要请求和凭据直接发送给你选择的服务商。
- 本项目不运营中转服务器，也没有远程自动更新服务。
- 手动发布包不会登录账号或自动上传。
- 你需要自行确认拥有处理和发布视频的合法权利。

安全问题请按照 [SECURITY.md](SECURITY.md) 私下报告。

## 开源协议

Video Automation 使用 [MIT License](LICENSE)。第三方工具、模型、字体和 API 可能有各自的条款，详见 [NOTICE](NOTICE)。
