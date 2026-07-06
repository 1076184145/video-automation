from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import shutil
import subprocess
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
from .hooks import generate_uvr_plan
from .io_utils import write_json_atomic, write_text_atomic
from .jobs import Job, configure_job_logger, create_job, find_resume_jobs, list_jobs
from .media import MEDIA_EXTENSIONS, detect_silence, detect_visual_events, extract_audio_outputs, generate_thumbnail, generate_waveform, probe_media
from .plans import generate_bgm_mix_plan, generate_platform_export_plan, generate_webhook_plan
from .profiles import apply_profile_settings, profile_flags
from .render import generate_render_preview, render_final_video, render_review_video
from .resources import job_gpu_status_callbacks
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
    parser.add_argument("--cleanup-days", type=int, help="Remove jobs older than N days")
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
        payload = cleanup_jobs(settings, days=args.cleanup_days, dry_run=args.dry_run)
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


def health_check(settings: Settings, *, as_json: bool = False) -> int:
    payload = health_payload(settings)
    ok = bool(payload["ok"])
    results = payload["checks"]
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for result in results:
            mark = "OK" if result["exists"] else "MISSING"
            suffix = f" - {result['version']}" if result["version"] else ""
            print(f"{mark:7} {result['name']}: {result['path']}{suffix}")
        if not ok:
            print("Configure missing tools in .env or add them to PATH.")
    return 0 if ok else 1


_health_cache: dict[str, Any] | None = None
_health_cache_time: float = 0.0
_HEALTH_CACHE_TTL = 30.0  # seconds


def health_payload(settings: Settings) -> dict[str, Any]:
    global _health_cache, _health_cache_time  # noqa: PLW0603
    now = time.monotonic()
    if _health_cache is not None and (now - _health_cache_time) < _HEALTH_CACHE_TTL:
        return _health_cache
    result = _build_health_payload(settings)
    _health_cache = result
    _health_cache_time = now
    return result


def clear_health_cache() -> None:
    global _health_cache, _health_cache_time  # noqa: PLW0603
    _health_cache = None
    _health_cache_time = 0.0


def _build_health_payload(settings: Settings) -> dict[str, Any]:
    whisper_kind = "exe" if settings.whisper_backend == "cli" else "optional_exe"
    checks = [
        ("root", settings.root, "dir"),
        ("input_recordings_dir", settings.input_recordings_dir, "dir"),
        ("jobs_dir", settings.jobs_dir, "dir"),
        ("logs_dir", settings.logs_dir, "dir"),
        ("ffmpeg_path", settings.ffmpeg_path, "exe"),
        ("ffprobe_path", settings.ffprobe_path, "exe"),
        ("audiowaveform_path", settings.audiowaveform_path, "optional_exe"),
        ("whisper_bin", settings.whisper_bin, whisper_kind),
    ]
    results = []
    ok = True
    for name, path, kind in checks:
        exists = _path_exists(path, kind)
        optional = kind == "optional_exe"
        version = ""
        if exists and kind == "exe" and name in {"ffmpeg_path", "ffprobe_path"}:
            version = _first_version_line(path)
        status = "ok" if exists else "optional_missing" if optional else "missing"
        result = {
            "name": name,
            "path": str(path),
            "exists": exists,
            "required": not optional,
            "optional": optional,
            "status": status,
            "version": version,
        }
        results.append(result)
        ok = ok and (exists or optional)
    for result in _transcription_runtime_checks(settings):
        results.append(result)
        ok = ok and (result["exists"] or result["optional"])
    for result in _render_runtime_checks(settings):
        results.append(result)
        ok = ok and (result["exists"] or result["optional"])
    for result in _cover_runtime_checks(settings):
        results.append(result)
        ok = ok and (result["exists"] or result["optional"])
    for result in _optional_module_checks(settings):
        results.append(result)
        ok = ok and (result["exists"] or result["optional"])
    return {"ok": ok, "checks": results, "settings": _settings_payload(settings)}


def _render_runtime_checks(settings: Settings) -> list[dict[str, Any]]:
    encoder = settings.render_video_encoder.strip().lower()
    if encoder not in {"h264_nvenc", "nvenc"}:
        return []
    exists = _ffmpeg_has_encoder(settings.ffmpeg_path, "h264_nvenc")
    return [{
        "name": "h264_nvenc",
        "path": str(settings.ffmpeg_path),
        "exists": exists,
        "required": True,
        "optional": False,
        "status": "ok" if exists else "missing",
        "version": "NVIDIA NVENC H.264 encoder" if exists else "",
    }]


