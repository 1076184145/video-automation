# Project Structure

This document describes the current file layout. It is a maintenance map only; the Python package and Web static files intentionally remain in their existing paths.

## Root

```text
D:\video-automation
├── video_automation/       Python workflow package
├── web/                    Local Web dashboard served by --serve
├── docs/                   Project documentation
├── examples/               Example input files for CLI/API workflows
├── input/                  Runtime input folders
├── processing/             Runtime job outputs
├── logs/                   Runtime logs
├── subtitles/              Reserved subtitle/template workspace
├── config/                 Reserved local config workspace
├── venv/                   Local Python virtual environment
├── run_worker.py           CLI/API entrypoint
├── README.md               English manual
├── README.zh-CN.md         Chinese manual
├── requirements.txt        Required Python dependencies
├── requirements-core.txt   Minimal Web/API runtime dependencies
├── requirements-transcription-faster.txt Lean Faster-Whisper runtime
├── requirements-transcription-funasr.txt FunASR plus Faster-Whisper fallback
├── requirements-optional.txt Optional Python dependencies
└── .env.example            Configuration template
```

Do not move `input/`, `processing/`, `logs/`, `venv/`, or `.env` as part of source cleanup. They are local runtime state.

## Python Package

`video_automation/` is kept flat to avoid import churn. Modules are grouped by responsibility:

| Area | Modules | Responsibility |
|---|---|---|
| Configuration and state | `config.py`, `credentials.py`, `jobs.py`, `io_utils.py` | Settings, OS credential references, job lifecycle, atomic file helpers |
| Media processing | `media.py`, `crop.py`, `render.py`, `progress.py`, `covers.py`, `segments.py` | ffprobe/ffmpeg operations, vertical framing, rendering, progress parsing, AI cover candidates, platform video segments |
| Transcription and subtitles | `transcribe.py`, `transcribe_runtime.py`, `transcribe_runner.py`, `transcribe_worker.py`, `subtitles.py`, `profanity.py` | Backend selection, supervised subprocess runtime, persistent worker protocol, transcript files, ASS subtitles, text cleanup |
| Cut planning | `cuts.py`, `profiles.py` | Invalid segment logic, clip scoring, workflow profiles |
| Optional integrations | `plans.py`, `hooks.py`, `cleanup.py`, `llm_tools.py`, `publish.py` | BGM/platform/webhook/UVR plan contracts, LLM metadata/highlights, publish package, old job cleanup |
| HTTP and diagnostics | `api.py`, `api_security.py`, `api_settings.py`, `routing.py`, `health.py` | Local HTTP server, bind safety, secure settings updates, route registry, health/security/storage reporting |
| Entrypoints | `worker.py` | CLI worker and pipeline orchestration |

## Web Dashboard

`web/` is served directly by `run_worker.py --serve`.

```text
web/
├── index.html
├── css/
│   └── style.css
└── js/
    ├── app.js
    ├── router.js
    ├── api.js
    ├── i18n.js
    ├── i18n-zh.js
    ├── i18n-en.js
    ├── utils.js
    ├── dashboard.js
    ├── new-job.js
    ├── job-detail.js
    ├── settings.js
    ├── health.js
    └── timeline.js
```

| Area | Files | Responsibility |
|---|---|---|
| App shell and routing | `app.js`, `router.js` | Navigation, route registration, language switch rendering |
| Shared support | `api.js`, `i18n.js`, `i18n-zh.js`, `i18n-en.js`, `utils.js` | Fetch wrapper, language runtime, split translation dictionaries, formatting/status helpers |
| Pages | `dashboard.js`, `new-job.js`, `job-detail.js`, `settings.js`, `health.js` | Dashboard, task creation, review/editing, settings, health checks |
| Visualization | `timeline.js` | Canvas timeline, marks, waveform rendering, tooltip behavior |
| Styling | `css/style.css` | Layout, controls, responsive behavior, dark theme |

## Examples

- `examples/batch.example.json`: sample batch-processing payload.

## Runtime Directories

- `input/recordings`: videos selected by the Web UI, dragged into the browser, or watched by `--watch`.
- `processing/jobs`: generated job folders and all media outputs.
- `logs`: worker-level logs.
- `venv`: local dependency environment.

These directories are intentionally outside the source organization scheme because their contents are machine- and job-specific.
