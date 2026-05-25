# Video Automation

Local recording-to-rough-cut workflow for video creators.

Video Automation watches or accepts local recording files, creates one job folder per source video, analyzes the media, transcribes speech, suggests cuts, and can render preview or final videos. It keeps the original recording untouched.

## Current Features

- Local Web dashboard at `http://127.0.0.1:8765/#/`
- Drag-and-drop video import into `input\recordings`
- Batch drag-and-drop import and batch job submission
- Upload progress while importing large media files in the Web UI
- Recording picker for files already in `input\recordings`
- Workflow profiles: analysis, Douyin, Bilibili, YouTube Shorts
- Job cards with status, progress, thumbnails, and quick navigation
- Job detail page with pipeline status, video preview, timeline, transcript, clip editor, and downloads
- Editable cut list with automatic preview re-render after saving
- Editable transcript text with timestamp-to-preview seeking
- AI video cover generation with portrait and landscape candidates
- Optional enhancement modules for platform segmentation, AI metadata, semantic highlights, download queue, and publish packages
- Timeline waveform data, using `audiowaveform` when available and a Python WAV fallback otherwise
- Clip stabilization that merges tiny fragments and short-gap jump cuts before review
- Approve/review flow for marking a job as complete
- Health and settings pages for local tool/config visibility
- HTTP Range support for video/audio preview seeking
- Local HTTP API for automation tools such as n8n or Coze
- Controlled parallel processing with `API_PARALLEL_JOBS`
- CLI worker for single-file, watch, batch, resume, cleanup, and status workflows

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
subtitles.ass             Styled ASS subtitle file
subtitles_clipped.ass     Subtitle file remapped to edited clips
corrupt.json              Source video decode integrity scan
silence.json              Silence detection output
freeze.json               Freeze/static-frame detection output
scene.json                Scene-change detection output
cuts.json                 Structured cut suggestions and edited clips
cuts.md                   Human-readable cut sheet
crop_plan.json            Vertical framing plan
uvr_plan.json             Vocal/BGM separation plan contract
render_preview.json       Preview render command plan
review.mp4                Preview video
final_render_preview.json Final render command plan
final.mp4                 Final rendered video
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

`uvr_plan.json` is only a planning contract. The worker does not run vocal separation automatically.

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
- Generate optional platform-sized segments, AI metadata, semantic highlights, and manual publish packages
- Start optional URL downloads when `DOWNLOAD_ENABLED=true`, then import completed downloads as jobs
- Download creator-facing outputs and advanced JSON outputs

UI labels use creator-facing terms. For example, `render_review` appears as preview video, and `burn_subtitles` appears as embedded subtitles.

## AI Video Covers

The Job Detail page includes an optional AI cover panel. Cover generation is separate from the main editing pipeline, so it does not change `needs_review`, `done`, or `failed` job state.

Configure an OpenAI API key before using it:

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

The worker builds a prompt from `manifest.json`, `cuts.json`, `transcript.json`, and the job title. The model generates background images without readable text, then local post-processing adds the chosen title with a deterministic font overlay. This avoids unreliable AI-generated Chinese text.

Outputs:

- Portrait candidates: `cover_9x16_01.jpg`, `cover_9x16_02.jpg`, ...
- Landscape candidates: `cover_16x9_01.jpg`, `cover_16x9_02.jpg`, ...
- Selected files: `cover_vertical.jpg` and `cover_landscape.jpg`

The default request creates 3 portrait and 3 landscape candidates. Choosing 5 candidates increases API usage. Common failures are missing `OPENAI_API_KEY`, insufficient quota, network errors, or content rejection by the image API. The Health page marks `Pillow` and `OPENAI_API_KEY` as optional cover-related checks.

## Optional Enhancement Modules

These modules are manual job add-ons. They do not run inside the default pipeline and do not change job review status.