def _transcription_runtime_checks(settings: Settings) -> list[dict[str, Any]]:
    if settings.whisper_backend in {"funasr-whisper", "funasr-faster-whisper"}:
        return [
            *_funasr_runtime_checks(settings, optional=True),
            *_faster_whisper_runtime_checks(settings, optional=False),
        ]
    if settings.whisper_backend == "funasr":
        return _funasr_runtime_checks(settings)
    if settings.whisper_backend != "faster-whisper":
        return []
    return _faster_whisper_runtime_checks(settings, optional=False)


def _faster_whisper_runtime_checks(settings: Settings, *, optional: bool = False) -> list[dict[str, Any]]:
    checks = []
    faster_exists = importlib.util.find_spec("faster_whisper") is not None
    checks.append({
        "name": "faster_whisper",
        "path": "python:faster_whisper",
        "exists": faster_exists,
        "required": not optional,
        "optional": optional,
        "status": "ok" if faster_exists else "optional_missing" if optional else "missing",
        "version": "",
    })
    if settings.faster_whisper_device.strip().lower() != "cuda":
        return checks
    cuda_exists = False
    version = ""
    try:
        import ctranslate2

        version = getattr(ctranslate2, "__version__", "")
        get_count = getattr(ctranslate2, "get_cuda_device_count", None)
        cuda_exists = bool(callable(get_count) and get_count() > 0)
        if cuda_exists and hasattr(ctranslate2, "get_supported_compute_types"):
            types = sorted(ctranslate2.get_supported_compute_types("cuda"))
            version = f"ctranslate2 {version}; cuda compute types: {', '.join(types)}"
    except Exception as exc:
        version = str(exc)
    checks.append({
        "name": "ctranslate2_cuda",
        "path": "python:ctranslate2",
        "exists": cuda_exists,
        "required": not optional,
        "optional": optional,
        "status": "ok" if cuda_exists else "optional_missing" if optional else "missing",
        "version": version,
    })
    return checks


def _funasr_runtime_checks(settings: Settings, *, optional: bool = False) -> list[dict[str, Any]]:
    checks = []
    funasr_exists = importlib.util.find_spec("funasr") is not None
    checks.append({
        "name": "funasr",
        "path": "python:funasr",
        "exists": funasr_exists,
        "required": not optional,
        "optional": optional,
        "status": "ok" if funasr_exists else "optional_missing" if optional else "missing",
        "version": _package_version("funasr") if funasr_exists else "",
    })
    torch_exists = importlib.util.find_spec("torch") is not None
    checks.append({
        "name": "torch",
        "path": "python:torch",
        "exists": torch_exists,
        "required": not optional,
        "optional": optional,
        "status": "ok" if torch_exists else "optional_missing" if optional else "missing",
        "version": _package_version("torch") if torch_exists else "",
    })
    if not settings.funasr_device.strip().lower().startswith("cuda"):
        return checks
    cuda_exists = False
    version = ""
    if torch_exists:
        try:
            import torch

            cuda_exists = bool(torch.cuda.is_available())
            version = f"torch {getattr(torch, '__version__', '')}; cuda {getattr(torch.version, 'cuda', '')}"
        except Exception as exc:
            version = str(exc)
    checks.append({
        "name": "torch_cuda",
        "path": "python:torch.cuda",
        "exists": cuda_exists,
        "required": not optional,
        "optional": optional,
        "status": "ok" if cuda_exists else "optional_missing" if optional else "missing",
        "version": version,
    })
    return checks


def _cover_runtime_checks(settings: Settings) -> list[dict[str, Any]]:
    provider = settings.cover_provider.strip().lower()
    if provider not in {"openai", "openai-compatible", "openrouter", "google"}:
        return []
    pillow_exists = importlib.util.find_spec("PIL") is not None
    cover_key_exists = bool(settings.cover_api_key_for_provider())
    key_path = "env:COVER_API_KEY or env:GOOGLE_API_KEY" if provider == "google" else "env:COVER_API_KEY or env:OPENAI_API_KEY"
    return [
        {
            "name": "pillow",
            "path": "python:PIL",
            "exists": pillow_exists,
            "required": False,
            "optional": True,
            "status": "ok" if pillow_exists else "optional_missing",
            "version": _package_version("Pillow") if pillow_exists else "",
        },
        {
            "name": "cover_api_key",
            "path": key_path,
            "exists": cover_key_exists,
            "required": False,
            "optional": True,
            "status": "ok" if cover_key_exists else "optional_missing",
            "version": "",
        },
    ]


