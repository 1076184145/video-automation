# Project Structure

This document describes the current file layout. It is a maintenance map only; the Python package and Web static files intentionally remain in their existing paths.

## Root

```text
video-automation/
├── .github/workflows/      Cross-platform tests, gated versioning, release builds
├── pyproject.toml          Python requirement and Ruff configuration
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
├── requirements-local-ai.txt Optional configurable local Hugging Face runtime
├── requirements-optional.txt Optional Python dependencies
└── .env.example            Configuration template
```

Do not move `input/`, `processing/`, `logs/`, `models/`, `venv/`, or `.env` as part of source cleanup. They are local runtime state.

## Python Package

`video_automation/` is kept flat to avoid import churn. Modules are grouped by responsibility:

| Area | Modules | Responsibility |
|---|---|---|
| Configuration and state | `config.py`, `credentials.py`, `jobs.py`, `io_utils.py` | Settings, OS credential references, job lifecycle, atomic file helpers |
| Media processing | `media.py`, `crop.py`, `render.py`, `progress.py`, `covers.py`, `segments.py` | ffprobe/ffmpeg operations, vertical framing, rendering, progress parsing, AI cover candidates, platform video segments |
| Transcription and subtitles | `transcribe.py`, `transcribe_runtime.py`, `transcribe_runner.py`, `transcribe_worker.py`, `subtitles.py`, `profanity.py` | Backend selection, supervised subprocess runtime, persistent worker protocol, transcript files, ASS subtitles, text cleanup |
| Cut planning and refinement | `cuts.py`, `profiles.py`, `clip_state.py`, `clip_evaluator.py`, `agent_policy.py`, `clip_operations.py`, `clip_refinement.py` | Invalid segment logic, clip scoring, typed refinement state, pure evaluation/policy, isolated side effects, and bounded orchestration |
| Unattended highlights | `llm_evaluator.py`, `highlight_graph.py`, `highlight_checker.py`, `highlight_edits.py`, `unattended_highlights.py` | Opt-in sentence-ID multi-angle selection, bounded/durable analysis and full-text review, final NMS, directional RMS/EDL planning, shared subtitle mapping, independent rendering; offline comparison via `tools/compare_highlight_runs.py` |
| Optional integrations | `plans.py`, `hooks.py`, `cleanup.py`, `llm_tools.py`, `local_ai.py`, `publish.py` | BGM/platform/webhook/UVR plan contracts, external or local LLM metadata/highlights, local Hugging Face covers, publish package, old job cleanup |
| HTTP routing | `api.py`, `api_context.py`, `routing.py`, `api_routes_base.py`, `api_routes_system.py`, `api_routes_jobs.py`, `api_routes_enhancements.py` | Thin server composition root, thread-safe runtime context, declarative route registry, and domain route handlers |
| API support and diagnostics | `api_http_utils.py`, `api_job_utils.py`, `api_system.py`, `api_security.py`, `url_security.py`, `api_settings.py`, `health.py` | HTTP helpers, job response/edit services, tool-install state, bind and Host safety, provider URL validation, secure settings updates, and health reporting |
| Pipeline execution | `pipeline_context.py`, `pipeline_spec.py`, `pipeline_scheduler.py`, `pipeline_executor.py`, `stage_runs.py` | Typed run context, stage contracts and dependencies, generic dependency scheduler, job-specific stage construction, and durable run state |
| Entrypoints | `worker.py` | CLI argument handling, batch execution, resume, and file watching |

## Web Dashboard

`web/` is served directly by `run_worker.py --serve`; there is no bundler or frontend build step.

```text
web/
├── index.html
├── css/
│   ├── fonts.css
│   └── style.css
└── js/
    ├── ai-disclosure.js
    ├── api.js
    ├── app.js
    ├── automation.js
    ├── clip-editor.js
    ├── clip-time.js
    ├── confirm-dialog.js
    ├── cover-panel.js
    ├── damaged-range.js
    ├── dashboard.js
    ├── detail-layout.js
    ├── detail-tabs.js
    ├── download-section.js
    ├── enhancement-panel.js
    ├── error-hints.js
    ├── event-hub.js
    ├── health.js
    ├── i18n-en.js
    ├── i18n-zh.js
    ├── i18n.js
    ├── icons.js
    ├── job-actions.js
    ├── job-card.js
    ├── job-detail-data.js
    ├── job-detail-view.js
    ├── job-detail.js
    ├── job-status.js
    ├── kept-transcript.js
    ├── motion.js
    ├── new-job-view.js
    ├── new-job.js
    ├── notifications.js
    ├── preview-player.js
    ├── projects.js
    ├── provider-errors.js
    ├── publish-center.js
    ├── review-drafts.js
    ├── revision-history.js
    ├── router.js
    ├── settings-schema.js
    ├── settings.js
    ├── shortcut-help.js
    ├── theme.js
    ├── timeline.js
    ├── toast.js
    ├── transcript-editor.js
    ├── ui-states.js
    └── utils.js
```

| Area | Files | Responsibility |
|---|---|---|
| App shell and navigation | `app.js`, `router.js`, `shortcut-help.js`, `notifications.js`, `theme.js`, `motion.js` | Routes, navigation, shortcuts, notifications, themes, and motion |
| Shared support and UI | `api.js`, `i18n.js`, `i18n-zh.js`, `i18n-en.js`, `utils.js`, `event-hub.js`, `error-hints.js`, `provider-errors.js`, `icons.js`, `ui-states.js`, `toast.js`, `confirm-dialog.js`, `ai-disclosure.js` | API/events, localization, formatting, error messages, shared controls, and AI disclosures |
| Workflow pages | `dashboard.js`, `job-card.js`, `new-job.js`, `new-job-view.js`, `automation.js`, `projects.js`, `publish-center.js` | Job lists and creation, automation, project management, and publish packages |
| Review and editing | `job-detail.js`, `job-detail-data.js`, `job-detail-view.js`, `detail-layout.js`, `detail-tabs.js`, `job-actions.js`, `job-status.js`, `transcript-editor.js`, `kept-transcript.js`, `review-drafts.js`, `revision-history.js`, `enhancement-panel.js`, `download-section.js`, `cover-panel.js`, `preview-player.js` | Job detail data and markup, actions, transcript review, revisions, enhancements, downloads, covers, and playback |
| Settings and diagnostics | `settings.js`, `settings-schema.js`, `health.js` | Settings forms and schema, credentials, and dependency health |
| Timeline and clips | `timeline.js`, `clip-editor.js`, `clip-time.js`, `damaged-range.js` | Canvas timeline, waveform, clip controls, time conversion, and invalid ranges |
| Styling | `css/fonts.css`, `css/style.css` | Local font declarations, design tokens, layout, responsive behavior, and dark theme |

## CI Workflows

- `.github/workflows/test.yml`: Ubuntu and Windows checks with Python 3.11 and Node 20; runs Ruff F/B, the Python test suite, and frontend tests.
- `.github/workflows/auto-version.yml`: runs after successful main-branch Tests, verifies the tested commit is still current, then updates the version and dispatches the release build.
- `.github/workflows/release.yml`: builds and publishes the Windows desktop release.

## Examples

- `examples/batch.example.json`: sample batch-processing payload.

## Runtime Directories

- `input/recordings`: videos selected by the Web UI, dragged into the browser, or watched by `--watch`.
- `processing/jobs`: generated job folders and all media outputs.
- `logs`: worker-level logs.
- `venv`: local dependency environment.

These directories are intentionally outside the source organization scheme because their contents are machine- and job-specific.
