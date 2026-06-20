# Video Automation 中文说明

**语言：** [English](README.md) | 简体中文

Video Automation 是一个把长录播变成可审核粗剪、字幕、封面和发布交接包的本地自动化工作流。

它优先服务直播切片、游戏录播、教育录播创作者，以及需要批量处理素材的小团队。系统可以接收本地视频文件，为每个素材创建独立 job，完成媒体探测、音频提取、语音转写、剪辑建议、字幕生成、预览视频和最终视频导出。原始录播不会被修改。

最快上手路径：

1. 启动桌面端或本地服务，先打开健康检查页。
2. 如果 FFmpeg 等必要便携工具缺失，点击一键修复。
3. 拖入一个视频，选择抖音或 B 站等创作者预设，然后开始处理。
4. 审核剪辑建议，导出成片，再使用发布交接包上传平台。

普通用户优先使用健康检查页和设置页完成配置；`.env` 仍保留给专家参数调优。

## 快速开始：本地运行

### 方式 A：Windows 桌面版

如果你下载的是发布包或安装器：

1. 安装或解压 Video Automation。
2. 启动 `VideoAutomationLite.exe`。
3. 如果软件提示环境缺失，打开健康检查页。
4. 如果 FFmpeg 或 FFprobe 缺失，点击 **一键修复环境**。
5. 进入 **新建任务**，拖入视频，选择预设，然后开始处理。

桌面端会在后台启动本地服务，并自动打开 Web 界面。视频、任务产物和 API Key 默认都保存在你自己的电脑上；只有主动使用外部 AI 服务时，相关内容才会发给对应服务商。

### 方式 B：从源码运行

前置条件：

- Windows 10/11、macOS 或 Linux。
- Python 3.11+。
- FFmpeg 和 FFprobe。Windows 下可以在健康检查页自动安装便携版到 `tools\bin`。
- 可选：NVIDIA CUDA，用于加速转写和渲染。

初始化：

```powershell
git clone https://github.com/<your-name>/<your-repo>.git
cd <your-repo>
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动本地 Web 应用：

```powershell
.\venv\Scripts\python.exe .\run_worker.py --serve
```

然后打开：

```text
http://127.0.0.1:8765/#/
```

首次运行检查清单：

1. 打开 **健康检查**，修复缺失的必要工具。
2. 只有需要调整模型、GPU、字幕、渲染或 AI 选项时，再打开 **设置**。
3. 打开 **新建任务**，拖入视频或填写本地文件路径。
4. 等待任务进入 **待审核** 或 **已完成**。
5. 打开任务详情页，审核剪辑片段，然后下载 `final.mp4`、字幕、封面或发布交接文件。

基础本地流程可以直接使用默认配置。如果需要 AI 封面、字幕翻译或自定义模型参数，打开 Web 设置页填写对应选项即可。

### 可选 AI 功能

AI 封面、语义高光、标题简介元数据和字幕翻译需要你自己的服务商 Key。可以在 Web 设置页填写，也可以写入私有 `.env`：

```text
OPENAI_API_KEY=
GOOGLE_API_KEY=
COVER_API_KEY=
```

如果你只需要本地视频处理、转写、字幕和渲染，可以全部留空。

## 当前功能

- 本地 Web 控制面板：`http://127.0.0.1:8765/#/`
- 新建任务页支持拖拽视频导入到 `input\recordings`
- 支持一次拖入多个视频，批量导入并批量提交任务；同一批次会在仪表板聚合显示整体进度
- 大文件导入时显示上传百分比进度
- 支持从 `input\recordings` 中直接选择已有录播
- 工作流预设：只分析、抖音、B 站、YouTube Shorts
- Dashboard 展示任务状态、进度、缩略图和快速入口
- 任务详情页展示 Pipeline、视频预览、时间线、转写文本、剪辑点编辑器和下载区
- 通过 Server-Sent Events 实时更新任务状态；后台标签页会显示完成计数，浏览器已授权时可弹出系统通知
- 剪辑点可在线编辑，保存后自动重渲染预览视频
- 转写文本可在线编辑，转写时间戳可点击跳转到视频预览位置
- 支持 AI 生成视频封面，可生成竖版和横版候选图后手动选择
- 支持可选增强模块：平台拆分、字幕翻译、AI 标题/简介/标签、语义高光、发布包、剪辑工程导出
- 时间线波形优先使用 `audiowaveform`，缺失时自动用 Python 从 `audio.wav` 生成简化波形
- 剪辑建议会自动合并过碎片段和短间隔跳切，降低复查负担
- 支持审核流程，可将待审核 job 标记为完成
- 健康检查页展示本地工具状态，支持一键修复常用便携工具；设置页可保存常用运行配置
- 视频/音频文件端点支持 HTTP Range，浏览器预览可以拖动进度条
- 提供本地 HTTP API，方便 n8n、扣子或其他自动化工具调度
- 支持 `API_PARALLEL_JOBS` 控制并行处理数量
- 提供 CLI worker，支持单文件、监听、批处理、续跑、清理和状态查询
- 提供标准库 `unittest` 测试，覆盖核心解析、剪辑规划、字幕和配置辅助逻辑