def _optional_module_checks(settings: Settings) -> list[dict[str, Any]]:
    llm_model_configured = bool(settings.llm_model.strip())
    llm_provider = settings.llm_provider.strip().lower()
    llm_key_exists = bool(settings.google_api_key.strip()) if llm_provider == "google" else bool(settings.openai_api_key.strip())
    llm_required = llm_model_configured and llm_provider in {"openai", "google"}
    llm_key_name = "llm_google_api_key" if llm_provider == "google" else "llm_openai_api_key"
    llm_key_path = "env:GOOGLE_API_KEY" if llm_provider == "google" else "env:OPENAI_API_KEY"
    separation_engine = settings.audio_separation_engine.strip().lower()
    demucs_required = separation_engine == "demucs"
    demucs_exists = _path_exists(settings.demucs_path, "optional_exe")
    return [
        {
            "name": "llm_model",
            "path": "env:LLM_MODEL",
            "exists": llm_model_configured,
            "required": False,
            "optional": True,
            "status": "ok" if llm_model_configured else "optional_missing",
            "version": settings.llm_model,
        },
        {
            "name": llm_key_name,
            "path": llm_key_path,
            "exists": llm_key_exists,
            "required": llm_required,
            "optional": not llm_required,
            "status": "ok" if llm_key_exists else "missing" if llm_required else "optional_missing",
            "version": "",
        },
        {
            "name": "demucs",
            "path": str(settings.demucs_path),
            "exists": demucs_exists,
            "required": demucs_required,
            "optional": not demucs_required,
            "status": "ok" if demucs_exists else "missing" if demucs_required else "optional_missing",
            "version": _demucs_version(settings.demucs_path) if demucs_exists else "",
        },
    ]


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _settings_payload(settings: Settings) -> dict[str, Any]:
    return {
        "directories": {
            "project_root": str(settings.root),
            "input_recordings": str(settings.input_recordings_dir),
            "job_outputs": str(settings.jobs_dir),
            "logs": str(settings.logs_dir),
        },
        "paths": {
            "ffmpeg": str(settings.ffmpeg_path),
            "ffprobe": str(settings.ffprobe_path),
            "whisper": str(settings.whisper_bin),
            "audiowaveform": str(settings.audiowaveform_path),
            "demucs": str(settings.demucs_path),
        },
        "whisper": {
            "backend": settings.whisper_backend,
            "model": settings.whisper_model,
            "model_fallbacks": ", ".join(settings.whisper_model_fallbacks),
            "language": settings.whisper_language,
            "initial_prompt": settings.whisper_initial_prompt,
            "faster_whisper_device": settings.faster_whisper_device,
            "faster_whisper_compute_type": settings.faster_whisper_compute_type,
            "faster_whisper_batch_size": settings.faster_whisper_batch_size,
            "funasr_model": settings.funasr_model,
            "funasr_vad_model": settings.funasr_vad_model,
            "funasr_punc_model": settings.funasr_punc_model,
            "funasr_device": settings.funasr_device,
            "funasr_hotwords": settings.funasr_hotwords,
            "funasr_batch_size_s": settings.funasr_batch_size_s,
            "funasr_max_segment_ms": settings.funasr_max_segment_ms,
            "word_timestamps": settings.whisper_word_timestamps,
            "vad_filter": settings.whisper_vad_filter,
            "transcribe_audio_filter": settings.transcribe_audio_filter,
        },
        "detection": {
            "silence_threshold_db": settings.silence_threshold_db,
            "silence_min_length": settings.silence_min_length_seconds,
            "silence_min_gap": settings.silence_min_gap_seconds,
            "cut_min_clip_seconds": settings.cut_min_clip_seconds,
            "cut_merge_gap_seconds": settings.cut_merge_gap_seconds,
            "freeze_noise_db": settings.freeze_noise_db,
            "freeze_min_duration": settings.freeze_min_duration_seconds,
            "scene_threshold": settings.scene_threshold,
            "source_integrity_scan_enabled": settings.source_integrity_scan_enabled,
            "source_integrity_scan_timeout_multiplier": settings.source_integrity_scan_timeout_multiplier,
            "source_integrity_scan_max_errors": settings.source_integrity_scan_max_errors,
            "visual_detect_keyframes_only": settings.visual_detect_keyframes_only,
            "visual_detect_fps": settings.visual_detect_fps,
            "visual_detect_width": settings.visual_detect_width,
        },
        "subtitles": {
            "preset": settings.ass_preset,
            "font_name": settings.ass_font_name,
            "font_size": settings.ass_font_size,
            "primary_color": settings.ass_primary_color,
            "outline_color": settings.ass_outline_color,
            "back_color": settings.ass_back_color,
            "outline": settings.ass_outline,
            "shadow": settings.ass_shadow,
            "alignment": settings.ass_alignment,
            "margin_v": settings.ass_margin_v,
            "max_lines": settings.ass_max_lines,
            "vertical_font_size": settings.ass_vertical_font_size,
            "censor_replacement": settings.subtitle_censor_replacement,
            "replacements": [{"source": source, "target": target} for source, target in settings.subtitle_replacements],
            "min_duration_seconds": settings.subtitle_min_duration_seconds,
        },
        "api": {
            "host": settings.api_host,
            "port": settings.api_port,
            "parallel_jobs": settings.api_parallel_jobs,
            "batch_limit": settings.api_batch_limit,
            "recording_upload_max_bytes": settings.recording_upload_max_bytes,
            "allowed_origins": ", ".join(settings.api_allowed_origins),
        },
        "exports": {
            "platforms": ", ".join(settings.export_platforms),
            "render_video_encoder": settings.render_video_encoder,
            "render_output_fps": settings.render_output_fps,
            "render_x264_preset": settings.render_x264_preset,
            "render_x264_crf": settings.render_x264_crf,
            "render_nvenc_preset": settings.render_nvenc_preset,
            "render_nvenc_cq": settings.render_nvenc_cq,
            "render_nvenc_preview_preset": settings.render_nvenc_preview_preset,
            "render_nvenc_preview_cq": settings.render_nvenc_preview_cq,
            "web_preview_enabled": settings.web_preview_enabled,
            "web_preview_max_width": settings.web_preview_max_width,
            "web_preview_max_height": settings.web_preview_max_height,
            "web_preview_fps": settings.web_preview_fps,
            "web_preview_video_bitrate": settings.web_preview_video_bitrate,
            "bgm_path": str(settings.bgm_path) if settings.bgm_path else "",
            "bgm_volume": settings.bgm_volume,
            "source_audio_volume": settings.source_audio_volume,
            "webhook_url": settings.webhook_url,
        },
        "optional_modules": {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "google_base_url": settings.google_base_url,
            "google_api_key_configured": bool(settings.google_api_key),
            "native_waveform_enabled": settings.native_waveform_enabled,
            "native_cuts_enabled": settings.native_cuts_enabled,
            "high_quality_audio_enabled": settings.high_quality_audio_enabled,
            "llm_translation_batch_size": settings.llm_translation_batch_size,
            "llm_translation_batch_chars": settings.llm_translation_batch_chars,
            "audio_separation_engine": settings.audio_separation_engine,
            "demucs_model": settings.demucs_model,
            "demucs_device": settings.demucs_device,
            "audio_separation_timeout_seconds": settings.audio_separation_timeout_seconds,
            "publish_enabled": settings.publish_enabled,
            "publish_providers": ", ".join(settings.publish_providers),
        },
        "crop": {
            "vertical_mode": settings.vertical_mode,
            "anchor_x": settings.crop_anchor_x,
            "anchor_y": settings.crop_anchor_y,
        },
        "covers": {
            "provider": settings.cover_provider,
            "base_url": settings.cover_base_url,
            "model": settings.cover_model,
            "count": settings.cover_count,
            "aspects": ", ".join(settings.cover_aspects),
            "quality": settings.cover_quality,
            "output_format": settings.cover_output_format,
            "title_font": settings.cover_title_font,
            "cover_api_key_configured": bool(settings.cover_api_key),
            "openai_api_key_configured": bool(settings.openai_api_key),
            "http_referer": settings.cover_http_referer,
            "app_title": settings.cover_app_title,
            "modalities": ", ".join(settings.cover_modalities),
        },
    }


