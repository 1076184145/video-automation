from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .api_context import (
    APIContext,
    allowed_api_origins as _allowed_api_origins,
    default_api_origins as _default_api_origins,
    normalize_origin as _normalize_origin,
)
from .api_http_utils import (
    event_last_id as _event_last_id,
    format_sse as _format_sse,
    parse_range as _parse_range,
)
from .api_job_utils import (
    bounded_text as _bounded_text,
    job_feedback as _job_feedback,
    job_files as _job_files,
    job_is_terminal as _job_is_terminal,
    job_runtime_state as _job_runtime_state,
    pid_is_alive as _pid_is_alive,
    publish_job_dir_event as _publish_job_dir_event,
    record_transcript_preferences as _record_transcript_preferences,
    remove_render_outputs as _remove_render_outputs,
    safe_float as _safe_float,
    safe_int as _safe_int,
    save_clip_feedback as _save_clip_feedback,
    segments_to_srt as _segments_to_srt,
    srt_time as _srt_time,
    string_list as _string_list,
    transcript_summary as _transcript_summary,
    update_transcript_from_editor as _update_transcript_from_editor,
    validate_transcript_segments as _validate_transcript_segments,
)
from .api_routes_base import CoreHTTPRoutes
from .api_routes_enhancements import EnhancementRoutes
from .api_routes_jobs import JobRoutes
from .api_routes_system import SystemRoutes
from .api_security import require_safe_api_binding
from .api_settings import (
    normalize_env_updates as _normalize_env_updates,
    update_env_file as _update_env_file,
)
from .api_system import (
    TOOLS_INSTALL_LOCK,
    TOOLS_INSTALL_STATE,
    health_response as _health_response,
    publish_package_queue as _publish_package_queue,
    recording_files as _recording_files,
    recording_upload_path as _recording_upload_path,
    resume_tombstone_cleanup as _resume_tombstone_cleanup,
    run_tools_install as _run_tools_install,
    schedule_tombstone_cleanup as _schedule_tombstone_cleanup,
    set_tools_install_state as _set_tools_install_state,
    tools_install_snapshot as _tools_install_snapshot,
)
from .config import Settings
from .covers import generate_cover_candidates
from .events import configure_event_store
from .hooks import generate_uvr_plan
from .io_utils import read_json_file, write_json_atomic
from .jobs import Job, load_job
from .library_api import (
    automation_repository_for,
    library_database_path,
    queue_repository_for,
)
from .llm_tools import generate_highlights, generate_metadata
from .pipeline_spec import PIPELINE_STAGE_SPECS
from .publish import generate_publish_package
from .profiles import apply_profile_flags, apply_profile_settings
from .project_exports import generate_project_exports
from .render import render_final_video, render_highlight_video
from .segments import generate_platform_segments
from .subtitle_translation import translate_subtitles, translated_clipped_ass_name, translated_final_video_name
from .queue_worker import QueueWorkerProcess
from .runtime_config import apply_runtime_settings_snapshot
from .task_queue import QueueControlRequested
from .recovery import backup_database, ensure_database_ready, ensure_job_capacity
from .transcribe import warm_transcription_backend
from .pipeline_executor import process_job

RERUN_STATUS = {name: spec.status for name, spec in PIPELINE_STAGE_SPECS.items()}


class AutomationHTTPServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can allow multiple live processes to bind the
    # same port and split incoming requests between different code versions.
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        queue_worker: QueueWorkerProcess | None,
    ):
        self.queue_worker = queue_worker
        super().__init__(server_address, handler)

    def server_close(self) -> None:
        if self.queue_worker is not None:
            self.queue_worker.stop()
        super().server_close()


def create_server(settings: Settings, *, start_queue_worker: bool = True) -> ThreadingHTTPServer:
    require_safe_api_binding(settings)
    database_path = library_database_path(settings)
    ensure_database_ready(database_path)
    configure_event_store(database_path)
    handler = _handler_class(settings)
    if database_path.is_file():
        backup_database(database_path, keep=5)
    _resume_tombstone_cleanup(settings.jobs_dir)
    queue_worker = QueueWorkerProcess(settings) if start_queue_worker else None
    server = AutomationHTTPServer((settings.api_host, settings.api_port), handler, queue_worker)
    if queue_worker is not None:
        try:
            queue_worker.start()
        except Exception:
            server.server_close()
            raise
    return server


def serve(settings: Settings) -> None:
    server = create_server(settings)
    print(f"Video Automation API listening on http://{settings.api_host}:{settings.api_port}", flush=True)
    server.serve_forever()


def _start_transcription_warmup(settings: Settings) -> None:
    if settings.whisper_backend not in {"funasr", "funasr-whisper", "funasr-faster-whisper"}:
        return
    if not settings.funasr_persistent_worker:
        return
    threading.Thread(target=warm_transcription_backend, args=(settings,), daemon=True).start()