- Platform segments: choose Douyin, Bilibili, or YouTube Shorts in Job Detail to create `segments_manifest.json` and `segments/<platform>_part_01.mp4` files from `final.mp4` or `review.mp4`.
- AI metadata: set `LLM_PROVIDER=openai`, `LLM_MODEL=<model>`, and `OPENAI_API_KEY`, then generate editable `metadata.json` with title, description, tag, hashtag, and cover-title ideas.
- Semantic highlights: uses the same LLM settings to write `highlights.json` and attach `semantic_score` to clips where possible.
- Downloads: set `DOWNLOAD_ENABLED=true` and `YTDLP_PATH=yt-dlp` to enable the New Job URL download queue. Downloaded files are stored in `input\downloads` and can be imported into the normal job flow.
- Publish package: creates `publish_package.json` for manual platform upload. It does not call Bilibili or Douyin APIs yet.
- Project export: creates `project_export_manifest.json` plus `project_exports/premiere/premiere_timeline.xml` for Premiere Pro import and `project_exports/jianying_package/` for Jianying/CapCut manual import. The Jianying package is not a proprietary draft project.

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
RENDER_NVENC_PRESET=p5
RENDER_NVENC_CQ=21
RENDER_NVENC_PREVIEW_PRESET=p4
RENDER_NVENC_PREVIEW_CQ=25
```

`review.mp4` uses the preview preset/CQ so it appears faster. `final.mp4` uses the final preset/CQ for better quality. If health check reports `h264_nvenc` as missing, either point `FFMPEG_PATH` to a build with NVENC support or switch back to `RENDER_VIDEO_ENCODER=libx264`.

## Transcription

Default transcription settings prioritize Chinese livestream accuracy with faster-whisper:

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

Useful optional settings:

```text
WHISPER_INITIAL_PROMPT=The recording is Chinese livestream content with streamer names, game terms, chat slang, and spoken language.
SUBTITLE_REPLACEMENTS=wrong term=>correct term,酒馆占棋=>酒馆战棋
TRANSCRIBE_AUDIO_FILTER=highpass=f=80,lowpass=f=7600,afftdn
```

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

FunASR can be used as an optional Chinese-first transcription backend. It is useful for Mandarin livestream recordings, punctuation, and hotword-biased recognition. Install optional dependencies first, then switch the backend in `.env`:

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

The first FunASR run downloads model files. Use `FUNASR_DEVICE=cpu` if CUDA PyTorch is not installed or not stable. FunASR output is normalized into the same `transcript.txt`, `transcript.srt`, and `transcript.json` files used by the rest of the pipeline.

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
--plan-uvr           Generate uvr_plan.json
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

`POST /process` accepts JSON and returns a job immediately while processing continues in the background. Poll `GET /jobs/<job-folder-name>` for status.

Mutation endpoints such as delete, rerun, cut editing, and transcript editing reject jobs that are still processing with `409 Conflict`. JSON request bodies are validated and malformed JSON returns `400`.

The Web UI and API reject browser requests from untrusted origins. The built-in local origins are allowed automatically. If you host a separate local frontend or connect a browser-based automation tool from another port, add exact origins to `API_ALLOWED_ORIGINS`, for example `http://localhost:3000,http://127.0.0.1:5678`.

## Configuration

Configuration is loaded from `.env`, with `.env.example` as fallback.

Important defaults:

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

`AUDIOWAVEFORM_PATH` is optional. If it is missing, the worker generates simplified waveform data from `audio.wav` so the Web timeline can still show audio rhythm.

## Boundaries

- Original recordings are not modified.
- The project creates rough-cut suggestions and local render outputs.
- Fine timeline editing is still limited compared with a full NLE.
- UVR, webhook, automatic platform publishing, and publisher connectors are not executed by default.
- Dynamic face/subject tracking is not included; vertical crop anchors are deterministic.

Example payloads live in `examples/`. Design and frontend review notes live in `docs/reviews/`.

For the Chinese manual, see `README.zh-CN.md`.
