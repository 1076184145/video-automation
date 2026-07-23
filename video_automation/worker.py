from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .cleanup import cleanup_jobs
from .config import Settings
from .health import health_check, health_payload  # Re-export health_payload for compatibility.
from .jobs import Job, create_job, find_resume_jobs, list_jobs
from .media import MEDIA_EXTENSIONS
from .pipeline_executor import (
    _raise_for_severe_source_corruption,
    _transcription_backend_label,
    create_empty_transcripts,
    process_job,
)
from .pipeline_scheduler import (
    PipelineStage,
    ProgressReporter,
    build_pipeline_batches,
    expand_stage_selection,
    run_pipeline,
)
from .profiles import apply_profile_settings, profile_flags


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