## 项目结构

当前文件布局、模块职责和运行目录说明见 `docs/PROJECT_STRUCTURE.md`。

## 处理流程

典型 Pipeline 阶段：

```text
probe
detect_corruption
extract_audio
transcribe
detect_silence
detect_freeze
detect_scenes
plan_cuts
style_subtitles
plan_crop
plan_uvr
plan_render
render_review
render_final
```

不是所有任务都会运行全部阶段。具体阶段取决于 CLI 参数、Web 选项或工作流预设。

## 目录结构

默认主目录：

```text
D:\video-automation
```

常用目录：

```text
D:\video-automation
├── input
│   └── recordings          # 默认录播输入目录
├── processing
│   └── jobs                # 每个视频对应一个 job
├── logs                    # worker 日志
├── web                     # 本地 Web 控制面板
├── video_automation        # Python 包
├── run_worker.py           # worker 入口
├── README.md
└── README.zh-CN.md
```

每个 job 输出到：

```text
D:\video-automation\processing\jobs\<时间戳-素材名>\
```

## Job 产物

常见产物：

```text
job.json                  job 状态、阶段进度、错误信息
job.log                   单个 job 日志
manifest.json             ffprobe 媒体信息和快速文件指纹
thumbnail.jpg             Dashboard 缩略图
audio.wav                 转写用音频，可选应用降噪/滤波
audio_hq.flac             高质量音频，供后续剪辑或调音
waveform.json             音量波形数据，来自 audiowaveform 或 Python fallback
transcript.txt            纯文本转写
transcript.srt            SRT 字幕
transcript.json           结构化转写片段，启用时包含词级时间戳
transcript_translated_zh.* 可选的翻译转写 JSON/TXT/SRT 产物
subtitles.ass             带样式的 ASS 字幕
subtitles_clipped.ass     按剪辑点重映射后的 ASS 字幕
subtitles_translated_zh*.ass 可选的翻译 ASS 字幕
corrupt.json              源视频解码完整性扫描结果
silence.json              静音检测结果
freeze.json               静止画面检测结果
scene.json                场景切换检测结果
cuts.json                 结构化剪辑建议和编辑后的 clips
cuts.md                   人工复查剪辑单
crop_plan.json            竖屏画面适配计划
uvr_plan.json             人声/BGM 分离计划或执行状态
render_preview.json       预览视频渲染计划
review.mp4                预览视频
final_render_preview.json 最终视频渲染计划
final.mp4                 最终视频
final_translated_zh.mp4   可选的翻译字幕最终视频
web_preview.mp4           网页播放用轻量预览，不影响最终成片质量
web_preview.json          网页预览代理生成记录
cover_manifest.json       AI 封面生成状态和候选图元数据
cover_9x16_01.jpg         竖版封面候选图
cover_16x9_01.jpg         横版封面候选图
cover_vertical.jpg        已选择的竖版封面
cover_landscape.jpg       已选择的横版封面
segments_manifest.json    可选的平台拆分清单
segments/*.mp4            可选的平台分段视频
metadata.json             可选的 AI 标题/简介/标签元数据
highlights.json           可选的 LLM 语义高光
publish_package.json      可选的手动上传发布包清单
project_export_manifest.json 可选的 Premiere/剪映工程导出清单
project_exports/*         可选的剪辑工程交接文件
```

`uvr_plan.json` 默认仍是计划模式。安装 Demucs 并设置 `AUDIO_SEPARATION_ENGINE=demucs` 后，同一个 `plan_uvr` 阶段会执行音频分离，并输出 `uvr/vocals.wav` 和 `uvr/instrumental.wav`。

## Web 控制面板

启动本地服务：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --serve
```

浏览器打开：

```text
http://127.0.0.1:8765/#/
```

Web 控制面板支持：

- 拖拽视频到新建任务页
- 一次拖入多个视频并批量提交
- 大文件导入时显示上传进度
- 选择 `input\recordings` 中已有文件
- 选择工作流预设
- 选择转写语言
- 勾选静音检测、静止画面检测、场景切换检测、生成预览视频、生成最终视频、竖屏 9:16、内嵌字幕、跳过转写
- 查看任务状态、Pipeline、视频预览、时间线、转写文本和剪辑片段
- 编辑剪辑点并保存
- 点击转写时间戳跳转视频预览
- 直接编辑转写文本并重新生成字幕/预览
- 生成 3 张或 5 张 AI 封面候选图，并选择竖版/横版成品封面
- 在任务详情页手动生成平台分段、字幕翻译、AI 元数据、语义高光和发布包
- 审核通过后标记任务完成
- 下载主要产物和高级 JSON 产物
- 任务状态通过 SSE 实时推送，不再依赖暴力轮询；页面在后台时会通过标题计数提示完成/待审核/失败任务，浏览器通知已授权时会弹出系统通知

界面文案使用创作者更容易理解的说法。例如 `render_review` 显示为“生成预览视频”，`burn_subtitles` 显示为“内嵌字幕”。

## 桌面启动器

Phase 1 桌面版是一个轻量 Python 外壳，复用现有 API 和 Web 前端。

安装可选桌面依赖：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe -m pip install pywebview
```

