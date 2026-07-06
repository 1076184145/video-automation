# Video Automation

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-orange.svg)](https://ffmpeg.org/)

**中文说明：[README.zh-CN.md](README.zh-CN.md)**

Turn a long local recording into a reviewed short video with transcript, subtitles, cover images, and export files. Everything runs on your computer unless you choose an external AI service.

![Video Automation dashboard](docs/assets/dashboard.png)

> Video Automation accepts local video files. URL downloading and livestream recording are not included. Original files are never modified, and platform login or automatic publishing is disabled by default.

## Start in 5 Minutes

### Windows app (recommended)

1. Download the latest Windows package from [GitHub Releases](https://github.com/1076184145/video-automation/releases).
2. Install or unzip it, then run `VideoAutomationLite.exe`.
3. Open **Health**. If FFmpeg or FFprobe is missing, click **Auto-fix Dependencies**.
4. Open **New Job** and add one local video.
5. Choose a profile such as **Fast**, **Douyin**, or **Bilibili**, then start processing.
6. Open the finished job, review it, and download `final.mp4`.

The basic workflow does not require an API key.

### Run from source

Requirements:

- Python 3.11 or newer
- FFmpeg and FFprobe available on `PATH`
- Git
- Optional: an NVIDIA GPU for faster transcription and rendering

Windows PowerShell:

```powershell
git clone https://github.com/1076184145/video-automation.git
cd video-automation
py -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r requirements-optional.txt
.\venv\Scripts\python.exe .\run_worker.py --serve
```

macOS or Linux:

```bash
git clone https://github.com/1076184145/video-automation.git
cd video-automation
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python -m pip install -r requirements-optional.txt
./venv/bin/python run_worker.py --serve
```

Open [http://127.0.0.1:8765/#/](http://127.0.0.1:8765/#/) in your browser. Keep the terminal window open while using the app.

## Daily Workflow

1. **Import:** drag in a local video or select one from `input/recordings`.
2. **Choose:** select a profile and enable only the options you need.
3. **Process:** the app inspects the video, transcribes speech, suggests cuts, and renders selected outputs.
4. **Review:** preview clips, edit cuts or transcript text, and rerun when needed.
5. **Export:** download the final video, subtitles, cover, or manual publish package.

Profiles are starting points:

| Profile | Use it for |
|---|---|
| **Fast** | A quick final video with less optional analysis |
| **Analysis** | Transcript and detection results without a full export |
| **Douyin** | Vertical short-video output |
| **Bilibili** | Standard Bilibili-oriented output |
| **YouTube Shorts** | Vertical Shorts output |

## Features

Included in the local workflow:

- Single and batch video import
- Job progress, restart recovery, and a persistent task queue
- Speech transcription with Whisper-compatible local backends
- Silence, freeze, scene, and damaged-frame checks
- Suggested cuts, transcript editing, and subtitle generation
- Browser preview plus full-quality `final.mp4`
- Vertical `1080x1920` output and subtitle burn-in
- Projects, reusable recipes, creator settings, and review revisions
- Premiere Pro and Jianying/CapCut handoff files
- Manual upload packages for supported platforms

Optional features:

- AI covers, translation, titles, descriptions, and highlight suggestions
- NVIDIA CUDA/NVENC acceleration
- FunASR and faster-whisper local speech backends
- Demucs audio separation
- A separately configured publishing connector; manual packages remain the fallback

AI features require a key from the provider you select. Add it in **Settings** or a private `.env` file. See [`.env.example`](.env.example) for available settings.

## Important Outputs

Each job is stored under `processing/jobs/<job-name>/`.

| File | What it is |
|---|---|
| `final.mp4` | Full-quality finished video |
| `web_preview.mp4` | Smaller browser preview |
| `transcript.txt` / `.srt` | Transcript and subtitles |
| `cuts.json` | Suggested or edited clip ranges |
| `cover_*.jpg` | Generated or selected covers |
| `publish_packages/` | Files and text for manual upload |
| `project_exports/` | Premiere Pro or Jianying/CapCut handoff files |

## Troubleshooting

**The app says FFmpeg or FFprobe is missing**

Open **Health** and use **Auto-fix Dependencies**. Source users can run:

```powershell
.\venv\Scripts\python.exe .\run_worker.py --health
```

**The first job is slow**

Speech models may download and initialize on first use. Later jobs reuse local model files and can start faster.

**CUDA or GPU processing fails**

Choose a smaller speech model or switch transcription/rendering to CPU in **Settings**.

**An AI button reports a missing key**

The local editing workflow still works. Configure a provider key only if you want that AI feature.

**Where are my jobs?**

Open `processing/jobs/`. Do not commit this folder, `.env`, logs, private videos, or generated exports to Git.

## Developer Commands

```powershell
# Show every CLI option
.\venv\Scripts\python.exe .\run_worker.py --help

# Machine-readable health check
.\venv\Scripts\python.exe .\run_worker.py --health --json

# Process one local file
.\venv\Scripts\python.exe .\run_worker.py --once "D:\path\video.mp4" --profile douyin --progress

# Run Python tests
.\venv\Scripts\python.exe -m unittest discover -s tests
```

The local Web server binds to `127.0.0.1:8765` by default. Do not expose it publicly without authentication, network controls, and HTTPS. Contribution guidance is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacy and Boundaries

- Videos, jobs, and provider keys stay on your computer by default.
- External AI features send only the required request and credential directly to the provider you choose.
- The project does not operate an intermediary server or a remote self-update service.
- Manual publish packages do not log in or upload automatically.
- You are responsible for the rights to process and publish your media.

Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

Video Automation is available under the [MIT License](LICENSE). Third-party tools, models, fonts, and APIs may have separate terms; see [NOTICE](NOTICE).
