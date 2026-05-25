# Video Automation 中文说明

Video Automation 是一个本地录播到粗剪成片的自动化工作流。它可以接收本地视频文件，为每个素材创建独立 job，完成媒体探测、音频提取、语音转写、剪辑建议、字幕生成、预览视频和最终视频导出。原始录播不会被修改。

## 当前功能

- 本地 Web 控制面板：`http://127.0.0.1:8765/#/`
- 新建任务页支持拖拽视频导入到 `input\recordings`
- 支持一次拖入多个视频，批量导入并批量提交任务
- 大文件导入时显示上传百分比进度
- 支持从 `input\recordings` 中直接选择已有录播
- 工作流预设：只分析、抖音、B 站、YouTube Shorts
- Dashboard 展示任务状态、进度、缩略图和快速入口
- 任务详情页展示 Pipeline、视频预览、时间线、转写文本、剪辑点编辑器和下载区
- 剪辑点可在线编辑，保存后自动重渲染预览视频
- 转写文本可在线编辑，转写时间戳可点击跳转到视频预览位置
- 支持 AI 生成视频封面，可生成竖版和横版候选图后手动选择
- 支持可选增强模块：平台拆分、AI 标题/简介/标签、语义高光、下载队列、发布包、剪辑工程导出
- 时间线波形优先使用 `audiowaveform`，缺失时自动用 Python 从 `audio.wav` 生成简化波形
- 剪辑建议会自动合并过碎片段和短间隔跳切，降低复查负担
- 支持审核流程，可将待审核 job 标记为完成
- 健康检查页和设置页展示本地工具、路径和配置
- 视频/音频文件端点支持 HTTP Range，浏览器预览可以拖动进度条
- 提供本地 HTTP API，方便 n8n、扣子或其他自动化工具调度
- 支持 `API_PARALLEL_JOBS` 控制并行处理数量
- 提供 CLI worker，支持单文件、监听、批处理、续跑、清理和状态查询

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
subtitles.ass             带样式的 ASS 字幕
subtitles_clipped.ass     按剪辑点重映射后的 ASS 字幕
corrupt.json              源视频解码完整性扫描结果
silence.json              静音检测结果
freeze.json               静止画面检测结果
scene.json                场景切换检测结果
cuts.json                 结构化剪辑建议和编辑后的 clips
cuts.md                   人工复查剪辑单
crop_plan.json            竖屏画面适配计划
uvr_plan.json             人声/BGM 分离计划契约
render_preview.json       预览视频渲染计划
review.mp4                预览视频
final_render_preview.json 最终视频渲染计划
final.mp4                 最终视频
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

`uvr_plan.json` 目前是集成计划契约，不会自动执行人声分离。

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
- 在任务详情页手动生成平台分段、AI 元数据、语义高光和发布包
- `DOWNLOAD_ENABLED=true` 时，可在新建任务页粘贴视频 URL 下载，下载完成后创建任务
- 审核通过后标记任务完成
- 下载主要产物和高级 JSON 产物

界面文案使用创作者更容易理解的说法。例如 `render_review` 显示为“生成预览视频”，`burn_subtitles` 显示为“内嵌字幕”。

## AI 视频封面

任务详情页包含可选的 AI 封面面板。封面生成是 job 的附加能力，不加入主剪辑 pipeline，也不会改变 `needs_review`、`done` 或 `failed` 状态。

使用前需要在 `.env` 中配置 OpenAI API key：

```text
COVER_PROVIDER=openai
COVER_MODEL=gpt-image-2
COVER_COUNT=3
COVER_ASPECTS=9:16,16:9
COVER_QUALITY=medium
COVER_OUTPUT_FORMAT=jpeg
COVER_TITLE_FONT=Microsoft YaHei
OPENAI_API_KEY=sk-...
```

生成时会读取 `manifest.json`、`cuts.json`、`transcript.json` 和任务标题来构建 prompt。模型只生成无文字背景图，标题由本地 Pillow 后处理叠加，避免 AI 直接生成中文时出现错字或乱码。

输出文件：

- 竖版候选：`cover_9x16_01.jpg`、`cover_9x16_02.jpg` ...
- 横版候选：`cover_16x9_01.jpg`、`cover_16x9_02.jpg` ...
- 选中封面：`cover_vertical.jpg`、`cover_landscape.jpg`

默认会生成 3 张竖版和 3 张横版候选。选择 5 张会增加 API 用量。常见失败原因包括未配置 `OPENAI_API_KEY`、额度不足、网络失败或图片接口拒绝生成。健康检查页会把 `Pillow` 和 `OPENAI_API_KEY` 标记为封面相关的可选检查项。

## 可选增强模块

这些能力都是 job 的手动附加功能，不加入默认 pipeline，也不会改变 job 审核状态。