启动桌面版：

```powershell
.\venv\Scripts\python.exe .\desktop_app.py
```

`desktop_app.py` 会启动同一套本地 API 服务，并用原生 WebView 窗口打开 `http://127.0.0.1:8765/#/`。如果没有安装 `pywebview`，会自动退回系统浏览器。如果端口已经被占用，则认为服务已经在运行，只打开界面，不再启动第二个后端。

构建推荐的轻量 Windows onedir 包：

```powershell
cd D:\video-automation
.\tools\build_desktop.ps1 -InstallDeps -Lite -Clean
```

Lite 包会排除 torch、FunASR、SciPy、ModelScope 等重型可选 ML 依赖，更适合作为第一版可分发桌面壳。如果确实想把当前 venv 中已经安装的可选 ML 依赖也打进去，可以去掉 `-Lite` 构建完整包：

```powershell
.\tools\build_desktop.ps1 -Clean
```

如果要把桌面包压缩成可分发 zip：

```powershell
.\tools\package_desktop.ps1 -Lite -SkipBuild
```

去掉 `-SkipBuild` 时，脚本会先重新构建再打包。zip 会输出到 `dist\releases\`。

如果要构建普通 Windows 安装器，先安装 Inno Setup 6，然后运行：

```powershell
.\tools\build_installer.ps1 -Version 0.1.0
```

安装器会输出到 `dist\installers\`。它会把 Lite 桌面包安装到当前用户的 Local AppData 目录，因此不需要管理员权限。如果 Inno Setup 安装在自定义路径，可以把 `INNO_SETUP_COMPILER` 设置为 `ISCC.exe` 的完整路径。

如果希望源码运行或桌面包不依赖系统 PATH，可以在构建前把便携工具放到 `tools\bin`：

```text
tools\bin\ffmpeg.exe
tools\bin\ffprobe.exe
tools\bin\audiowaveform.exe
```

Windows 下可以用脚本自动下载常用便携工具：

```powershell
.\tools\install_desktop_tools.ps1
```

这个脚本会把 `ffmpeg.exe` 和 `ffprobe.exe` 下载到 `tools\bin`。需要覆盖已有文件时加 `-Force`；不需要安装时可加 `-SkipFfmpeg`。

同样的修复流程也可以直接在 Web UI 里完成：打开健康检查页，若 `ffmpeg` 或 `ffprobe` 缺失，会出现 **“一键修复环境”** 按钮。后端会在后台运行 `tools\install_desktop_tools.ps1`，通过 SSE 推送安装日志，并在完成后自动刷新健康检查状态。

Settings / `.env` 中显式配置的路径仍然优先；如果 `tools\bin` 中没有对应 exe，程序会继续回退到 PATH 里的命令名。可以用下面的脚本检查当前会使用哪一个工具：

```powershell
.\tools\check_desktop_tools.ps1
```

当前桌面包会包含 Web 静态资源和 `.env.example`，但不会把你的 `.env` 密钥打包进去。模型和 API Key 仍通过 Settings 页面或 `.env` 配置。健康检查页可以把常用便携工具安装到 `tools\bin`；可选 ML 依赖和 API Key 仍由用户按需配置。

## 测试

项目当前使用 Python 标准库 `unittest` 作为初始测试体系，不需要额外测试依赖。

运行当前回归检查：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe -m unittest discover -s tests
.\venv\Scripts\python.exe -m compileall .\video_automation .\run_worker.py
node --check .\web\js\app.js
node --check .\web\js\router.js
node --check .\web\js\job-detail.js
node --check .\web\js\download-section.js
node --check .\web\js\i18n.js
```

