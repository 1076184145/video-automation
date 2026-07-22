from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .cleanup import cleanup_jobs
from .config import Settings
from .crop import generate_vertical_crop_plan
from .cuts import generate_cuts
from .health import health_check, health_payload  # Re-export health_payload for compatibility.
from .hooks import generate_uvr_plan
from .io_utils import write_json_atomic, write_text_atomic
from .jobs import Job, close_job_logger, configure_job_logger, create_job, find_resume_jobs, list_jobs
from .library_api import library_database_path
from .media import MEDIA_EXTENSIONS, detect_silence, detect_visual_events, extract_audio_outputs, generate_thumbnail, generate_waveform, probe_media
from .plans import generate_bgm_mix_plan, generate_platform_export_plan, generate_webhook_plan
from .pipeline_spec import PIPELINE_STAGE_DEPENDENCIES, PIPELINE_STAGE_SELECTION_DEPENDENCIES, PIPELINE_STAGE_SPECS
from .profiles import apply_profile_settings, profile_flags
from .render import generate_render_preview, render_final_video, render_review_video, render_web_preview
from .resources import job_gpu_status_callbacks, rendering_uses_gpu, transcription_uses_gpu
from .stage_runs import StageRunRepository
from .task_queue import QueueControlRequested
from .subtitles import generate_ass_subtitles, generate_clipped_ass_subtitles
from .transcribe import transcribe_audio


def _transcription_backend_label(backend: str) -> str:
    normalized = str(backend or "").strip().lower()
    if normalized in {"funasr", "funasr-whisper", "funasr-faster-whisper"}:
        return "FunASR"
    if normalized == "faster-whisper":
        return "Faster-Whisper"
    if normalized == "cli":
        return "Whisper CLI"
    return normalized or "Transcription backend"