def _handler_class(settings: Settings) -> type[BaseHTTPRequestHandler]:
    context = APIContext(settings)

    class Handler(
        EnhancementRoutes,
        JobRoutes,
        SystemRoutes,
        CoreHTTPRoutes,
        BaseHTTPRequestHandler,
    ):
        api_context = context

    return Handler

def _execute_queue_item(settings: Settings, item: dict[str, Any]) -> None:
    job_name = str(item.get("job_name") or "")
    job = load_job(Path(settings.jobs_dir) / job_name / "job.json")
    if job is None:
        raise RuntimeError(f"queued job not found: {job_name}")
    ensure_job_capacity(settings, job.source_path)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    write_json_atomic(job.job_dir / "run_config.json", {
        "queue_id": item.get("id"),
        "attempt": item.get("attempt"),
        "retry_stage": item.get("retry_stage"),
        "runtime_settings": payload.get("_runtime_settings_snapshot"),
        "recipe": payload.get("_recipe_snapshot"),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    })
    job_settings, options = _queued_process_config(settings, payload)
    retry_stage = str(item.get("retry_stage") or "").strip()
    def control_callback() -> str | None:
        return _queue_control_action(settings, str(item.get("id") or ""))
    command = str(payload.get("_command") or "").strip()
    if command:
        _execute_managed_job_command(
            job_settings,
            job,
            command,
            payload.get("_command_payload") if isinstance(payload.get("_command_payload"), dict) else {},
            control_callback=control_callback,
        )
        return
    if retry_stage:
        if retry_stage not in RERUN_STATUS:
            raise RuntimeError(f"unsupported retry stage: {retry_stage}")
        options.update({
            "force": True,
            "selected_stages": [retry_stage],
            "expand_selected_dependencies": False,
            "completion_status": "needs_review",
            "control_callback": control_callback,
        })
        process_job(job_settings, job, **options)
    else:
        options["control_callback"] = control_callback
        process_job(job_settings, job, **options)
    if job.status == "failed":
        raise RuntimeError(job.error or "job failed")