当前测试覆盖配置读取、ffmpeg 输出解析、剪辑规划辅助逻辑、转写文本关联到 clip、ASS 字幕格式化、剪辑后字幕时间轴重映射和字幕换行。后续改核心 pipeline 逻辑时，建议同步在 `tests\` 中补测试。

## AI 视频封面

任务详情页包含可选的 AI 封面面板。封面生成是 job 的附加能力，不加入主剪辑 pipeline，也不会改变 `needs_review`、`done` 或 `failed` 状态。

使用前需要在 `.env` 中配置图片接口 key。官方 OpenAI 使用默认配置即可；第三方 OpenAI-compatible Images API 网关可以通过 `COVER_BASE_URL` 和 `COVER_API_KEY` 接入。OpenRouter 走带图片输出的 Chat Completions；Google AI Studio 则通过 Gemini 原生 `generateContent` API 接入。

```text
COVER_PROVIDER=openai
COVER_BASE_URL=https://api.openai.com/v1
COVER_MODEL=gpt-image-2
COVER_COUNT=3
COVER_ASPECTS=9:16,16:9
COVER_QUALITY=medium
COVER_OUTPUT_FORMAT=jpeg
COVER_TITLE_FONT=Microsoft YaHei
COVER_API_KEY=YOUR_OPENAI_OR_COMPATIBLE_KEY
COVER_HTTP_REFERER=http://127.0.0.1:8765
COVER_APP_TITLE=Video Automation
```

如果 `COVER_API_KEY` 留空，worker 会回退使用 `OPENAI_API_KEY`。这样可以用官方 OpenAI key 生成封面，也可以把封面单独接到第三方 key，同时不影响字幕翻译、标题简介、语义高光等文本 LLM 配置。`COVER_HTTP_REFERER` 是可选请求头，第三方网关要求时再填；本地使用可填 `http://127.0.0.1:8765`。

OpenRouter 示例：

```text
COVER_PROVIDER=openrouter
COVER_BASE_URL=https://openrouter.ai/api/v1
COVER_API_KEY=YOUR_OPENROUTER_KEY
COVER_MODEL=google/gemini-2.5-flash-image
COVER_MODALITIES=image,text
COVER_HTTP_REFERER=http://127.0.0.1:8765
COVER_APP_TITLE=Video Automation
```

使用 OpenRouter 时，需要选择 `output_modalities` 包含 `image` 的模型；可通过 `https://openrouter.ai/api/v1/models?output_modalities=image` 查看可用模型。

Google AI Studio 示例：

```text
GOOGLE_API_KEY=YOUR_GOOGLE_API_KEY
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com/v1beta
COVER_PROVIDER=google
COVER_MODEL=gemini-2.5-flash-image
```

使用 Google 生成封面时，如果填写了 `COVER_API_KEY` 会优先使用它，否则使用 `GOOGLE_API_KEY`。每张候选图对应一次 Gemini 图片请求，之后仍由本地 Pillow 完成精确的 9:16 / 16:9 裁切和中文标题叠加。Google 的可用图片模型可能随时间调整，请在 Google AI Studio 中选择当前可用、支持图片输出的 Gemini 模型。

生成时会读取 `manifest.json`、`cuts.json`、`transcript.json` 和任务标题来构建 prompt。模型只生成无文字背景图，标题由本地 Pillow 后处理叠加，避免 AI 直接生成中文时出现错字或乱码。

输出文件：

- 竖版候选：`cover_9x16_01.jpg`、`cover_9x16_02.jpg` ...
- 横版候选：`cover_16x9_01.jpg`、`cover_16x9_02.jpg` ...
- 选中封面：`cover_vertical.jpg`、`cover_landscape.jpg`

默认会生成 3 张竖版和 3 张横版候选。选择 5 张会增加 API 用量。常见失败原因包括未配置 `COVER_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`、模型或图片接口不兼容、额度不足、网络失败或图片接口拒绝生成。健康检查页会把 `Pillow` 和当前服务商所需的 API Key 标记为封面相关的可选检查项。

## 可选增强模块

这些能力都是 job 的手动附加功能，不加入默认 pipeline，也不会改变 job 审核状态。

- 平台拆分：在任务详情页选择抖音、B站或 YouTube Shorts，将 `final.mp4` 或 `review.mp4` 拆成符合平台时长限制的 `segments/<platform>_part_01.mp4`，并写入 `segments_manifest.json`。
- 字幕翻译：可以使用 `LLM_PROVIDER=openai` + `OPENAI_API_KEY`，也可以使用 `LLM_PROVIDER=google`、`LLM_MODEL=gemini-2.5-flash` 和 `GOOGLE_API_KEY`。输出 `transcript_translated_<lang>.json/.txt/.srt`、`subtitles_translated_<lang>.ass`；带翻译字幕的 `final_translated_<lang>.mp4` 通过单独按钮后台渲染，避免长视频渲染阻塞翻译请求。
- AI 元数据：使用同一组 OpenAI 或 Google 文本模型配置生成可编辑的 `metadata.json`，包含标题、简介、标签、话题和封面标题建议。
- 语义高光：使用同一组 LLM 配置生成 `highlights.json`，并尽量把 `semantic_score` 附加到 clips 上，作为现有剪辑评分的补充。
- 发布中心 v1：生成 `publish_package.json`，并为抖音 / B站输出 `publish_packages/douyin/`、`publish_packages/bilibili/` 交接目录，包含标题、简介、标签、话题、本地视频路径、上传清单和平台元数据文件。它还会生成 `publish_extension_manifest.json`，并通过 `GET /publish/packages` 暴露给可信浏览器插件读取本地交接数据。当前不会登录账号或自动上传。
- 剪辑工程导出：生成 `project_export_manifest.json`，并输出 `project_exports/premiere/premiere_timeline.xml` 供 Premiere Pro 导入；同时输出 `project_exports/jianying_package/` 作为剪映/CapCut 稳定素材包。剪映包不是剪映草稿工程。