- 平台拆分：在任务详情页选择抖音、B站或 YouTube Shorts，将 `final.mp4` 或 `review.mp4` 拆成符合平台时长限制的 `segments/<platform>_part_01.mp4`，并写入 `segments_manifest.json`。
- AI 元数据：配置 `LLM_PROVIDER=openai`、`LLM_MODEL=<model>` 和 `OPENAI_API_KEY` 后，可生成可编辑的 `metadata.json`，包含标题、简介、标签、话题和封面标题建议。
- 语义高光：使用同一组 LLM 配置生成 `highlights.json`，并尽量把 `semantic_score` 附加到 clips 上，作为现有剪辑评分的补充。
- 视频下载：配置 `DOWNLOAD_ENABLED=true` 和 `YTDLP_PATH=yt-dlp` 后，新建任务页会启用 URL 下载队列。下载文件保存在 `input\downloads`，完成后可导入现有 job 流程。
- 发布包：生成 `publish_package.json`，用于手动上传平台。当前不会调用 B站或抖音发布 API。
- 剪辑工程导出：生成 `project_export_manifest.json`，并输出 `project_exports/premiere/premiere_timeline.xml` 供 Premiere Pro 导入；同时输出 `project_exports/jianying_package/` 作为剪映/CapCut 稳定素材包。剪映包不是剪映草稿工程。

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
RENDER_NVENC_PRESET=p5
RENDER_NVENC_CQ=21
RENDER_NVENC_PREVIEW_PRESET=p4
RENDER_NVENC_PREVIEW_CQ=25
```

`review.mp4` 使用预览 preset/CQ，优先更快出预览；`final.mp4` 使用最终 preset/CQ，优先画质。若健康检查显示 `h264_nvenc` 缺失，请把 `FFMPEG_PATH` 指向支持 NVENC 的 ffmpeg，或改回 `RENDER_VIDEO_ENCODER=libx264`。

## 字幕尺寸

ASS 字幕会根据输出分辨率生成。竖屏成片会使用 `1080x1920` 的 PlayRes，并把过长转写段拆成多个字幕事件，避免一整段长句同时占据半个屏幕。

```text
ASS_MAX_LINES=2
ASS_VERTICAL_FONT_SIZE=44
```

如果觉得竖屏字幕仍然偏大，可以把 `ASS_VERTICAL_FONT_SIZE` 降到 `40` 或 `38`；如果想显示更多内容，可以把 `ASS_MAX_LINES` 调到 `3`，但不建议用于短视频成片。

## 转写

默认转写配置优先提升中文直播录播识别质量：

```text
WHISPER_BACKEND=faster-whisper
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
WHISPER_INITIAL_PROMPT=以下是中文直播录播，可能包含主播名、游戏术语、弹幕口语和网络用语。
SUBTITLE_REPLACEMENTS=错词=>正确词,酒馆占棋=>酒馆战棋
TRANSCRIBE_AUDIO_FILTER=highpass=f=80,lowpass=f=7600,afftdn
```

- `WHISPER_INITIAL_PROMPT` 会传给 faster-whisper，帮助识别直播口语和专有词。
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
```

FunASR 第一次运行会下载模型文件。如果本机没有 CUDA 版 PyTorch，或 CUDA 不稳定，可以改为 `FUNASR_DEVICE=cpu`。FunASR 的输出会被规范化成现有的 `transcript.txt`、`transcript.srt` 和 `transcript.json`，后续剪辑、字幕和时间线流程不需要改。

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
--plan-uvr           生成 uvr_plan.json
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
POST   /jobs/<job-folder-name>/project-export/generate
GET    /downloads
POST   /downloads
POST   /downloads/<download-id>/import
POST   /process
POST   /process/batch
```

`POST /process` 接收 JSON 并立即返回 job，处理会在后台继续。前端或自动化工具可以轮询 `GET /jobs/<job-folder-name>` 查看状态。

删除、重跑、剪辑点编辑和转写编辑等会修改 job 产物的接口，会拒绝仍在处理中的 job，并返回 `409 Conflict`。JSON 请求体会做格式校验，非法 JSON 返回 `400`。

## 配置

配置优先读取 `.env`，没有 `.env` 时读取 `.env.example`。

重要配置：

```text
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
AUDIOWAVEFORM_PATH=audiowaveform
WHISPER_BACKEND=faster-whisper
WHISPER_MODEL=large-v3
WHISPER_MODEL_FALLBACKS=large-v3-turbo,medium
WHISPER_LANGUAGE=zh
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
API_ALLOWED_ORIGINS=
INPUT_DOWNLOADS_DIR=D:\video-automation\input\downloads
DOWNLOAD_ENABLED=false
YTDLP_PATH=yt-dlp
LLM_PROVIDER=openai
LLM_MODEL=
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
OPENAI_API_KEY=
```

`AUDIOWAVEFORM_PATH` 是可选工具。缺失时主流程仍可运行，worker 会从 `audio.wav` 生成简化波形，Web 时间线仍可显示音频节奏。

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
- UVR、Webhook、自动平台发布和发布连接器默认不执行；当前发布模块只生成手动上传用发布包。
- 竖屏裁切支持固定锚点和去源黑边，但不包含自动人脸跟踪。
- 精细剪辑、复杂花字字幕和多轨音频设计仍建议交给专业剪辑软件完成。

示例文件放在 `examples/`，设计和前端审查文档放在 `docs/reviews/`。

英文说明见 `README.md`。