def _path_exists(path: Path, kind: str) -> bool:
    if path.exists():
        return True
    if kind in {"exe", "optional_exe"} and not path.is_absolute():
        return shutil.which(str(path)) is not None
    return False


def _first_version_line(path: Path) -> str:
    try:
        executable = shutil.which(str(path)) or str(path)
        result = subprocess.run([executable, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""


def _demucs_version(path: Path) -> str:
    try:
        executable = shutil.which(str(path)) or str(path)
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""


def _ffmpeg_has_encoder(path: Path, encoder: str) -> bool:
    try:
        executable = shutil.which(str(path)) or str(path)
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and encoder in result.stdout


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

    try:
        logger.info("Processing %s", job.source_path)
        audio_path = job.job_dir / "audio.wav"
        audio_hq_path = _high_quality_audio_path(settings, job, plan_uvr_enabled=plan_uvr_enabled)
        context: dict[str, Any] = {
            "audio_path": audio_path,
            "audio_hq_path": audio_hq_path,
            "manifest": None,
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
            extract_audio_outputs(
                settings,
                job.source_path,
                stage_context["audio_path"],
                stage_context["audio_hq_path"],
                integrity_output_path=job.job_dir / "corrupt.json",
                duration=manifest["duration_seconds"],
                force=force,
            )
            stage_context["media_outputs_prepared"] = True

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
                job.stage_estimate_seconds = round(estimated_seconds, 2)
                waiting_callback, acquired_callback = job_gpu_status_callbacks(job, "transcription")

                def on_resource_wait() -> None:
                    resource_waiting.set()
                    waiting_callback()

                def on_resource_acquired() -> None:
                    resource_waiting.clear()
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
                    )
                finally:
                    stop_heartbeat.set()
                    heartbeat_thread.join(timeout=1)

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
            render: Callable[[Callable[[float], None], Callable[[], None], Callable[[], None]], None],
        ) -> None:
            stop_heartbeat = threading.Event()
            resource_waiting = threading.Event()
            started_at = time.monotonic()
            state = {"percent": 0.0}
            label = stage_name.replace("_", " ")
            waiting_callback, acquired_callback = job_gpu_status_callbacks(job, label)

            def on_resource_wait() -> None:
                resource_waiting.set()
                waiting_callback()

            def on_resource_acquired() -> None:
                resource_waiting.clear()
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

        def render_review_stage(stage_context: dict[str, Any]) -> None:
            run_render_stage(
                "render_review",
                lambda callback, on_wait, on_acquired: render_review_video(
                    settings,
                    job.job_dir,
                    job.source_path,
                    force=force,
                    progress_callback=callback,
                    resource_wait_callback=on_wait,
                    resource_acquired_callback=on_acquired,
                ),
            )

        def render_final_stage(stage_context: dict[str, Any]) -> None:
            run_render_stage(
                "render_final",
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
                ),
            )

        stage_selection = expand_stage_selection(selected_stages)

        def enabled(stage_name: str, default: bool) -> bool:
            return default and (stage_selection is None or stage_name in stage_selection)

        stages = [
            PipelineStage("probe", "probing", enabled("probe", True), probe_stage),
            PipelineStage("detect_corruption", "detecting_corruption", enabled("detect_corruption", settings.source_integrity_scan_enabled), corruption_stage),
            PipelineStage("extract_audio", "extracting_audio", enabled("extract_audio", True), extract_audio_stage),
            PipelineStage("transcribe", "transcribing", enabled("transcribe", True), transcribe_stage),
            PipelineStage("detect_silence", "detecting_silence", enabled("detect_silence", detect_silence_enabled), silence_stage),
            PipelineStage("detect_freeze", "detecting_freeze", enabled("detect_freeze", detect_freeze_enabled), freeze_stage),
            PipelineStage("detect_scenes", "detecting_scenes", enabled("detect_scenes", detect_scenes_enabled), scenes_stage),
            PipelineStage("plan_cuts", "planning_cuts", enabled("plan_cuts", True), cuts_stage),
            PipelineStage("plan_crop", "planning_crop", enabled("plan_crop", plan_crop_enabled or vertical_enabled), crop_stage),
            PipelineStage("style_subtitles", "styling_subtitles", enabled("style_subtitles", (not skip_transcribe) or burn_subtitles_enabled), subtitles_stage),
            PipelineStage("plan_uvr", "planning_uvr", enabled("plan_uvr", plan_uvr_enabled), uvr_stage),
            PipelineStage("plan_render", "planning_render", enabled("plan_render", True), render_preview_stage),
            PipelineStage("render_review", "rendering_review", enabled("render_review", render_review_enabled), render_review_stage),
            PipelineStage("render_final", "rendering_final", enabled("render_final", render_final_enabled), render_final_stage),
        ]
        if control_callback is None:
            run_pipeline(progress, job, stages, context)
        else:
            run_pipeline(progress, job, stages, context, control_callback=control_callback)
        job.set_status("done" if render_final_enabled else "needs_review")
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
            job.set_status("queued")
        else:
            job.fail("Canceled by user")
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