URL 下载和直播录制已从 Web 工作流移除。新建任务页现在聚焦本地路径、拖拽导入，以及 `input\recordings` 中已有的录播文件，这样剪辑和封面生成流程更轻、更稳定。

## 竖屏输出

竖屏最终视频目标分辨率为 `1080x1920`。

支持的竖屏模式：

- `VERTICAL_MODE=blur`：保留完整主体画面，用模糊背景填满 9:16
- `VERTICAL_MODE=pad`：保留完整主体画面，用黑边补齐
- `VERTICAL_MODE=crop`：按锚点裁切填满 9:16

生成 `crop_plan.json` 时会检测稳定的源视频黑边。若录屏文件本身带有固定黑栏，worker 会先移除黑边，再进行竖屏适配。

手动锚点配置：

```text
CROP_ANCHOR_X=0.5
CROP_ANCHOR_Y=0.5
```

当前没有自动人脸或主体跟踪，锚点裁切是确定性规则。

## GPU 渲染加速

NVIDIA 显卡可以通过 FFmpeg NVENC 加速预览视频和最终成片渲染。当前 RTX 3070 Ti Laptop 环境可使用：

```text
RENDER_VIDEO_ENCODER=h264_nvenc
RENDER_OUTPUT_FPS=30
RENDER_NVENC_PRESET=p5
RENDER_NVENC_CQ=21
RENDER_NVENC_PREVIEW_PRESET=p4
RENDER_NVENC_PREVIEW_CQ=25
```

`review.mp4` 使用预览 preset/CQ，优先更快出预览；`final.mp4` 使用最终 preset/CQ，优先画质。`RENDER_OUTPUT_FPS=30` 会把直播录播常见的可变帧率时间轴统一成 30fps，并配合音频异步重采样降低音画漂移风险；设为 `0` 可保留源时间轴。若健康检查显示 `h264_nvenc` 缺失，请把 `FFMPEG_PATH` 指向支持 NVENC 的 ffmpeg，或改回 `RENDER_VIDEO_ENCODER=libx264`。

Web 详情页会优先播放 `web_preview.mp4`。它默认从 `final.mp4` 或 `review.mp4` 自动生成，压到较轻的长边 960px / 24fps，解决大文件或 60fps 成片在浏览器里卡顿的问题。上传平台或下载成片仍使用原始 `final.mp4`。

内置的抖音、B站和 YouTube Shorts 一键预设会直接生成 `final.mp4`，不再额外重复编码 `review.mp4`。如果确实需要同时保留预览文件和最终成片，请选择“自定义”并手动勾选生成预览视频。

```text
WEB_PREVIEW_ENABLED=true
WEB_PREVIEW_MAX_WIDTH=960
WEB_PREVIEW_MAX_HEIGHT=960
WEB_PREVIEW_FPS=24
WEB_PREVIEW_VIDEO_BITRATE=1200k
```

## 字幕尺寸

ASS 字幕会根据输出分辨率生成。竖屏成片会使用 `1080x1920` 的 PlayRes，并把过长转写段拆成多个字幕事件，避免一整段长句同时占据半个屏幕。

```text
ASS_MAX_LINES=2
ASS_VERTICAL_FONT_SIZE=44
```

如果觉得竖屏字幕仍然偏大，可以把 `ASS_VERTICAL_FONT_SIZE` 降到 `40` 或 `38`；如果想显示更多内容，可以把 `ASS_MAX_LINES` 调到 `3`，但不建议用于短视频成片。

## 转写

默认转写配置优先提升中文直播录播识别质量：先尝试 FunASR，失败时自动回退到 faster-whisper。

```text
WHISPER_BACKEND=funasr-whisper
WHISPER_MODEL=large-v3
WHISPER_MODEL_FALLBACKS=large-v3-turbo,medium
WHISPER_LANGUAGE=zh
WHISPER_WORD_TIMESTAMPS=true
WHISPER_VAD_FILTER=true
FASTER_WHISPER_DEVICE=cuda
FASTER_WHISPER_COMPUTE_TYPE=int8_float16
FASTER_WHISPER_BATCH_SIZE=8
```