def _raise_for_severe_source_corruption(settings: Settings, payload: dict[str, Any] | None) -> None:
    if not isinstance(payload, dict) or payload.get("status") != "corrupt":
        return
    error_count = max(0, int(payload.get("error_count") or 0))
    limit = max(1, int(settings.source_integrity_scan_max_errors))
    if error_count < limit:
        return
    raise RuntimeError(
        f"source integrity scan found {error_count} decode errors, exceeding the limit of {limit}; "
        "normalize or replace the source before transcription and rendering"
    )


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Video automation worker")
    parser.add_argument("--once", type=Path, help="Process one media file and exit")
    parser.add_argument("--batch", type=Path, help="Process media files from a JSON batch file and exit")
    parser.add_argument("--watch", action="store_true", help="Watch input recordings directory")
    parser.add_argument("--profile", choices=["fast", "analysis", "douyin", "bilibili", "youtube_shorts"], help="Apply a creator workflow preset")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs")
    parser.add_argument("--detect-silence", action="store_true", help="Generate silence.json and silence-based cuts")
    parser.add_argument("--detect-freeze", action="store_true", help="Generate freeze.json with ffmpeg freezedetect")
    parser.add_argument("--detect-scenes", action="store_true", help="Generate scene.json with ffmpeg scene-change detection")
    parser.add_argument("--render-review", action="store_true", help="Render review.mp4 from cuts.json after planning")
    parser.add_argument("--render-final", action="store_true", help="Render final.mp4 from review.mp4 after planning")
    parser.add_argument("--vertical", action="store_true", help="Render final.mp4 as 1080x1920 vertical video")
    parser.add_argument("--burn-subtitles", action="store_true", help="Burn subtitles.ass into final.mp4")
    parser.add_argument("--plan-crop", action="store_true", help="Generate crop_plan.json for vertical rendering")
    parser.add_argument("--plan-uvr", action="store_true", help="Generate uvr_plan.json for future vocal separation")
    parser.add_argument("--skip-transcribe", action="store_true", help="Skip Whisper and create empty transcript files")
    parser.add_argument("--serve", action="store_true", help="Start local HTTP API server")
    parser.add_argument("--cleanup-days", type=_positive_int, help="Remove jobs older than N days")
    parser.add_argument(
        "--cleanup-mode",
        choices=["all", "intermediates"],
        default="all",
        help="Delete whole terminal jobs or only caches from completed jobs",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview cleanup without deleting jobs")
    parser.add_argument("--health", action="store_true", help="Check configured tools and directories")
    parser.add_argument("--status", action="store_true", help="Print known jobs and exit")
    parser.add_argument("--resume", action="store_true", help="Resume failed or incomplete jobs and exit")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output for --health or --status")
    parser.add_argument("--progress", action="store_true", help="Print JSONL progress events while processing")
    args = parser.parse_args(argv)

    settings = Settings.load()
    _apply_profile_to_args(args)
    settings = apply_profile_settings(settings, args.profile)
    bootstrap_dirs(settings)
    configure_root_logger(settings)

    if args.serve:
        from .api import serve

        serve(settings)
        return 0

    if args.cleanup_days is not None:
        payload = cleanup_jobs(
            settings,
            days=args.cleanup_days,
            dry_run=args.dry_run,
            mode=args.cleanup_mode,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.health:
        return health_check(settings, as_json=args.json)

    if args.status:
        print_status(settings, as_json=args.json)
        return 0

    if args.resume:
        resume_jobs(
            settings,
            force=args.force,
            detect_silence_enabled=args.detect_silence,
            detect_freeze_enabled=args.detect_freeze,
            detect_scenes_enabled=args.detect_scenes,
            render_review_enabled=args.render_review,
            render_final_enabled=args.render_final,
            vertical_enabled=args.vertical,
            burn_subtitles_enabled=args.burn_subtitles,
            plan_crop_enabled=args.plan_crop,
            plan_uvr_enabled=args.plan_uvr,
            skip_transcribe=args.skip_transcribe,
            progress_enabled=args.progress,
        )
        return 0

    if args.batch:
        return process_batch(
            settings,
            args.batch,
            force=args.force,
            detect_silence_enabled=args.detect_silence,
            detect_freeze_enabled=args.detect_freeze,
            detect_scenes_enabled=args.detect_scenes,
            render_review_enabled=args.render_review,
            render_final_enabled=args.render_final,
            vertical_enabled=args.vertical,
            burn_subtitles_enabled=args.burn_subtitles,
            plan_crop_enabled=args.plan_crop,
            plan_uvr_enabled=args.plan_uvr,
            skip_transcribe=args.skip_transcribe,
            progress_enabled=args.progress,
        )

    if args.once:
        process_file(
            settings,
            args.once,
            force=args.force,
            detect_silence_enabled=args.detect_silence,
            detect_freeze_enabled=args.detect_freeze,
            detect_scenes_enabled=args.detect_scenes,
            render_review_enabled=args.render_review,
            render_final_enabled=args.render_final,
            vertical_enabled=args.vertical,
            burn_subtitles_enabled=args.burn_subtitles,
            plan_crop_enabled=args.plan_crop,
            plan_uvr_enabled=args.plan_uvr,
            skip_transcribe=args.skip_transcribe,
            progress_enabled=args.progress,
        )
        return 0

    watch(
        settings,
        force=args.force,
        detect_silence_enabled=args.detect_silence,
        detect_freeze_enabled=args.detect_freeze,
        detect_scenes_enabled=args.detect_scenes,
        render_review_enabled=args.render_review,
        render_final_enabled=args.render_final,
        vertical_enabled=args.vertical,
        burn_subtitles_enabled=args.burn_subtitles,
        plan_crop_enabled=args.plan_crop,
        plan_uvr_enabled=args.plan_uvr,
        skip_transcribe=args.skip_transcribe,
        progress_enabled=args.progress,
    )
    return 0


def _apply_profile_to_args(args: argparse.Namespace) -> None:
    flags = profile_flags(getattr(args, "profile", None))
    mapping = {
        "detect_silence": "detect_silence",
        "detect_freeze": "detect_freeze",
        "detect_scenes": "detect_scenes",
        "render_review": "render_review",
        "render_final": "render_final",
        "vertical": "vertical",
        "burn_subtitles": "burn_subtitles",
        "plan_crop": "plan_crop",
        "plan_uvr": "plan_uvr",
    }
    for option, attr in mapping.items():
        if flags.get(option):
            setattr(args, attr, True)


def bootstrap_dirs(settings: Settings) -> None:
    for path in [settings.input_recordings_dir, settings.jobs_dir, settings.logs_dir]:
        path.mkdir(parents=True, exist_ok=True)


def configure_root_logger(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(settings.logs_dir / "worker.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"),
        ],
    )


class ProgressReporter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def emit(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        data = {
            "event": event,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        print(json.dumps(data, ensure_ascii=False), flush=True)


@dataclass(frozen=True)
class PipelineStage:
    name: str
    status: str
    enabled: bool
    run: Callable[[dict[str, Any]], None]
    dependencies: frozenset[str] = frozenset()
    exclusive_resources: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BatchItem:
    source_path: Path
    force: bool
    detect_silence_enabled: bool
    detect_freeze_enabled: bool
    detect_scenes_enabled: bool
    render_review_enabled: bool
    render_final_enabled: bool
    vertical_enabled: bool
    burn_subtitles_enabled: bool
    plan_crop_enabled: bool
    plan_uvr_enabled: bool
    skip_transcribe: bool


def print_status(settings: Settings, *, as_json: bool = False) -> None:
    jobs = list_jobs(settings)
    if as_json:
        print(json.dumps([job.to_dict() for job in jobs], ensure_ascii=False, indent=2))
        return
    if not jobs:
        print("No jobs found.")
        return
    for job in jobs:
        print(f"{job.status:18} {job.updated_at}  {job.job_dir.name}")
        if job.error:
            print(f"  error: {job.error}")
        print(f"  source: {job.source_path}")


def resume_jobs(
    settings: Settings,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
) -> None:
    jobs = find_resume_jobs(settings)
    if not jobs:
        logging.info("No failed or incomplete jobs to resume")
        return
    for job in jobs:
        logging.info("Resuming %s", job.job_dir)
        process_job(
            settings,
            job,
            force=force,
            detect_silence_enabled=detect_silence_enabled,
            detect_freeze_enabled=detect_freeze_enabled,
            detect_scenes_enabled=detect_scenes_enabled,
            render_review_enabled=render_review_enabled,
            render_final_enabled=render_final_enabled,
            vertical_enabled=vertical_enabled,
            burn_subtitles_enabled=burn_subtitles_enabled,
            plan_crop_enabled=plan_crop_enabled,
            plan_uvr_enabled=plan_uvr_enabled,
            skip_transcribe=skip_transcribe,
            progress_enabled=progress_enabled,
        )


def process_batch(
    settings: Settings,
    batch_path: Path,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
) -> int:
    progress = ProgressReporter(progress_enabled)
    items = load_batch_items(
        batch_path,
        force=force,
        detect_silence_enabled=detect_silence_enabled,
        detect_freeze_enabled=detect_freeze_enabled,
        detect_scenes_enabled=detect_scenes_enabled,
        render_review_enabled=render_review_enabled,
        render_final_enabled=render_final_enabled,
        vertical_enabled=vertical_enabled,
        burn_subtitles_enabled=burn_subtitles_enabled,
        plan_crop_enabled=plan_crop_enabled,
        plan_uvr_enabled=plan_uvr_enabled,
        skip_transcribe=skip_transcribe,
    )
    failures = 0
    progress.emit("batch:start", batch_path=str(batch_path), total_items=len(items))
    for index, item in enumerate(items, start=1):
        payload = {
            "batch_path": str(batch_path),
            "item_number": index,
            "total_items": len(items),
            "source_path": str(item.source_path),
        }
        progress.emit("batch:item_start", **payload)
        try:
            job = process_file(
                settings,
                item.source_path,
                force=item.force,
                detect_silence_enabled=item.detect_silence_enabled,
                detect_freeze_enabled=item.detect_freeze_enabled,
                detect_scenes_enabled=item.detect_scenes_enabled,
                render_review_enabled=item.render_review_enabled,
                render_final_enabled=item.render_final_enabled,
                vertical_enabled=item.vertical_enabled,
                burn_subtitles_enabled=item.burn_subtitles_enabled,
                plan_crop_enabled=item.plan_crop_enabled,
                plan_uvr_enabled=item.plan_uvr_enabled,
                skip_transcribe=item.skip_transcribe,
                progress_enabled=progress_enabled,
            )
        except Exception as exc:
            failures += 1
            logging.exception("Batch item failed before job creation: %s", item.source_path)
            progress.emit("batch:item_error", **payload, error=str(exc))
            continue
        if job.status == "failed":
            failures += 1
            progress.emit("batch:item_error", **payload, job_dir=str(job.job_dir), error=job.error or "job failed")
            continue
        progress.emit("batch:item_complete", **payload, job_dir=str(job.job_dir), status=job.status)
    progress.emit("batch:complete", batch_path=str(batch_path), total_items=len(items), failures=failures)
    return 1 if failures else 0


def load_batch_items(
    batch_path: Path,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
) -> list[BatchItem]:
    try:
        payload = json.loads(batch_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"failed to read batch file: {exc}") from exc

    default_force = force or bool(_get_option(payload, "force", False))
    default_detect_silence = detect_silence_enabled or bool(_get_option(payload, "detect_silence", False))
    default_detect_freeze = detect_freeze_enabled or bool(_get_option(payload, "detect_freeze", False))
    default_detect_scenes = detect_scenes_enabled or bool(_get_option(payload, "detect_scenes", False))
    default_render_review = render_review_enabled or bool(_get_option(payload, "render_review", False))
    default_render_final = render_final_enabled or bool(_get_option(payload, "render_final", False))
    default_vertical = vertical_enabled or bool(_get_option(payload, "vertical", False))
    default_burn_subtitles = burn_subtitles_enabled or bool(_get_option(payload, "burn_subtitles", False))
    default_plan_crop = plan_crop_enabled or bool(_get_option(payload, "plan_crop", False))
    default_plan_uvr = plan_uvr_enabled or bool(_get_option(payload, "plan_uvr", False))
    default_skip_transcribe = skip_transcribe or bool(_get_option(payload, "skip_transcribe", False))
    raw_items = payload.get("files") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise RuntimeError("batch file must be a JSON array or an object with a files array")

    items: list[BatchItem] = []
    base_dir = batch_path.parent
    for raw_item in raw_items:
        item_options: dict[str, Any] = {}
        if isinstance(raw_item, str):
            source_text = raw_item
        elif isinstance(raw_item, dict):
            source_text = str(raw_item.get("path") or raw_item.get("source_path") or "")
            item_options = raw_item
        else:
            raise RuntimeError("batch items must be strings or objects")
        if not source_text:
            raise RuntimeError("batch item is missing path")
        source_path = Path(source_text)
        if not source_path.is_absolute():
            source_path = base_dir / source_path
        items.append(BatchItem(
            source_path=source_path,
            force=bool(item_options.get("force", default_force)),
            detect_silence_enabled=bool(item_options.get("detect_silence", default_detect_silence)),
            detect_freeze_enabled=bool(item_options.get("detect_freeze", default_detect_freeze)),
            detect_scenes_enabled=bool(item_options.get("detect_scenes", default_detect_scenes)),
            render_review_enabled=bool(item_options.get("render_review", default_render_review)),
            render_final_enabled=bool(item_options.get("render_final", default_render_final)),
            vertical_enabled=bool(item_options.get("vertical", default_vertical)),
            burn_subtitles_enabled=bool(item_options.get("burn_subtitles", default_burn_subtitles)),
            plan_crop_enabled=bool(item_options.get("plan_crop", default_plan_crop)),
            plan_uvr_enabled=bool(item_options.get("plan_uvr", default_plan_uvr)),
            skip_transcribe=bool(item_options.get("skip_transcribe", default_skip_transcribe)),
        ))
    return items


def _get_option(payload: Any, name: str, default: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return default


def watch(
    settings: Settings,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
) -> None:
    try:
        watch_with_watchdog(
            settings,
            force=force,
            detect_silence_enabled=detect_silence_enabled,
            detect_freeze_enabled=detect_freeze_enabled,
            detect_scenes_enabled=detect_scenes_enabled,
            render_review_enabled=render_review_enabled,
            render_final_enabled=render_final_enabled,
            vertical_enabled=vertical_enabled,
            burn_subtitles_enabled=burn_subtitles_enabled,
            plan_crop_enabled=plan_crop_enabled,
            plan_uvr_enabled=plan_uvr_enabled,
            skip_transcribe=skip_transcribe,
            progress_enabled=progress_enabled,
        )
    except ImportError:
        logging.info("watchdog is unavailable; falling back to polling")
        watch_with_polling(
            settings,
            force=force,
            detect_silence_enabled=detect_silence_enabled,
            detect_freeze_enabled=detect_freeze_enabled,
            detect_scenes_enabled=detect_scenes_enabled,
            render_review_enabled=render_review_enabled,
            render_final_enabled=render_final_enabled,
            vertical_enabled=vertical_enabled,
            burn_subtitles_enabled=burn_subtitles_enabled,
            plan_crop_enabled=plan_crop_enabled,
            plan_uvr_enabled=plan_uvr_enabled,
            skip_transcribe=skip_transcribe,
            progress_enabled=progress_enabled,
        )


def watch_with_watchdog(
    settings: Settings,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
) -> None:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    logging.info("Watching %s", settings.input_recordings_dir)
    seen: set[Path] = set()
    pending: queue.Queue[Path] = queue.Queue()

    class Handler(FileSystemEventHandler):
        def on_created(self, event):  # type: ignore[no-untyped-def]
            if not event.is_directory:
                pending.put(Path(event.src_path))

        def on_modified(self, event):  # type: ignore[no-untyped-def]
            if not event.is_directory:
                pending.put(Path(event.src_path))

        def on_moved(self, event):  # type: ignore[no-untyped-def]
            if not event.is_directory:
                pending.put(Path(event.dest_path))

    for path in iter_media_files(settings.input_recordings_dir):
        pending.put(path)

    observer = Observer()
    observer.schedule(Handler(), str(settings.input_recordings_dir), recursive=True)
    observer.start()
    try:
        while True:
            path = pending.get()
            if path.suffix.lower() not in MEDIA_EXTENSIONS or not path.exists():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            if not is_file_stable(resolved, settings.file_stable_seconds):
                pending.put(resolved)
                time.sleep(settings.poll_interval_seconds)
                continue
            try:
                process_file(
                    settings,
                    resolved,
                    force=force,
                    detect_silence_enabled=detect_silence_enabled,
                    detect_freeze_enabled=detect_freeze_enabled,
                    detect_scenes_enabled=detect_scenes_enabled,
                    render_review_enabled=render_review_enabled,
                    render_final_enabled=render_final_enabled,
                    vertical_enabled=vertical_enabled,
                    burn_subtitles_enabled=burn_subtitles_enabled,
                    plan_crop_enabled=plan_crop_enabled,
                    plan_uvr_enabled=plan_uvr_enabled,
                    skip_transcribe=skip_transcribe,
                    progress_enabled=progress_enabled,
                )
                seen.add(resolved)
            except Exception:
                logging.exception("Failed to process %s", resolved)
    finally:
        observer.stop()
        observer.join()


def watch_with_polling(
    settings: Settings,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
) -> None:
    logging.info("Polling %s", settings.input_recordings_dir)
    seen: set[Path] = set()
    while True:
        for path in iter_media_files(settings.input_recordings_dir):
            resolved = path.resolve()
            if resolved in seen:
                continue
            if not is_file_stable(resolved, settings.file_stable_seconds):
                continue
            try:
                process_file(
                    settings,
                    resolved,
                    force=force,
                    detect_silence_enabled=detect_silence_enabled,
                    detect_freeze_enabled=detect_freeze_enabled,
                    detect_scenes_enabled=detect_scenes_enabled,
                    render_review_enabled=render_review_enabled,
                    render_final_enabled=render_final_enabled,
                    vertical_enabled=vertical_enabled,
                    burn_subtitles_enabled=burn_subtitles_enabled,
                    plan_crop_enabled=plan_crop_enabled,
                    plan_uvr_enabled=plan_uvr_enabled,
                    skip_transcribe=skip_transcribe,
                    progress_enabled=progress_enabled,
                )
                seen.add(resolved)
            except Exception:
                logging.exception("Failed to process %s", resolved)
        time.sleep(settings.poll_interval_seconds)


def iter_media_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS)


def is_file_stable(path: Path, stable_seconds: float) -> bool:
    if not path.exists():
        return False
    first = path.stat()
    time.sleep(stable_seconds)
    if not path.exists():
        return False
    second = path.stat()
    return first.st_size == second.st_size and int(first.st_mtime) == int(second.st_mtime)


def process_file(
    settings: Settings,
    source_path: Path,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
    whisper_language: str | None = None,
) -> Job:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    job = create_job(settings, source_path, force=force)
    return process_job(
        settings,
        job,
        force=force,
        detect_silence_enabled=detect_silence_enabled,
        detect_freeze_enabled=detect_freeze_enabled,
        detect_scenes_enabled=detect_scenes_enabled,
        render_review_enabled=render_review_enabled,
        render_final_enabled=render_final_enabled,
        vertical_enabled=vertical_enabled,
        burn_subtitles_enabled=burn_subtitles_enabled,
        plan_crop_enabled=plan_crop_enabled,
        plan_uvr_enabled=plan_uvr_enabled,
        skip_transcribe=skip_transcribe,
        progress_enabled=progress_enabled,
        whisper_language=whisper_language,
    )


def process_job(
    settings: Settings,
    job: Job,
    *,
    force: bool,
    detect_silence_enabled: bool,
    detect_freeze_enabled: bool,
    detect_scenes_enabled: bool,
    render_review_enabled: bool,
    render_final_enabled: bool,
    vertical_enabled: bool,
    burn_subtitles_enabled: bool,
    plan_crop_enabled: bool,
    plan_uvr_enabled: bool,
    skip_transcribe: bool,
    progress_enabled: bool,
    whisper_language: str | None = None,
    selected_stages: list[str] | None = None,
    expand_selected_dependencies: bool = True,
    completion_status: str | None = None,
    control_callback: Callable[[], str | None] | None = None,
) -> Job:
    logger = configure_job_logger(job)
    progress = ProgressReporter(progress_enabled)
    if job.status in {"needs_review", "done"} and not force:
        logger.info("Skipping completed job %s", job.job_dir)
        progress.emit(
            "pipeline:skip",
            job_dir=str(job.job_dir),
            source_path=str(job.source_path),
            status=job.status,
            reason="already_complete",
        )
        return job
    if job.status in {"failed", "canceled", "paused"}:
        job.set_status("queued")

    try:
        logger.info("Processing %s", job.source_path)
        audio_path = job.job_dir / "audio.wav"
        audio_hq_path = _high_quality_audio_path(settings, job, plan_uvr_enabled=plan_uvr_enabled)
        existing_manifest = None
        manifest_path = job.job_dir / "manifest.json"
        if selected_stages and manifest_path.is_file():
            try:
                candidate = json.loads(manifest_path.read_text(encoding="utf-8"))
                existing_manifest = candidate if isinstance(candidate, dict) else None
            except (OSError, ValueError):
                existing_manifest = None
        context: dict[str, Any] = {
            "audio_path": audio_path,
            "audio_hq_path": audio_hq_path,
            "manifest": existing_manifest,
        }

        def probe_stage(stage_context: dict[str, Any]) -> None:
            manifest = probe_media(settings, job.source_path, job.job_dir / "manifest.json", force=force)
            if manifest["audio_stream_count"] < 1:
                raise RuntimeError("source has no audio stream")
            stage_context["manifest"] = manifest
            if manifest.get("video_stream_count", 0) > 0:
                generate_thumbnail(settings, job.source_path, job.job_dir / "thumbnail.jpg", manifest["duration_seconds"], force=force)

        def extract_audio_stage(stage_context: dict[str, Any]) -> None:
            if not stage_context.get("media_outputs_prepared"):
                extract_audio_outputs(
                    settings,
                    job.source_path,
                    stage_context["audio_path"],
                    stage_context["audio_hq_path"],
                    force=force,
                )
            generate_waveform(settings, stage_context["audio_path"], job.job_dir / "waveform.json", force=force)

        def corruption_stage(stage_context: dict[str, Any]) -> None:
            manifest = stage_context["manifest"]
            if manifest.get("video_stream_count", 0) < 1:
                return
            integrity = extract_audio_outputs(
                settings,
                job.source_path,
                stage_context["audio_path"],
                stage_context["audio_hq_path"],
                integrity_output_path=job.job_dir / "corrupt.json",
                duration=manifest["duration_seconds"],
                force=force,
            )
            stage_context["media_outputs_prepared"] = True
            _raise_for_severe_source_corruption(settings, integrity)

        def transcribe_stage(stage_context: dict[str, Any]) -> None:
            if skip_transcribe:
                create_empty_transcripts(job.job_dir, force=force)
            else:
                manifest = stage_context.get("manifest") or {}
                duration = float(manifest.get("duration_seconds") or 0)
                estimated_seconds = max(settings.whisper_timeout_min_seconds, duration * settings.whisper_timeout_multiplier)
                backend_label = _transcription_backend_label(settings.whisper_backend)
                logger.info("Transcribing with %s", backend_label)
                stop_heartbeat = threading.Event()
                resource_waiting = threading.Event()
                resource_timing = {"wait_started": None, "wait_seconds": 0.0}
                job.stage_estimate_seconds = round(estimated_seconds, 2)
                waiting_callback, acquired_callback = job_gpu_status_callbacks(job, "transcription")

                def on_resource_wait() -> None:
                    resource_waiting.set()
                    if resource_timing["wait_started"] is None:
                        resource_timing["wait_started"] = time.monotonic()
                    waiting_callback()

                def on_resource_acquired() -> None:
                    resource_waiting.clear()
                    wait_started = resource_timing["wait_started"]
                    if wait_started is not None:
                        resource_timing["wait_seconds"] += time.monotonic() - wait_started
                        resource_timing["wait_started"] = None
                    acquired_callback()

                def heartbeat() -> None:
                    while not stop_heartbeat.wait(5):
                        if resource_waiting.is_set():
                            continue
                        elapsed = time.monotonic() - started_at
                        percent = min(95.0, elapsed / estimated_seconds * 100) if estimated_seconds > 0 else None
                        job.update_stage_progress(
                            percent,
                            message=f"{backend_label} transcribing, elapsed {int(elapsed)}s. Percent is estimated.",
                        )

                started_at = time.monotonic()
                heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
                heartbeat_thread.start()
                try:
                    transcribe_settings = replace(settings, whisper_language=whisper_language) if whisper_language else settings
                    transcribe_audio(
                        transcribe_settings,
                        stage_context["audio_path"],
                        job.job_dir,
                        force=force,
                        resource_wait_callback=on_resource_wait,
                        resource_acquired_callback=on_resource_acquired,
                        control_callback=control_callback,
                    )
                finally:
                    stop_heartbeat.set()
                    heartbeat_thread.join(timeout=1)
                    wait_started = resource_timing["wait_started"]
                    if wait_started is not None:
                        resource_timing["wait_seconds"] += time.monotonic() - wait_started
                    elapsed = time.monotonic() - started_at
                    stage_context.setdefault("_stage_metrics", {})["transcribe"] = {
                        "resource_wait_seconds": round(float(resource_timing["wait_seconds"]), 3),
                        "execution_seconds": round(
                            max(0.0, elapsed - float(resource_timing["wait_seconds"])), 3
                        ),
                    }

        def silence_stage(stage_context: dict[str, Any]) -> None:
            logger.info("Detecting silence")
            manifest = stage_context["manifest"]
            detect_silence(settings, stage_context["audio_path"], manifest["duration_seconds"], job.job_dir / "silence.json", force=force)

        def freeze_stage(stage_context: dict[str, Any]) -> None:
            logger.info("Detecting freeze")
            manifest = stage_context["manifest"]
            detect_visual_events(
                settings,
                job.source_path,
                manifest["duration_seconds"],
                job.job_dir / "freeze.json",
                job.job_dir / "scene.json" if detect_scenes_enabled else None,
                force=force,
            )
            stage_context["visual_events_prepared"] = True

        def scenes_stage(stage_context: dict[str, Any]) -> None:
            logger.info("Detecting scene changes")
            if stage_context.get("visual_events_prepared"):
                return
            manifest = stage_context["manifest"]
            detect_visual_events(
                settings,
                job.source_path,
                manifest["duration_seconds"],
                None,
                job.job_dir / "scene.json",
                force=force,
            )

        def cuts_stage(stage_context: dict[str, Any]) -> None:
            manifest = stage_context["manifest"]
            generate_cuts(
                job.job_dir,
                manifest["duration_seconds"],
                force=force,
                min_clip_seconds=settings.cut_min_clip_seconds,
                merge_gap_seconds=settings.cut_merge_gap_seconds,
            )

        def subtitles_stage(stage_context: dict[str, Any]) -> None:
            generate_ass_subtitles(settings, job.job_dir, force=force)
            generate_clipped_ass_subtitles(settings, job.job_dir, force=force)

        def crop_stage(stage_context: dict[str, Any]) -> None:
            generate_vertical_crop_plan(settings, job.job_dir, force=force)

        def uvr_stage(stage_context: dict[str, Any]) -> None:
            generate_uvr_plan(settings, job.job_dir, force=force)

        def render_preview_stage(stage_context: dict[str, Any]) -> None:
            generate_render_preview(settings, job.job_dir, job.source_path, force=force)
            generate_platform_export_plan(settings, job.job_dir, force=force)
            generate_bgm_mix_plan(settings, job.job_dir, force=force)
            generate_webhook_plan(settings, job.job_dir, force=force)

        def run_render_stage(
            stage_name: str,
            stage_context: dict[str, Any],
            render: Callable[[Callable[[float], None], Callable[[], None], Callable[[], None]], None],
        ) -> None:
            stop_heartbeat = threading.Event()
            resource_waiting = threading.Event()
            started_at = time.monotonic()
            state = {"percent": 0.0}
            resource_timing = {"wait_started": None, "wait_seconds": 0.0}
            label = stage_name.replace("_", " ")
            waiting_callback, acquired_callback = job_gpu_status_callbacks(job, label)

            def on_resource_wait() -> None:
                resource_waiting.set()
                if resource_timing["wait_started"] is None:
                    resource_timing["wait_started"] = time.monotonic()
                waiting_callback()

            def on_resource_acquired() -> None:
                resource_waiting.clear()
                wait_started = resource_timing["wait_started"]
                if wait_started is not None:
                    resource_timing["wait_seconds"] += time.monotonic() - wait_started
                    resource_timing["wait_started"] = None
                acquired_callback()

            def callback(percent: float) -> None:
                state["percent"] = round(max(0.0, min(100.0, percent)), 2)
                job.update_stage_progress(
                    state["percent"],
                    message=f"{label} progress {state['percent']:.1f}%.",
                )
                progress.emit("stage:progress", stage=stage_name, percent=state["percent"], job_dir=str(job.job_dir))

            def heartbeat() -> None:
                while not stop_heartbeat.wait(5):
                    if resource_waiting.is_set():
                        continue
                    elapsed = int(time.monotonic() - started_at)
                    job.update_stage_progress(
                        state["percent"],
                        message=f"{label} running, elapsed {elapsed}s. Last parsed progress {state['percent']:.1f}%.",
                    )

            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()
            try:
                render(callback, on_resource_wait, on_resource_acquired)
            finally:
                stop_heartbeat.set()
                heartbeat_thread.join(timeout=1)
                wait_started = resource_timing["wait_started"]
                if wait_started is not None:
                    resource_timing["wait_seconds"] += time.monotonic() - wait_started
                elapsed = time.monotonic() - started_at
                stage_context.setdefault("_stage_metrics", {})[stage_name] = {
                    "resource_wait_seconds": round(float(resource_timing["wait_seconds"]), 3),
                    "execution_seconds": round(
                        max(0.0, elapsed - float(resource_timing["wait_seconds"])), 3
                    ),
                }

        def render_review_stage(stage_context: dict[str, Any]) -> None:
            run_render_stage(
                "render_review",
                stage_context,
                lambda callback, on_wait, on_acquired: render_review_video(
                    settings,
                    job.job_dir,
                    job.source_path,
                    force=force,
                    progress_callback=callback,
                    resource_wait_callback=on_wait,
                    resource_acquired_callback=on_acquired,
                    control_callback=control_callback,
                    refresh_web_preview=False,
                ),
            )

        def render_final_stage(stage_context: dict[str, Any]) -> None:
            run_render_stage(
                "render_final",
                stage_context,
                lambda callback, on_wait, on_acquired: render_final_video(
                    settings,
                    job.job_dir,
                    job.source_path,
                    force=force,
                    vertical=vertical_enabled,
                    burn_subtitles=burn_subtitles_enabled,
                    progress_callback=callback,
                    resource_wait_callback=on_wait,
                    resource_acquired_callback=on_acquired,
                    control_callback=control_callback,
                    refresh_web_preview=False,
                ),
            )

        def render_web_preview_stage(stage_context: dict[str, Any]) -> None:
            final_source = job.job_dir / "final.mp4"
            source = final_source if render_final_enabled or final_source.is_file() else job.job_dir / "review.mp4"
            run_render_stage(
                "render_web_preview",
                stage_context,
                lambda callback, on_wait, on_acquired: render_web_preview(
                    settings,
                    job.job_dir,
                    source_path=source,
                    force=force,
                    progress_callback=callback,
                    resource_wait_callback=on_wait,
                    resource_acquired_callback=on_acquired,
                    control_callback=control_callback,
                ),
            )

        stage_selection = (
            expand_stage_selection(selected_stages)
            if expand_selected_dependencies
            else ({str(stage).strip() for stage in selected_stages if str(stage).strip()} if selected_stages else None)
        )

        def enabled(stage_name: str, default: bool) -> bool:
            if stage_selection is not None and not expand_selected_dependencies:
                return stage_name in stage_selection
            return default and (stage_selection is None or stage_name in stage_selection)

        stages = [
            PipelineStage("probe", PIPELINE_STAGE_SPECS["probe"].status, enabled("probe", True), probe_stage),
            PipelineStage("detect_corruption", PIPELINE_STAGE_SPECS["detect_corruption"].status, enabled("detect_corruption", settings.source_integrity_scan_enabled), corruption_stage),
            PipelineStage("extract_audio", PIPELINE_STAGE_SPECS["extract_audio"].status, enabled("extract_audio", True), extract_audio_stage),
            PipelineStage("transcribe", PIPELINE_STAGE_SPECS["transcribe"].status, enabled("transcribe", True), transcribe_stage),
            PipelineStage("detect_silence", PIPELINE_STAGE_SPECS["detect_silence"].status, enabled("detect_silence", detect_silence_enabled), silence_stage),
            PipelineStage("detect_freeze", PIPELINE_STAGE_SPECS["detect_freeze"].status, enabled("detect_freeze", detect_freeze_enabled), freeze_stage),
            PipelineStage("detect_scenes", PIPELINE_STAGE_SPECS["detect_scenes"].status, enabled("detect_scenes", detect_scenes_enabled), scenes_stage),
            PipelineStage("plan_cuts", PIPELINE_STAGE_SPECS["plan_cuts"].status, enabled("plan_cuts", True), cuts_stage),
            PipelineStage("plan_crop", PIPELINE_STAGE_SPECS["plan_crop"].status, enabled("plan_crop", plan_crop_enabled or vertical_enabled), crop_stage),
            PipelineStage("style_subtitles", PIPELINE_STAGE_SPECS["style_subtitles"].status, enabled("style_subtitles", (not skip_transcribe) or burn_subtitles_enabled), subtitles_stage),
            PipelineStage("plan_uvr", PIPELINE_STAGE_SPECS["plan_uvr"].status, enabled("plan_uvr", plan_uvr_enabled), uvr_stage),
            PipelineStage("plan_render", PIPELINE_STAGE_SPECS["plan_render"].status, enabled("plan_render", True), render_preview_stage),
            PipelineStage("render_review", PIPELINE_STAGE_SPECS["render_review"].status, enabled("render_review", render_review_enabled), render_review_stage),
            PipelineStage("render_final", PIPELINE_STAGE_SPECS["render_final"].status, enabled("render_final", render_final_enabled), render_final_stage),
            PipelineStage(
                "render_web_preview",
                PIPELINE_STAGE_SPECS["render_web_preview"].status,
                enabled(
                    "render_web_preview",
                    getattr(settings, "web_preview_enabled", True)
                    and (render_review_enabled or render_final_enabled),
                ),
                render_web_preview_stage,
            ),
        ]
        web_preview_dependencies = {"render_final"} if render_final_enabled else {"render_review"}
        stages = [
            replace(
                stage,
                dependencies=frozenset(
                    web_preview_dependencies
                    if stage.name == "render_web_preview"
                    else PIPELINE_STAGE_DEPENDENCIES[stage.name]
                ),
                exclusive_resources=_stage_exclusive_resources(settings, stage.name),
            )
            for stage in stages
        ]
        database_path = (
            library_database_path(settings)
            if hasattr(settings, "jobs_dir")
            else Path(job.job_dir).parent.parent / "library.sqlite3"
        )
        stage_repository = StageRunRepository(database_path)
        context["_stage_repository"] = stage_repository
        context["_max_parallel_stages"] = 3
        if control_callback is None:
            run_pipeline(progress, job, stages, context)
        else:
            run_pipeline(progress, job, stages, context, control_callback=control_callback)
        job.set_status(completion_status or ("done" if render_final_enabled else "needs_review"))
        progress.emit(
            "pipeline:complete",
            job_dir=str(job.job_dir),
            source_path=str(job.source_path),
            status=job.status,
        )
        logger.info("Job complete: %s", job.job_dir)
        return job
    except QueueControlRequested as exc:
        logger.info("Queue control requested: %s", exc.action)
        if exc.action == "paused":
            job.set_status("paused")
        else:
            job.cancel()
        raise
    except Exception as exc:
        logger.exception("Job failed")
        job.fail(str(exc))
        progress.emit(
            "pipeline:error",
            job_dir=str(job.job_dir),
            source_path=str(job.source_path),
            status=job.status,
            error=str(exc),
        )
        return job
    finally:
        close_job_logger(logger)


def _stage_exclusive_resources(settings: Settings, stage_name: str) -> frozenset[str]:
    if stage_name == "transcribe" and transcription_uses_gpu(settings):
        return frozenset({"gpu"})
    if stage_name in {"render_review", "render_final", "render_web_preview"} and rendering_uses_gpu(settings):
        return frozenset({"gpu"})
    if stage_name == "plan_uvr" and str(getattr(settings, "demucs_device", "")).lower().startswith("cuda"):
        return frozenset({"gpu"})
    return frozenset()


def expand_stage_selection(selected_stages: list[str] | None) -> set[str] | None:
    if not selected_stages:
        return None
    requested = {str(stage).strip() for stage in selected_stages if str(stage).strip()}
    unknown = sorted(requested - PIPELINE_STAGE_SELECTION_DEPENDENCIES.keys())
    if unknown:
        raise ValueError(f"unknown pipeline stage: {unknown[0]}")
    expanded = set(requested)
    pending = list(requested)
    while pending:
        stage = pending.pop()
        for dependency in PIPELINE_STAGE_SELECTION_DEPENDENCIES[stage]:
            if dependency not in expanded:
                expanded.add(dependency)
                pending.append(dependency)
    return expanded


def build_pipeline_batches(
    stages: list[PipelineStage],
    *,
    max_parallel_stages: int,
) -> list[list[PipelineStage]]:
    """Build stable dependency batches without sharing exclusive resources."""
    if not stages:
        return []
    concurrency = max(1, int(max_parallel_stages))
    stage_names = {stage.name for stage in stages}
    if len(stage_names) != len(stages):
        raise ValueError("pipeline stage names must be unique")

    remaining = list(stages)
    completed: set[str] = set()
    batches: list[list[PipelineStage]] = []
    while remaining:
        ready = [
            stage
            for stage in remaining
            if (stage.dependencies & stage_names).issubset(completed)
        ]
        if not ready:
            blocked = ", ".join(stage.name for stage in remaining)
            raise ValueError(f"pipeline dependency cycle or unresolved dependency among: {blocked}")

        batch: list[PipelineStage] = []
        resources_in_use: set[str] = set()
        for stage in ready:
            if len(batch) >= concurrency:
                break
            if stage.exclusive_resources & resources_in_use:
                continue
            batch.append(stage)
            resources_in_use.update(stage.exclusive_resources)
        if not batch:
            batch = [ready[0]]

        batches.append(batch)
        selected = {stage.name for stage in batch}
        completed.update(selected)
        remaining = [stage for stage in remaining if stage.name not in selected]
    return batches


def _execute_pipeline_stage(
    progress: ProgressReporter,
    job: Job,
    stage: PipelineStage,
    context: dict[str, Any],
    *,
    stage_number: int,
    total_stages: int,
    job_name: str,
    pipeline_run_id: str | None,
    stage_repository: StageRunRepository | None,
    control_callback: Callable[[], str | None] | None,
) -> tuple[dict[str, Any], BaseException | None]:
    stage_payload = {
        "job_dir": str(job.job_dir),
        "source_path": str(job.source_path),
        "stage": stage.name,
        "stage_number": stage_number,
        "total_stages": total_stages,
    }
    action = control_callback() if control_callback else None
    if action in {"paused", "canceled"}:
        return (
            {
                "stage": stage.name,
                "status": action,
                "stage_number": stage_number,
                "total_stages": total_stages,
                "duration_seconds": 0.0,
            },
            QueueControlRequested(action),
        )
    if not stage.enabled:
        progress.emit("stage:skip", **stage_payload, reason="disabled")
        timing = {
            "stage": stage.name,
            "status": "skipped",
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": 0.0,
            "reason": "disabled",
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status="skipped",
                duration_seconds=0.0,
            )
        return timing, None

    started_at = time.monotonic()
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.record_stage(
            pipeline_run_id,
            job_name,
            stage.name,
            stage_number=stage_number,
            total_stages=total_stages,
            status="running",
        )
    progress.emit("stage:start", **stage_payload, status=job.status)
    try:
        stage.run(context)
    except QueueControlRequested as exc:
        duration = time.monotonic() - started_at
        metrics = _take_stage_metrics(context, stage.name)
        progress.emit(
            "stage:control",
            **stage_payload,
            status=exc.action,
            duration_seconds=round(duration, 3),
            **metrics,
        )
        timing = {
            "stage": stage.name,
            "status": exc.action,
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": round(duration, 3),
            **metrics,
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status=exc.action,
                duration_seconds=duration,
            )
        return timing, exc
    except Exception as exc:
        duration = time.monotonic() - started_at
        metrics = _take_stage_metrics(context, stage.name)
        progress.emit(
            "stage:error",
            **stage_payload,
            status=job.status,
            duration_seconds=round(duration, 3),
            error=str(exc),
            **metrics,
        )
        timing = {
            "stage": stage.name,
            "status": "failed",
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": round(duration, 3),
            "error": str(exc),
            **metrics,
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status="failed",
                duration_seconds=duration,
                error=str(exc),
            )
        return timing, exc

    duration = time.monotonic() - started_at
    metrics = _take_stage_metrics(context, stage.name)
    timing = {
        "stage": stage.name,
        "status": "complete",
        "stage_number": stage_number,
        "total_stages": total_stages,
        "duration_seconds": round(duration, 3),
        **metrics,
    }
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.record_stage(
            pipeline_run_id,
            job_name,
            stage.name,
            stage_number=stage_number,
            total_stages=total_stages,
            status="complete",
            duration_seconds=duration,
        )
    progress.emit(
        "stage:complete",
        **stage_payload,
        status=job.status,
        duration_seconds=round(duration, 3),
        **metrics,
    )
    return timing, None


def _take_stage_metrics(context: dict[str, Any], stage_name: str) -> dict[str, float]:
    metrics_by_stage = context.get("_stage_metrics")
    if not isinstance(metrics_by_stage, dict):
        return {}
    raw = metrics_by_stage.pop(stage_name, None)
    if not isinstance(raw, dict):
        return {}
    metrics: dict[str, float] = {}
    for key in ("resource_wait_seconds", "execution_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            metrics[key] = round(max(0.0, float(value)), 3)
    return metrics


def run_pipeline(
    progress: ProgressReporter,
    job: Job,
    stages: list[PipelineStage],
    context: dict[str, Any],
    *,
    control_callback: Callable[[], str | None] | None = None,
    stage_repository: StageRunRepository | None = None,
) -> None:
    total_stages = len(stages)
    if stage_repository is None:
        candidate = context.get("_stage_repository")
        if isinstance(candidate, StageRunRepository):
            stage_repository = candidate
    job_name = Path(job.job_dir).name
    pipeline_run_id = (
        stage_repository.start_pipeline(job_name, total_stages=total_stages)
        if stage_repository is not None
        else None
    )
    timings: list[dict[str, Any]] = []
    pipeline_started_at = datetime.now().isoformat(timespec="seconds")
    _write_stage_timings(job, timings, status="running", total_stages=total_stages, started_at=pipeline_started_at)
    progress.emit(
        "pipeline:start",
        job_dir=str(job.job_dir),
        source_path=str(job.source_path),
        total_stages=total_stages,
    )
    max_parallel_stages = max(1, int(context.get("_max_parallel_stages", 1)))
    batches = build_pipeline_batches(stages, max_parallel_stages=max_parallel_stages)
    stage_numbers = {stage.name: index for index, stage in enumerate(stages, start=1)}
    for batch in batches:
        action = control_callback() if control_callback else None
        if action in {"paused", "canceled"}:
            if stage_repository is not None and pipeline_run_id is not None:
                stage_repository.finish_pipeline(pipeline_run_id, action)
            raise QueueControlRequested(action)
        primary_stage = next((stage for stage in batch if stage.enabled), None)
        if primary_stage is not None:
            job.start_stage(primary_stage.status, primary_stage.name)
        batch_results: list[tuple[dict[str, Any], BaseException | None]] = []
        if len(batch) == 1:
            stage = batch[0]
            batch_results.append(
                _execute_pipeline_stage(
                    progress,
                    job,
                    stage,
                    context,
                    stage_number=stage_numbers[stage.name],
                    total_stages=total_stages,
                    job_name=job_name,
                    pipeline_run_id=pipeline_run_id,
                    stage_repository=stage_repository,
                    control_callback=control_callback,
                )
            )
        else:
            with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="pipeline-stage") as executor:
                futures = [
                    executor.submit(
                        _execute_pipeline_stage,
                        progress,
                        job,
                        stage,
                        context,
                        stage_number=stage_numbers[stage.name],
                        total_stages=total_stages,
                        job_name=job_name,
                        pipeline_run_id=pipeline_run_id,
                        stage_repository=stage_repository,
                        control_callback=control_callback,
                    )
                    for stage in batch
                ]
                batch_results.extend(future.result() for future in as_completed(futures))

        batch_results.sort(key=lambda result: int(result[0]["stage_number"]))
        timings.extend(result[0] for result in batch_results)
        timings.sort(key=lambda timing: int(timing["stage_number"]))
        errors = [result[1] for result in batch_results if result[1] is not None]
        pipeline_status = "running"
        if errors:
            pipeline_status = errors[0].action if isinstance(errors[0], QueueControlRequested) else "failed"
        _write_stage_timings(
            job,
            timings,
            status=pipeline_status,
            total_stages=total_stages,
            started_at=pipeline_started_at,
        )
        if errors:
            error = errors[0]
            if stage_repository is not None and pipeline_run_id is not None:
                if isinstance(error, QueueControlRequested):
                    stage_repository.finish_pipeline(pipeline_run_id, error.action)
                else:
                    stage_repository.finish_pipeline(pipeline_run_id, "failed", error=str(error))
            raise error
        if primary_stage is not None:
            job.complete_stage()
    _write_stage_timings(job, timings, status="complete", total_stages=total_stages, started_at=pipeline_started_at)
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.finish_pipeline(pipeline_run_id, "complete")


def _high_quality_audio_path(settings: Settings, job: Job, *, plan_uvr_enabled: bool) -> Path | None:
    if plan_uvr_enabled or getattr(settings, "high_quality_audio_enabled", True):
        return job.job_dir / "audio_hq.flac"
    return None


def _write_stage_timings(
    job: Job,
    stages: list[dict[str, Any]],
    *,
    status: str,
    total_stages: int,
    started_at: str,
) -> None:
    payload = {
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "total_stages": total_stages,
        "total_duration_seconds": round(sum(float(item.get("duration_seconds") or 0.0) for item in stages), 3),
        "stages": stages,
    }
    write_json_atomic(job.job_dir / "stage_timings.json", payload)


def create_empty_transcripts(job_dir: Path, *, force: bool) -> None:
    outputs = {
        "transcript.txt": "",
        "transcript.srt": "",
        "transcript.json": json.dumps({"segments": []}, ensure_ascii=False, indent=2),
    }
    for name, content in outputs.items():
        path = job_dir / name
        if path.exists() and not force:
            continue
        write_text_atomic(path, content)