def _execute_managed_job_command(
    settings: Settings,
    job: Job,
    command: str,
    payload: dict[str, Any],
    *,
    control_callback: Callable[[], str | None],
) -> None:
    def check_control() -> None:
        action = control_callback()
        if action in {"paused", "canceled"}:
            raise QueueControlRequested(action)

    check_control()
    if command == "generate_covers":
        try:
            generate_cover_candidates(
                settings,
                job.job_dir,
                title=str(payload.get("title") or "").strip(),
                style=str(payload.get("style") or "short_video").strip(),
                count=int(payload.get("count") or settings.cover_count),
                aspects=[str(value) for value in payload.get("aspects", [])]
                if isinstance(payload.get("aspects"), list)
                else None,
            )
        finally:
            _publish_job_dir_event(job.job_dir)
        check_control()
        return
    if command == "generate_segments":
        generate_platform_segments(
            settings,
            job.job_dir,
            platforms=_string_list(payload.get("platforms")),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_metadata":
        generate_metadata(
            settings,
            job.job_dir,
            platform=str(payload.get("platform") or "douyin"),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_highlights":
        generate_highlights(settings, job.job_dir, force=bool(payload.get("force", False)))
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_publish_package":
        generate_publish_package(
            settings,
            job.job_dir,
            platforms=_string_list(payload.get("platforms")),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_project_export":
        generate_project_exports(
            settings,
            job.job_dir,
            targets=_string_list(payload.get("targets")),
            include_clips=bool(payload.get("include_clips", False)),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "translate_subtitles":
        target_language = str(payload.get("target_language") or "zh").strip() or "zh"
        translate_subtitles(
            settings,
            job.job_dir,
            target_language=target_language,
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "render_highlight":
        highlight_cut = payload.get("highlight_cut") if isinstance(payload.get("highlight_cut"), dict) else {}
        status_path = job.job_dir / "highlight_render_status.json"
        status_base = {
            "output": "highlight.mp4",
            "duration_seconds": highlight_cut.get("duration_seconds", 0),
            "selected_clip_count": highlight_cut.get("selected_clip_count", 0),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            write_json_atomic(status_path, {**status_base, "status": "rendering", "message": "Rendering highlight video."})
            render_highlight_video(
                settings,
                job.job_dir,
                job.source_path,
                force=True,
                resource_wait_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "waiting_for_gpu", "message": "Waiting for GPU to render highlight video."}
                ),
                resource_acquired_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "rendering", "message": "GPU available. Rendering highlight video."}
                ),
                control_callback=control_callback,
            )
            write_json_atomic(status_path, {
                **status_base,
                "status": "done",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
        except QueueControlRequested:
            write_json_atomic(status_path, {**status_base, "status": "canceled"})
            raise
        except Exception as exc:
            write_json_atomic(status_path, {**status_base, "status": "failed", "error": str(exc)})
            raise
        finally:
            _publish_job_dir_event(job.job_dir)
        return
    if command == "render_translated_subtitles":
        target_language = str(payload.get("target_language") or "zh").strip() or "zh"
        output_filename = str(payload.get("output_filename") or translated_final_video_name(target_language))
        status_path = job.job_dir / f"subtitle_translation_render_{target_language}.json"
        status_base = {
            "target_language": target_language,
            "output": output_filename,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        preview = read_json_file(job.job_dir / "final_render_preview.json") or {}
        try:
            write_json_atomic(status_path, {**status_base, "status": "rendering", "message": "Rendering translated subtitles."})
            render_final_video(
                settings,
                job.job_dir,
                job.source_path,
                force=True,
                vertical=bool(preview.get("vertical", False)),
                burn_subtitles=True,
                subtitle_filename=translated_clipped_ass_name(target_language),
                output_filename=output_filename,
                resource_wait_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "waiting_for_gpu", "message": "Waiting for GPU to render translated subtitles."}
                ),
                resource_acquired_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "rendering", "message": "GPU available. Rendering translated subtitles."}
                ),
                control_callback=control_callback,
            )
            write_json_atomic(status_path, {
                **status_base,
                "status": "done",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
        except QueueControlRequested:
            write_json_atomic(status_path, {**status_base, "status": "canceled"})
            raise
        except Exception as exc:
            write_json_atomic(status_path, {**status_base, "status": "failed", "error": str(exc)})
            raise
        finally:
            _publish_job_dir_event(job.job_dir)
        return
    raise RuntimeError(f"unsupported managed job command: {command}")


def _queue_control_action(settings: Settings, queue_id: str) -> str | None:
    current = queue_repository_for(settings).get(queue_id)
    if not current:
        return "canceled"
    if current.get("cancel_requested"):
        return "canceled"
    if current.get("pause_requested"):
        return "paused"
    return None


def _queued_process_config(settings: Settings, payload: dict[str, Any]) -> tuple[Settings, dict[str, Any]]:
    settings = apply_runtime_settings_snapshot(settings, payload.get("_runtime_settings_snapshot"))
    effective = dict(payload)
    recipe_id = str(payload.get("recipe_id") or "").strip()
    if recipe_id:
        recipe = payload.get("_recipe_snapshot")
        if not isinstance(recipe, dict):
            recipe = automation_repository_for(settings).get_recipe(recipe_id)
        if recipe is None:
            raise RuntimeError(f"recipe not found: {recipe_id}")
        effective = {**recipe.get("options", {}), **effective}
        effective["recipe_stages"] = recipe.get("stages", [])
    profile = str(effective.get("profile") or "").strip()
    job_settings = apply_profile_settings(settings, profile)
    if "source_integrity_scan" in effective:
        job_settings = replace(
            job_settings,
            source_integrity_scan_enabled=bool(effective.get("source_integrity_scan", False)),
        )
    options = apply_profile_flags({
        "force": bool(effective.get("force", False)),
        "detect_silence_enabled": bool(effective.get("detect_silence", False)),
        "detect_freeze_enabled": bool(effective.get("detect_freeze", False)),
        "detect_scenes_enabled": bool(effective.get("detect_scenes", False)),
        "render_review_enabled": bool(effective.get("render_review", False)),
        "render_final_enabled": bool(effective.get("render_final", False)),
        "vertical_enabled": bool(effective.get("vertical", False)),
        "burn_subtitles_enabled": bool(effective.get("burn_subtitles", False)),
        "plan_crop_enabled": bool(effective.get("plan_crop", False)),
        "plan_uvr_enabled": bool(effective.get("plan_uvr", False)),
        "skip_transcribe": bool(effective.get("skip_transcribe", False)),
        "progress_enabled": False,
        "whisper_language": str(effective.get("whisper_language") or "").strip() or None,
        "selected_stages": effective.get("recipe_stages") if isinstance(effective.get("recipe_stages"), list) else None,
    }, profile)
    return job_settings, options


def _run_single_stage(
    settings: Settings,
    job: Job,
    stage: str,
    options: dict[str, Any],
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    """Compatibility entry point backed by the canonical pipeline implementation."""
    if stage not in RERUN_STATUS:
        raise ValueError(f"unsupported stage: {stage}")
    job_settings, process_options = _queued_process_config(
        settings,
        {"path": str(job.source_path), **dict(options)},
    )
    process_options.update({
        "force": True,
        "selected_stages": [stage],
        "expand_selected_dependencies": False,
        "completion_status": "needs_review",
        "control_callback": control_callback,
    })
    process_job(job_settings, job, **process_options)