常用可选配置：

```text
WHISPER_INITIAL_PROMPT=
SUBTITLE_REPLACEMENTS=错词=>正确词,酒馆占棋=>酒馆战棋
TRANSCRIBE_AUDIO_FILTER=highpass=f=80,lowpass=f=7600,afftdn
```

- 多语种或不确定语言的视频建议保留 `WHISPER_LANGUAGE=auto`。只有明确是中文、英文、韩文或日文时，再手动改成 `zh`、`en`、`ko` 或 `ja`。
- `WHISPER_INITIAL_PROMPT` 会传给 faster-whisper，适合补充特定语言的主播名、游戏术语和口语词；如果视频不是中文，不要使用中文 prompt，否则会干扰识别。
- `SUBTITLE_REPLACEMENTS` 会应用到 `transcript.txt`、`transcript.srt`、`transcript.json` 和 ASS 字幕。
- faster-whisper 返回词级时间戳时，`transcript.json` 会保留 `words`，剪辑内容列会优先按 clip 边界聚合词级文本。
- `TRANSCRIBE_AUDIO_FILTER` 只影响转写用的 `audio.wav`，不影响 `audio_hq.flac`。

如果本机 CUDA 或显存不稳定，可以切回 CPU 或更小模型：

```text
WHISPER_MODEL=medium
WHISPER_MODEL_FALLBACKS=small
FASTER_WHISPER_DEVICE=cpu
FASTER_WHISPER_COMPUTE_TYPE=int8
FASTER_WHISPER_BATCH_SIZE=4
```

`FASTER_WHISPER_BATCH_SIZE` 会启用 faster-whisper 的批处理推理。8GB 显存的 NVIDIA 显卡可以先试 `8`；如果遇到显存不足，就降到 `4` 或 `1`。

长视频在 Windows 上使用 `medium + cpu + int8` 会更慢，但通常更稳。

FunASR 可以作为可选的中文优先转写后端，适合普通话直播录播、标点恢复和热词偏置识别。先安装可选依赖，再在 `.env` 中切换：

```text
WHISPER_BACKEND=funasr
FUNASR_MODEL=paraformer-zh
FUNASR_VAD_MODEL=fsmn-vad
FUNASR_PUNC_MODEL=ct-punc
FUNASR_DEVICE=cuda:0
FUNASR_HOTWORDS=主播名 游戏名 专有名词
FUNASR_BATCH_SIZE_S=300
FUNASR_MAX_SEGMENT_MS=60000
FUNASR_PERSISTENT_WORKER=true
```

FunASR 第一次运行会下载模型文件。默认会保留隔离的 FunASR 子进程并复用已加载模型，后续任务不再重复支付约 20-30 秒的模型启动成本。只有排查问题时才建议设置 `FUNASR_PERSISTENT_WORKER=false`；子进程崩溃会自动重启，通信失败仍会退回原有的一次性 runner。如果本机没有 CUDA 版 PyTorch，或 CUDA 不稳定，可以改为 `FUNASR_DEVICE=cpu`。FunASR 的输出会被规范化成现有的 `transcript.txt`、`transcript.srt` 和 `transcript.json`，后续剪辑、字幕和时间线流程不需要改。

## 剪辑片段稳定化

系统会先根据静音/静止画面生成无效片段，再对保留片段做稳定化，减少短暂停顿造成的碎片跳切。

当前默认值：

```text
SILENCE_MIN_GAP_SECONDS=0.35
CUT_MIN_CLIP_SECONDS=2.0
CUT_MERGE_GAP_SECONDS=1.5
SOURCE_INTEGRITY_SCAN_ENABLED=true
SOURCE_INTEGRITY_SCAN_TIMEOUT_MULTIPLIER=3.0
SOURCE_INTEGRITY_SCAN_MAX_ERRORS=40
VISUAL_DETECT_KEYFRAMES_ONLY=true
VISUAL_DETECT_FPS=2
VISUAL_DETECT_WIDTH=480
```

`CUT_MERGE_GAP_SECONDS` 控制相邻保留片段被短间隔隔开时的合并强度。想要更平滑的粗剪可以调大；想要更紧凑的剪辑可以调小。

`SOURCE_INTEGRITY_SCAN_ENABLED` 会用 ffmpeg 做源视频解码扫描并写出 `corrupt.json`。它不会让任务失败，但 Web UI 会在源视频存在坏帧/损坏码流时提示，方便你优先重新下载/重新导出源文件，或删除受损剪辑片段后再渲染。

