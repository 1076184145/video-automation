# Video Automation

Turn long livestream recordings into reviewable rough cuts, subtitles, covers, and publish handoff packages on your own machine.

Video Automation is built first for livestream/game/education creators and small teams that batch-process recordings. It watches or accepts local media, creates one isolated job folder per source video, analyzes the media, transcribes speech, suggests cuts, and can render preview or final videos while keeping the original recording untouched.

Fastest path to value:

1. Start the desktop app or local server and open the Health page.
2. Use one-click repair if FFmpeg or other required portable tools are missing.
3. Drag in a video, choose a creator profile such as Douyin or Bilibili, and start processing.
4. Review the suggested clips, export the final video, then use the publish handoff package for upload.

For non-technical users, most setup lives in the Health and Settings pages. The `.env` file remains available for expert-only tuning.

## Quick Start: Run Locally

### Option A: Windows desktop build

If you downloaded a release package or installer:

1. Install or unzip Video Automation.
2. Start `VideoAutomationLite.exe`.
3. Open the Health page when prompted.
4. Click **Auto-fix Dependencies** if FFmpeg or FFprobe is missing.
5. Go to **New Job**, drag in a video, choose a profile, and start processing.

The desktop app runs a local server in the background and opens the Web UI for you. Your videos, job outputs, and API keys stay on your machine unless you explicitly use an external AI provider.

### Option B: Run from source

Prerequisites:

- Windows 10/11, macOS, or Linux.
- Python 3.11+.
- FFmpeg and FFprobe. On Windows, the Health page can install portable copies into `tools\bin`.
- Optional: NVIDIA CUDA for faster transcription/rendering.

Setup:

```powershell
git clone https://github.com/<your-name>/<your-repo>.git
cd <your-repo>
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the local Web app:

```powershell
.\venv\Scripts\python.exe .\run_worker.py --serve
```

Then open:

```text
http://127.0.0.1:8765/#/
```

First run checklist:

1. Open **Health** and fix missing required tools.
2. Open **Settings** only if you want to change model, GPU, subtitle, render, or AI options.
3. Open **New Job** and drag in a video or enter a local file path.
4. Wait for the job to reach **Needs review** or **Done**.
5. Open the job page, review clips, then download `final.mp4`, subtitles, covers, or publish handoff files.

The basic local workflow works with the built-in defaults. If you need AI covers, translation, or custom model settings, open the Web Settings page and fill in the required fields there.

### Optional AI features

AI cover generation, semantic highlights, metadata, and subtitle translation require your own provider key. Configure keys in the Web Settings page or in a private `.env` file:

```text
OPENAI_API_KEY=
GOOGLE_API_KEY=
COVER_API_KEY=
```

Leave these blank if you only need local media processing, transcription, subtitles, and rendering.

## Current Features

- Local Web dashboard at `http://127.0.0.1:8765/#/`
- Drag-and-drop video import into `input\recordings`
- Batch drag-and-drop import and submission, with batch-level progress grouped on the dashboard
- Upload progress while importing large media files in the Web UI
- Recording picker for files already in `input\recordings`
- Workflow profiles: analysis, Douyin, Bilibili, YouTube Shorts
- Job cards with status, progress, thumbnails, and quick navigation
- Job detail page with pipeline status, video preview, timeline, transcript, clip editor, and downloads
- Real-time status updates through Server-Sent Events, with title badges and optional browser notifications for completed jobs
- Editable cut list with automatic preview re-render after saving
- Editable transcript text with timestamp-to-preview seeking
- AI video cover generation with portrait and landscape candidates
- Optional enhancement modules for platform segmentation, subtitle translation, AI metadata, semantic highlights, project export, and publish packages
- Timeline waveform data, using `audiowaveform` when available and a Python WAV fallback otherwise
- Clip stabilization that merges tiny fragments and short-gap jump cuts before review
- Approve/review flow for marking a job as complete
- Health page for local tool checks, one-click portable tool repair, and Settings page for saving common runtime options
- HTTP Range support for video/audio preview seeking
- Local HTTP API for automation tools such as n8n or Coze
- Controlled parallel processing with `API_PARALLEL_JOBS`
- CLI worker for single-file, watch, batch, resume, cleanup, and status workflows
- Standard-library unit tests for core parsing, cut planning, subtitle, and configuration helpers