PIPELINE_STAGE_DEPENDENCIES: dict[str, set[str]] = {
    "probe": set(),
    "detect_corruption": {"probe"},
    "extract_audio": {"probe"},
    "transcribe": {"probe", "extract_audio"},
    "detect_silence": {"probe", "extract_audio"},
    "detect_freeze": {"probe"},
    "detect_scenes": {"probe"},
    "plan_cuts": {"probe", "extract_audio", "transcribe"},
    "plan_crop": {"probe"},
    "style_subtitles": {"probe", "extract_audio", "transcribe", "plan_cuts"},
    "plan_uvr": {"probe", "extract_audio"},
    "plan_render": {"probe", "extract_audio", "transcribe", "plan_cuts", "style_subtitles"},
    "render_review": {"probe", "extract_audio", "transcribe", "plan_cuts", "style_subtitles", "plan_render"},
    "render_final": {"probe", "extract_audio", "transcribe", "plan_cuts", "plan_crop", "style_subtitles", "plan_render"},
}


def expand_stage_selection(selected_stages: list[str] | None) -> set[str] | None:
    if not selected_stages:
        return None
    requested = {str(stage).strip() for stage in selected_stages if str(stage).strip()}
    unknown = sorted(requested - PIPELINE_STAGE_DEPENDENCIES.keys())
    if unknown:
        raise ValueError(f"unknown pipeline stage: {unknown[0]}")
    expanded = set(requested)
    pending = list(requested)
    while pending:
        stage = pending.pop()
        for dependency in PIPELINE_STAGE_DEPENDENCIES[stage]:
            if dependency not in expanded:
                expanded.add(dependency)
                pending.append(dependency)
    return expanded