`VISUAL_DETECT_KEYFRAMES_ONLY` 会让静止画面/场景切换检测只解码关键帧，长录播会快很多。需要更密集的场景切换标记时可以关闭。`VISUAL_DETECT_FPS` 和 `VISUAL_DETECT_WIDTH` 只影响画面分析，不影响最终渲染画质；其中 FPS 限制只在关闭关键帧模式时使用。

## CLI 示例

健康检查：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --health
```

处理单个文件：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\to\recording.mp4" --detect-silence --detect-scenes
```

使用工作流预设：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\to\recording.mp4" --profile douyin --progress
```

监听录播目录：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --watch --profile analysis
```

续跑未完成或失败的 job：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --resume
```

查看已有 job：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --status
```

预览清理 30 天前的 job：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --dry-run
```

运行批处理示例：

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --batch ".\examples\batch.example.json" --progress
```

## 常用 CLI 参数

```text
--once <file>        处理单个媒体文件
--batch <json>       按 JSON 文件批处理
--watch              监听 input\recordings
--profile <name>     analysis / douyin / bilibili / youtube_shorts
--force              强制重跑并覆盖已有产物
--detect-silence     生成 silence.json
--detect-freeze      生成 freeze.json
--detect-scenes      生成 scene.json
--render-review      生成 review.mp4
--render-final       生成 final.mp4
--vertical           将 final.mp4 渲染为 1080x1920
--burn-subtitles     将 ASS 字幕内嵌到 final.mp4
--plan-crop          生成 crop_plan.json
--plan-uvr           生成音频分离 / UVR 计划；启用 Demucs 时执行分离
--skip-transcribe    跳过转写并创建空转写产物
--serve              启动本地 Web 控制面板和 API
--health             检查目录和工具
--status             查看已有 job
--resume             续跑未完成 job
--json               让 health/status 输出 JSON
--progress           输出 JSONL 进度事件
```

## HTTP API

启动：

```powershell
D:\video-automation\venv\Scripts\python.exe D:\video-automation\run_worker.py --serve
```

默认地址：

```text
http://127.0.0.1:8765
```

接口：

```text
GET    /
GET    /health
POST   /health/install-tools
GET    /events
GET    /recordings
POST   /recordings/upload?filename=<name>
GET    /jobs
GET    /jobs/<job-folder-name>
DELETE /jobs/<job-folder-name>
GET    /jobs/<job-folder-name>/files/<filename>
POST   /jobs/<job-folder-name>/approve
POST   /jobs/<job-folder-name>/cuts
POST   /jobs/<job-folder-name>/transcript
POST   /jobs/<job-folder-name>/rerun
POST   /jobs/<job-folder-name>/covers/generate
POST   /jobs/<job-folder-name>/covers/select
POST   /jobs/<job-folder-name>/segments/generate
POST   /jobs/<job-folder-name>/metadata/generate
POST   /jobs/<job-folder-name>/metadata
POST   /jobs/<job-folder-name>/highlights/generate
POST   /jobs/<job-folder-name>/publish/package
GET    /publish/packages
POST   /jobs/<job-folder-name>/project-export/generate
POST   /jobs/<job-folder-name>/subtitles/translate
POST   /process
POST   /process/batch
```

`POST /process` 接收 JSON 并立即返回 job，处理会在后台继续。前端或自动化工具可以通过 `GET /jobs/<job-folder-name>` 查询状态，也可以订阅 `GET /events` 接收 SSE 实时事件。

删除、重跑、剪辑点编辑和转写编辑等会修改 job 产物的接口，会拒绝仍在处理中的 job，并返回 `409 Conflict`。JSON 请求体会做格式校验，非法 JSON 返回 `400`。

## 配置

配置优先读取 `.env`，没有 `.env` 时读取 `.env.example`。环境变量优先级最高，即使显式设置为空字符串也会生效。`.env` 和 `.env.example` 会按文件时间戳和大小缓存，重复 `Settings.load()` 不会反复读取文件；编辑配置文件后，下次读取会自动刷新缓存。

Web 设置页可以直接保存高频常用项，例如转写模型/语言、剪辑阈值、字幕样式、预览渲染、AI 封面 provider/model、LLM 模型、批量任务上限和浏览器上传大小限制。保存时只会写入白名单内的 `.env` key，并热重载当前 API；未来新任务和增强操作会使用新配置，已经运行中的任务仍保持启动时配置。更高级的路径、部署项和完整手动控制仍可直接编辑 `.env`。

重要配置：