## Project Structure

See `docs/PROJECT_STRUCTURE.md` for the current file layout, module responsibility map, and runtime directory notes.

## Processing Pipeline

Typical pipeline stages:

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

Not every stage runs for every job. Optional stages depend on CLI flags, Web options, or the selected workflow profile.

## Job Outputs

Each source file creates a folder under:

```text
D:\video-automation\processing\jobs\<timestamp-source-name>\
```

Common outputs:

```text
job.json                  Job state, stage progress, errors
job.log                   Per-job log
manifest.json             ffprobe media info and quick fingerprint
thumbnail.jpg             Dashboard thumbnail
audio.wav                 Whisper/transcription audio, optionally filtered
audio_hq.flac             High-quality audio copy for editing
waveform.json             Timeline waveform data from audiowaveform or Python fallback
transcript.txt            Plain text transcript
transcript.srt            SRT subtitle file
transcript.json           Structured transcript segments, with word timestamps when enabled
transcript_translated_zh.* Optional translated transcript JSON/TXT/SRT output
subtitles.ass             Styled ASS subtitle file
subtitles_clipped.ass     Subtitle file remapped to edited clips
subtitles_translated_zh*.ass Optional translated ASS subtitle output
corrupt.json              Source video decode integrity scan
silence.json              Silence detection output
freeze.json               Freeze/static-frame detection output
scene.json                Scene-change detection output
cuts.json                 Structured cut suggestions and edited clips
cuts.md                   Human-readable cut sheet
crop_plan.json            Vertical framing plan
uvr_plan.json             Vocal/BGM separation plan/status
render_preview.json       Preview render command plan
review.mp4                Preview video
final_render_preview.json Final render command plan
final.mp4                 Final rendered video
final_translated_zh.mp4   Optional final render with translated subtitles
web_preview.mp4           Lightweight Web playback proxy; final quality is unchanged
web_preview.json          Web playback proxy render metadata
cover_manifest.json       AI cover generation status and candidate metadata
cover_9x16_01.jpg         Portrait cover candidates
cover_16x9_01.jpg         Landscape cover candidates
cover_vertical.jpg        Selected portrait cover
cover_landscape.jpg       Selected landscape cover
segments_manifest.json    Optional platform segment manifest
segments/*.mp4            Optional platform-sized video parts
metadata.json             Optional AI title/description/tag metadata
highlights.json           Optional LLM semantic highlights
publish_package.json      Optional manual upload package manifest
project_export_manifest.json Optional Premiere/Jianying export manifest
project_exports/*         Optional editor handoff files
```

`uvr_plan.json` is plan-only by default. If you install Demucs and set `AUDIO_SEPARATION_ENGINE=demucs`, the same `plan_uvr` stage runs audio separation and writes `uvr/vocals.wav` plus `uvr/instrumental.wav`.

## Web Dashboard

Start the local server:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --serve
```

Open:

```text
http://127.0.0.1:8765/#/
```

The Web UI supports:

- Drag a video onto the New Job page
- Drag multiple videos to import and submit them as a batch
- See upload progress while a large file is imported
- Select an existing file from `input\recordings`
- Pick a workflow profile
- Choose transcription language
- Enable silence, freeze, scene detection, preview render, final render, vertical 9:16, embedded subtitles, and skip-transcribe options
- Review and edit clips
- Click transcript timestamps to seek the preview video
- Edit transcript text directly and regenerate subtitles/preview
- Preview `review.mp4` or `final.mp4`
- Generate 3 or 5 AI cover candidates, then choose portrait and landscape covers
- Generate optional platform-sized segments, translated subtitles, AI metadata, semantic highlights, and manual publish packages
- Download creator-facing outputs and advanced JSON outputs
- Real-time job updates without polling. When the tab is hidden, completed/review/failed jobs update the browser title badge; if browser notifications have already been granted, a system notification is also shown.

UI labels use creator-facing terms. For example, `render_review` appears as preview video, and `burn_subtitles` appears as embedded subtitles.

## Desktop Launcher

Phase 1 desktop support is a lightweight Python shell around the existing API and Web UI.

Install the optional desktop dependency:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe -m pip install pywebview
```