def run_pipeline(
    progress: ProgressReporter,
    job: Job,
    stages: list[PipelineStage],
    context: dict[str, Any],
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    total_stages = len(stages)
    timings: list[dict[str, Any]] = []
    pipeline_started_at = datetime.now().isoformat(timespec="seconds")
    _write_stage_timings(job, timings, status="running", total_stages=total_stages, started_at=pipeline_started_at)
    progress.emit(
        "pipeline:start",
        job_dir=str(job.job_dir),
        source_path=str(job.source_path),
        total_stages=total_stages,
    )
    for index, stage in enumerate(stages, start=1):
        action = control_callback() if control_callback else None
        if action in {"paused", "canceled"}:
            raise QueueControlRequested(action)
        stage_payload = {
            "job_dir": str(job.job_dir),
            "source_path": str(job.source_path),
            "stage": stage.name,
            "stage_number": index,
            "total_stages": total_stages,
        }
        if not stage.enabled:
            progress.emit("stage:skip", **stage_payload, reason="disabled")
            timings.append({
                "stage": stage.name,
                "status": "skipped",
                "stage_number": index,
                "total_stages": total_stages,
                "duration_seconds": 0.0,
                "reason": "disabled",
            })
            _write_stage_timings(job, timings, status="running", total_stages=total_stages, started_at=pipeline_started_at)
            continue
        job.start_stage(stage.status, stage.name)
        started_at = time.monotonic()
        progress.emit("stage:start", **stage_payload, status=job.status)
        try:
            stage.run(context)
        except Exception as exc:
            progress.emit(
                "stage:error",
                **stage_payload,
                status=job.status,
                duration_seconds=round(time.monotonic() - started_at, 3),
                error=str(exc),
            )
            timings.append({
                "stage": stage.name,
                "status": "failed",
                "stage_number": index,
                "total_stages": total_stages,
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "error": str(exc),
            })
            _write_stage_timings(job, timings, status="failed", total_stages=total_stages, started_at=pipeline_started_at)
            raise
        job.complete_stage()
        timings.append({
            "stage": stage.name,
            "status": "complete",
            "stage_number": index,
            "total_stages": total_stages,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        })
        _write_stage_timings(job, timings, status="running", total_stages=total_stages, started_at=pipeline_started_at)
        progress.emit(
            "stage:complete",
            **stage_payload,
            status=job.status,
            duration_seconds=round(time.monotonic() - started_at, 3),
        )
    _write_stage_timings(job, timings, status="complete", total_stages=total_stages, started_at=pipeline_started_at)


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