```text
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
AUDIOWAVEFORM_PATH=audiowaveform
WHISPER_BACKEND=funasr-whisper
WHISPER_MODEL=large-v3
WHISPER_MODEL_FALLBACKS=large-v3-turbo,medium
WHISPER_LANGUAGE=auto
WHISPER_WORD_TIMESTAMPS=true
WHISPER_VAD_FILTER=true
FASTER_WHISPER_DEVICE=cuda
FASTER_WHISPER_COMPUTE_TYPE=int8_float16
FASTER_WHISPER_BATCH_SIZE=8
FUNASR_MODEL=paraformer-zh
FUNASR_VAD_MODEL=fsmn-vad
FUNASR_PUNC_MODEL=ct-punc
FUNASR_DEVICE=cuda:0
FUNASR_HOTWORDS=
CUT_MIN_CLIP_SECONDS=2.0
CUT_MERGE_GAP_SECONDS=1.5
SOURCE_INTEGRITY_SCAN_ENABLED=true
SOURCE_INTEGRITY_SCAN_TIMEOUT_MULTIPLIER=3.0
SOURCE_INTEGRITY_SCAN_MAX_ERRORS=40
VISUAL_DETECT_KEYFRAMES_ONLY=true
VISUAL_DETECT_FPS=2
VISUAL_DETECT_WIDTH=480
API_HOST=127.0.0.1
API_PORT=8765
API_PARALLEL_JOBS=2
API_BATCH_LIMIT=30
RECORDING_UPLOAD_MAX_BYTES=21474836480
API_ALLOWED_ORIGINS=
LLM_PROVIDER=openai
LLM_MODEL=
LLM_TRANSLATION_BATCH_SIZE=24
LLM_TRANSLATION_BATCH_CHARS=6000
GOOGLE_API_KEY=
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com/v1beta
PUBLISH_ENABLED=false
PUBLISH_PROVIDERS=
VERTICAL_MODE=blur
ASS_FONT_NAME=Microsoft YaHei
ASS_FONT_SIZE=56
COVER_PROVIDER=openai
COVER_MODEL=gpt-image-2
COVER_COUNT=3
COVER_ASPECTS=9:16,16:9
COVER_QUALITY=medium
COVER_OUTPUT_FORMAT=jpeg
COVER_TITLE_FONT=Microsoft YaHei
COVER_BASE_URL=https://api.openai.com/v1
COVER_API_KEY=
COVER_HTTP_REFERER=
COVER_APP_TITLE=Video Automation
OPENAI_API_KEY=
UVR_PATH=
AUDIO_SEPARATION_ENGINE=plan
DEMUCS_PATH=demucs
DEMUCS_MODEL=htdemucs
DEMUCS_DEVICE=auto
AUDIO_SEPARATION_TIMEOUT_SECONDS=7200
```

`AUDIOWAVEFORM_PATH` 是可选工具。缺失时主流程仍可运行，worker 会从 `audio.wav` 生成简化波形，Web 时间线仍可显示音频节奏。

`UVR_PATH` 是外部 Ultimate Vocal Remover 的旧式可选集成入口。不使用该工具时留空即可。Windows 示例：

```text
UVR_PATH=F:\Ultimate Vocal Remover\UVR.exe
```

原生音频分离默认关闭，请保持 `AUDIO_SEPARATION_ENGINE=plan`。如果要让 `plan_uvr` 阶段实际运行 Demucs：

```powershell
.\venv\Scripts\python.exe -m pip install demucs
```

然后设置：

```text
AUDIO_SEPARATION_ENGINE=demucs
DEMUCS_PATH=demucs
DEMUCS_MODEL=htdemucs
DEMUCS_DEVICE=auto
```

启用后，每个 job 会生成 `uvr/vocals.wav` 和 `uvr/instrumental.wav`。Demucs 属于可选重型依赖，不会打包进 Lite 桌面版。

## 与 n8n / 扣子集成

推荐方式：

1. 外部平台收集待处理视频路径。
2. 生成批处理 JSON 文件。
3. 调用本地 worker：

```powershell
D:\video-automation\venv\Scripts\python.exe D:\video-automation\run_worker.py --batch "D:\path\to\batch.json" --progress
```

4. 按行读取 stdout 的 JSONL 进度事件。
5. 完成后读取 job 目录里的 `cuts.json`、`review.mp4` 或 `final.mp4`。

也可以直接调用本地 HTTP API。

## 当前边界

- 原始录播不会被修改。
- 这个项目更适合做录播整理、粗剪建议和本地批处理，不是完整非线性剪辑软件。
- UVR/Demucs、Webhook、自动平台发布和发布连接器默认不执行；当前发布模块只生成手动上传用发布包。
- 竖屏裁切支持固定锚点和去源黑边，但不包含自动人脸跟踪。
- 精细剪辑、复杂花字字幕和多轨音频设计仍建议交给专业剪辑软件完成。

示例文件放在 `examples/`，设计和前端审查文档放在 `docs/reviews/`。

## 开源协议

本项目使用 MIT License 开源，详情见 `LICENSE`。

本项目可选调用的第三方工具、模型、字体和 API 可能有各自的许可证或服务条款。简要说明见 `NOTICE`。

英文说明见 `README.md`。