Start the desktop launcher:

```powershell
.\venv\Scripts\python.exe .\desktop_app.py
```

`desktop_app.py` starts the same local API server and opens `http://127.0.0.1:8765/#/` in a native WebView window. If `pywebview` is not installed, it falls back to the system browser. If the API port is already in use, it assumes the service is already running and opens the UI without starting a second server.

To build the recommended lightweight Windows onedir package:

```powershell
cd D:\video-automation
.\tools\build_desktop.ps1 -InstallDeps -Lite -Clean
```

The lite bundle excludes heavy optional ML libraries such as torch, FunASR, SciPy, and ModelScope. This keeps the desktop shell portable and small enough for first-pass distribution. To build a full bundle that includes whatever optional ML packages are currently installed in the venv, omit `-Lite`:

```powershell
.\tools\build_desktop.ps1 -Clean
```

To create a zip package from the desktop bundle:

```powershell
.\tools\package_desktop.ps1 -Lite -SkipBuild
```

Omit `-SkipBuild` if you want the script to rebuild before packaging. The zip is written to `dist\releases\`.

To build a normal Windows installer, install Inno Setup 6 and run:

```powershell
.\tools\build_installer.ps1 -Version 0.1.0
```

The installer is written to `dist\installers\`. It installs the Lite desktop bundle under the current user's local app data folder, so it does not require administrator privileges. If Inno Setup is installed in a custom location, set `INNO_SETUP_COMPILER` to the full path of `ISCC.exe`.

Portable command-line tools can be placed in `tools\bin` before running from source or building the desktop package:

```text
tools\bin\ffmpeg.exe
tools\bin\ffprobe.exe
tools\bin\audiowaveform.exe
```

On Windows, you can download the common portable tools with:

```powershell
.\tools\install_desktop_tools.ps1
```

This downloads `ffmpeg.exe` and `ffprobe.exe` into `tools\bin`. Use `-Force` to replace existing files or `-SkipFfmpeg` to skip installation.

The same repair flow is available inside the Web UI: open the Health page and click **Auto-fix Dependencies** when `ffmpeg` or `ffprobe` is missing. The backend runs `tools\install_desktop_tools.ps1` in the background and streams progress through SSE, then refreshes the health check automatically.

Explicit Settings / `.env` paths still take priority. If no portable executable is found, the app falls back to the normal command name on PATH. You can check the current resolution with:

```powershell
.\tools\check_desktop_tools.ps1
```

The generated bundle includes the Web assets and `.env.example`, but it intentionally does not bundle your `.env` secrets. Keep models and API keys configured through the Settings page or `.env`. The Health page can install the common portable tools into `tools\bin`; optional ML libraries and API keys remain user-managed.

## Testing

The project uses Python's standard-library `unittest` for the initial test suite, so no test dependency is required.

Run the current regression checks:

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

Current tests cover configuration loading, ffmpeg parser helpers, cut planning helpers, transcript-to-clip association, ASS subtitle formatting, subtitle timeline remapping, and subtitle wrapping. Add new tests under `tests\` when changing core pipeline logic.

## AI Video Covers

The Job Detail page includes an optional AI cover panel. Cover generation is separate from the main editing pipeline, so it does not change `needs_review`, `done`, or `failed` job state.

Configure an image API key before using it. Official OpenAI works with the defaults. OpenAI-compatible Images API gateways can be used by changing `COVER_BASE_URL` and `COVER_API_KEY`. OpenRouter is also supported through Chat Completions image output. Google AI Studio is supported through the native Gemini `generateContent` API.

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

If `COVER_API_KEY` is empty, the worker falls back to `OPENAI_API_KEY`. This lets you use one key for official OpenAI covers, or a separate third-party key for cover generation while keeping text LLM settings independent. `COVER_HTTP_REFERER` is optional; leave it blank unless your gateway asks for a referer header. For local use, `http://127.0.0.1:8765` is a reasonable value.

