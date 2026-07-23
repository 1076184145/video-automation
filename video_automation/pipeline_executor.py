from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .crop import generate_vertical_crop_plan
from .cuts import generate_cuts
from .hooks import generate_uvr_plan
from .io_utils import write_text_atomic
from .jobs import Job, close_job_logger, configure_job_logger
from .library_api import library_database_path
from .media import (
    detect_silence,
    detect_visual_events,
    extract_audio_outputs,
    generate_thumbnail,
    generate_waveform,
    probe_media,
)
from .pipeline_scheduler import PipelineStage, ProgressReporter, expand_stage_selection, run_pipeline
from .pipeline_spec import PIPELINE_STAGE_DEPENDENCIES, PIPELINE_STAGE_SPECS
from .plans import generate_bgm_mix_plan, generate_platform_export_plan, generate_webhook_plan
from .render import generate_render_preview, render_final_video, render_review_video, render_web_preview
from .resources import job_gpu_status_callbacks, rendering_uses_gpu, transcription_uses_gpu
from .stage_runs import StageRunRepository
from .subtitles import generate_ass_subtitles, generate_clipped_ass_subtitles
from .task_queue import QueueControlRequested
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


def _high_quality_audio_path(settings: Settings, job: Job, *, plan_uvr_enabled: bool) -> Path | None:
    if plan_uvr_enabled or getattr(settings, "high_quality_audio_enabled", True):
        return job.job_dir / "audio_hq.flac"
    return None


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