OpenRouter example:

```text
COVER_PROVIDER=openrouter
COVER_BASE_URL=https://openrouter.ai/api/v1
COVER_API_KEY=YOUR_OPENROUTER_KEY
COVER_MODEL=google/gemini-2.5-flash-image
COVER_MODALITIES=image,text
COVER_HTTP_REFERER=http://127.0.0.1:8765
COVER_APP_TITLE=Video Automation
```

For OpenRouter, choose a model whose `output_modalities` includes `image`; OpenRouter recommends checking models via `https://openrouter.ai/api/v1/models?output_modalities=image`.

Google AI Studio example:

```text
GOOGLE_API_KEY=YOUR_GOOGLE_API_KEY
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com/v1beta
COVER_PROVIDER=google
COVER_MODEL=gemini-2.5-flash-image
```

For Google covers, `COVER_API_KEY` takes precedence when set; otherwise the worker uses `GOOGLE_API_KEY`. Each candidate is one Gemini image request, then Pillow performs the exact 9:16 or 16:9 crop and local title overlay. The image model may be downloaded or changed by Google over time, so use a currently available image-capable Gemini model from Google AI Studio.

The worker builds a prompt from `manifest.json`, `cuts.json`, `transcript.json`, and the job title. The model generates background images without readable text, then local post-processing adds the chosen title with a deterministic font overlay. This avoids unreliable AI-generated Chinese text.

Outputs:

- Portrait candidates: `cover_9x16_01.jpg`, `cover_9x16_02.jpg`, ...
- Landscape candidates: `cover_16x9_01.jpg`, `cover_16x9_02.jpg`, ...
- Selected files: `cover_vertical.jpg` and `cover_landscape.jpg`

The default request creates 3 portrait and 3 landscape candidates. Choosing 5 candidates increases API usage. Common failures are missing `COVER_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`, an incompatible model or endpoint, insufficient quota, network errors, or content rejection by the image API. The Health page marks `Pillow` and the selected provider key as optional cover-related checks.

## Optional Enhancement Modules

These modules are manual job add-ons. They do not run inside the default pipeline and do not change job review status.

- Platform segments: choose Douyin, Bilibili, or YouTube Shorts in Job Detail to create `segments_manifest.json` and `segments/<platform>_part_01.mp4` files from `final.mp4` or `review.mp4`.
- Subtitle translation: use either `LLM_PROVIDER=openai` with `OPENAI_API_KEY`, or `LLM_PROVIDER=google`, `LLM_MODEL=gemini-2.5-flash`, and `GOOGLE_API_KEY`. Outputs include `transcript_translated_<lang>.json/.txt/.srt` and `subtitles_translated_<lang>.ass`; rendering `final_translated_<lang>.mp4` is a separate button so long renders do not block translation.
- AI metadata: use the same OpenAI or Google text configuration to generate editable `metadata.json` with title, description, tag, hashtag, and cover-title ideas.
- Semantic highlights: uses the same LLM settings to write `highlights.json` and attach `semantic_score` to clips where possible.
- Publish center v1: creates `publish_package.json` plus `publish_packages/douyin/` and `publish_packages/bilibili/` handoff folders with title, description, tags, hashtags, local video path, checklist, and platform metadata files. It also writes `publish_extension_manifest.json` and exposes `GET /publish/packages` for a trusted browser extension to read local handoff data. It does not log in or upload automatically.
- Project export: creates `project_export_manifest.json` plus `project_exports/premiere/premiere_timeline.xml` for Premiere Pro import and `project_exports/jianying_package/` for Jianying/CapCut manual import. The Jianying package is not a proprietary draft project.

URL downloading and livestream recording have been removed from the Web workflow. New Job now focuses on local paths, drag-and-drop import, and files already present in `input\recordings`, which keeps the editing and cover-generation loop lighter and more predictable.

## Vertical Output

Vertical rendering targets `1080x1920`.

Supported vertical modes:

- `VERTICAL_MODE=blur`: preserve the source frame as foreground and fill the 9:16 background with a blurred duplicate
- `VERTICAL_MODE=pad`: preserve the source frame with black padding
- `VERTICAL_MODE=crop`: crop to fill 9:16 using configured anchors

The crop planner can remove stable black borders from the source before fitting or cropping. This is useful for recordings that contain black bars from capture software.

Manual anchor settings are available for deterministic crop mode:

```text
CROP_ANCHOR_X=0.5
CROP_ANCHOR_Y=0.5
```

This is not automatic face tracking.

## GPU Render Acceleration

NVIDIA GPUs can speed up preview and final rendering through FFmpeg NVENC. The current RTX 3070 Ti Laptop setup can use:

```text
RENDER_VIDEO_ENCODER=h264_nvenc
RENDER_OUTPUT_FPS=30
RENDER_NVENC_PRESET=p5
RENDER_NVENC_CQ=21
RENDER_NVENC_PREVIEW_PRESET=p4
RENDER_NVENC_PREVIEW_CQ=25
```

`review.mp4` uses the preview preset/CQ so it appears faster. `final.mp4` uses the final preset/CQ for better quality. `RENDER_OUTPUT_FPS=30` normalizes common VFR livestream timestamps to 30fps and combines with async audio resampling to reduce A/V drift; set it to `0` to preserve source timing. If health check reports `h264_nvenc` as missing, either point `FFMPEG_PATH` to a build with NVENC support or switch back to `RENDER_VIDEO_ENCODER=libx264`.

The Web job detail page prefers `web_preview.mp4` for playback. It is generated automatically from `final.mp4` or `review.mp4` and defaults to a lighter 960px-long-edge / 24fps proxy, which keeps large or 60fps renders smooth in the browser. Platform upload and downloads still use the original `final.mp4`.

The built-in Douyin, Bilibili, and YouTube Shorts one-click profiles render `final.mp4` directly and do not also render a redundant `review.mp4`. Choose the custom profile and enable preview rendering when you explicitly need both files.

```text
WEB_PREVIEW_ENABLED=true
WEB_PREVIEW_MAX_WIDTH=960
WEB_PREVIEW_MAX_HEIGHT=960
WEB_PREVIEW_FPS=24
WEB_PREVIEW_VIDEO_BITRATE=1200k
```

## Transcription

Default transcription settings prioritize Chinese livestream accuracy by trying FunASR first and falling back to faster-whisper:

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

Useful optional settings:

```text
WHISPER_INITIAL_PROMPT=
SUBTITLE_REPLACEMENTS=wrong term=>correct term,misheard phrase=>correct phrase
TRANSCRIBE_AUDIO_FILTER=highpass=f=80,lowpass=f=7600,afftdn
```

Leave `WHISPER_LANGUAGE=auto` for mixed-language recordings. Set it to `zh`, `en`, `ko`, or `ja` only when the source language is known. Use `WHISPER_INITIAL_PROMPT` only for language-specific terms; a Chinese prompt can hurt Korean, English, or mixed-language transcription.

`SUBTITLE_REPLACEMENTS` is applied to `transcript.txt`, `transcript.srt`, `transcript.json`, and ASS subtitle output. Word timestamps are stored in `transcript.json` when faster-whisper returns them, and cut summaries prefer word-level text when mapping transcript content to clips.

ASS subtitle sizing is resolution-aware. For vertical output, `subtitles_clipped.ass` is regenerated against `1080x1920`, long transcript segments are split over time, and each on-screen subtitle is capped by:

```text
ASS_MAX_LINES=2
ASS_VERTICAL_FONT_SIZE=44
```

If CUDA or VRAM is not stable on a given machine, switch to CPU or a smaller model:

```text
WHISPER_MODEL=medium
WHISPER_MODEL_FALLBACKS=small
FASTER_WHISPER_DEVICE=cpu
FASTER_WHISPER_COMPUTE_TYPE=int8
FASTER_WHISPER_BATCH_SIZE=4
```

`FASTER_WHISPER_BATCH_SIZE` enables faster-whisper's batched inference. `8` is a good first try on many 8 GB NVIDIA GPUs; reduce it to `4` or `1` if you see out-of-memory errors.

For long videos on Windows, CPU `medium + int8` is slower but usually safer.

FunASR can also be required as the only Chinese-first transcription backend. The recommended `funasr-whisper` mode tries FunASR first and automatically falls back to faster-whisper if FunASR dependencies, CUDA, or model loading fail. Use strict FunASR only when you want failures to stop immediately:

```text
WHISPER_BACKEND=funasr
FUNASR_MODEL=paraformer-zh
FUNASR_VAD_MODEL=fsmn-vad
FUNASR_PUNC_MODEL=ct-punc
FUNASR_DEVICE=cuda:0
FUNASR_HOTWORDS=streamer_name game_name custom_term
FUNASR_BATCH_SIZE_S=300
FUNASR_MAX_SEGMENT_MS=60000
FUNASR_PERSISTENT_WORKER=true
```

The first FunASR run downloads model files. By default, the isolated FunASR
child process stays alive and reuses its loaded model, avoiding the repeated
20-30 second startup cost on later jobs. Set
`FUNASR_PERSISTENT_WORKER=false` only for diagnosis; process crashes still
restart automatically and communication failures fall back to the legacy
one-shot runner. Use `FUNASR_DEVICE=cpu` if CUDA PyTorch is not installed or
not stable. FunASR output is normalized into the same `transcript.txt`,
`transcript.srt`, and `transcript.json` files used by the rest of the pipeline.
When fallback is used, `transcript.json` records `fallback_from=funasr` and the
fallback reason.

## Cut Stabilization

The initial cut plan removes invalid spans from silence/freeze detection, then stabilizes kept clips so short pauses do not create excessive jump cuts.

Current defaults:

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

`CUT_MERGE_GAP_SECONDS` controls how aggressively adjacent kept clips separated by a short removed gap are merged. Increase it for smoother rough cuts; decrease it for tighter, more aggressive editing.

`SOURCE_INTEGRITY_SCAN_ENABLED` runs an ffmpeg decode scan and writes `corrupt.json`. It does not fail the job, but the Web UI warns when the source file has damaged H.264 frames so you can re-download/re-export the source or remove damaged clips before final rendering.

`VISUAL_DETECT_KEYFRAMES_ONLY` makes freeze and scene detection decode only keyframes, which is much faster on long recordings. Disable it if you need denser scene-change markers. `VISUAL_DETECT_FPS` and `VISUAL_DETECT_WIDTH` downsample only visual analysis and do not affect rendered videos; the FPS limiter is used only when keyframe-only mode is disabled.

## CLI Examples

Health check:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --health
```

Process one file:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\to\recording.mp4" --detect-silence --detect-scenes
```

Use a profile:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\to\recording.mp4" --profile douyin --progress
```

Watch the recordings folder:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --watch --profile analysis
```

Resume failed or incomplete jobs:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --resume
```

List known jobs:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --status
```

Clean old jobs with a dry run:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --cleanup-days 30 --dry-run
```

Run the batch example:

```powershell
cd D:\video-automation
.\venv\Scripts\python.exe .\run_worker.py --batch ".\examples\batch.example.json" --progress
```

## Common CLI Flags

```text
--once <file>        Process one media file
--batch <json>       Process a batch JSON file
--watch              Watch input\recordings
--profile <name>     analysis / douyin / bilibili / youtube_shorts
--force              Re-run and overwrite existing outputs
--detect-silence     Generate silence.json
--detect-freeze      Generate freeze.json
--detect-scenes      Generate scene.json
--render-review      Generate review.mp4
--render-final       Generate final.mp4
--vertical           Render final video as 1080x1920
--burn-subtitles     Embed ASS subtitles into final.mp4
--plan-crop          Generate crop_plan.json
--plan-uvr           Generate audio separation / UVR plan, or run Demucs when enabled
--skip-transcribe    Create empty transcript outputs
--serve              Start the local Web UI and API
--health             Check configured tools
--status             List jobs
--resume             Resume incomplete jobs
--json               JSON output for health/status
--progress           Emit JSONL progress events
```

## HTTP API

Start:

```powershell
D:\video-automation\venv\Scripts\python.exe D:\video-automation\run_worker.py --serve
```

Default base URL:

```text
http://127.0.0.1:8765
```

Endpoints:

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

`POST /process` accepts JSON and returns a job immediately while processing continues in the background. Query `GET /jobs/<job-folder-name>` for status or subscribe to `GET /events` for Server-Sent Events.

Mutation endpoints such as delete, rerun, cut editing, and transcript editing reject jobs that are still processing with `409 Conflict`. JSON request bodies are validated and malformed JSON returns `400`.

The Web UI and API reject browser requests from untrusted origins. The built-in local origins are allowed automatically. If you host a separate local frontend or connect a browser-based automation tool from another port, add exact origins to `API_ALLOWED_ORIGINS`, for example `http://localhost:3000,http://127.0.0.1:5678`.

## Configuration

Configuration is loaded from `.env`, with `.env.example` as fallback. Environment variables still have highest priority, including empty strings when intentionally set. The file loader caches `.env` and `.env.example` by file timestamp and size, so repeated `Settings.load()` calls no longer reread the files every time; editing either file is picked up automatically on the next read.

The Web Settings page can save common high-frequency options such as transcription model/language, cut thresholds, subtitle style, render preview settings, AI cover provider/model, LLM model, batch limits, and upload-size limits. Saving writes a whitelist of keys to `.env` and hot-reloads the running API for future jobs and enhancement actions. Advanced paths, deployment-only values, and full manual control remain available by editing `.env` directly.

Important defaults:

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

`AUDIOWAVEFORM_PATH` is optional. If it is missing, the worker generates simplified waveform data from `audio.wav` so the Web timeline can still show audio rhythm.

`UVR_PATH` is an optional legacy integration hook for an external Ultimate Vocal Remover executable. Leave it blank unless you actively use that tool. Typical Windows example:

```text
UVR_PATH=F:\Ultimate Vocal Remover\UVR.exe
```

For native audio separation, leave `AUDIO_SEPARATION_ENGINE=plan` unless Demucs is installed. To run it from the UVR stage:

```powershell
.\venv\Scripts\python.exe -m pip install demucs
```

Then set:

```text
AUDIO_SEPARATION_ENGINE=demucs
DEMUCS_PATH=demucs
DEMUCS_MODEL=htdemucs
DEMUCS_DEVICE=auto
```

When enabled, `--plan-uvr` writes `uvr/vocals.wav` and `uvr/instrumental.wav` for each job. Demucs is optional and is not bundled into Lite desktop builds.

## Boundaries

- Original recordings are not modified.
- The project creates rough-cut suggestions and local render outputs.
- Fine timeline editing is still limited compared with a full NLE.
- UVR/Demucs, webhook, automatic platform publishing, and publisher connectors are not executed by default.
- Dynamic face/subject tracking is not included; vertical crop anchors are deterministic.

Example payloads live in `examples/`. Design and frontend review notes live in `docs/reviews/`.

## License

This project is licensed under the MIT License. See `LICENSE` for details.

Third-party tools, models, fonts, and APIs used with this project may have their own licenses or terms. See `NOTICE` for a short attribution and responsibility note.

For the Chinese manual, see `README.zh-CN.md`.
