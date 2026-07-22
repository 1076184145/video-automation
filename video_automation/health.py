from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import time
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

from .api_security import api_binding_status
from .api_settings import legacy_secret_keys
from .config import Settings
from .render import probe_nvenc_encoder


def _transcription_backend_label(backend: str) -> str:
    normalized = str(backend or "").strip().lower()
    if normalized in {"funasr", "funasr-whisper", "funasr-faster-whisper"}:
        return "FunASR"
    if normalized == "faster-whisper":
        return "Faster-Whisper"
    return "Whisper CLI"


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
_health_cache_key: tuple[Any, ...] | None = None
_HEALTH_CACHE_TTL = 30.0  # seconds


def health_payload(settings: Settings) -> dict[str, Any]:
    global _health_cache, _health_cache_key, _health_cache_time  # noqa: PLW0603
    now = time.monotonic()
    cache_key = _health_settings_cache_key(settings)
    if (
        _health_cache is not None
        and cache_key == _health_cache_key
        and (now - _health_cache_time) < _HEALTH_CACHE_TTL
    ):
        return deepcopy(_health_cache)
    result = _build_health_payload(settings)
    _health_cache = deepcopy(result)
    _health_cache_key = cache_key
    _health_cache_time = now
    return result


def clear_health_cache() -> None:
    global _health_cache, _health_cache_key, _health_cache_time  # noqa: PLW0603
    _health_cache = None
    _health_cache_key = None
    _health_cache_time = 0.0


def _health_settings_cache_key(settings: Settings) -> tuple[Any, ...]:
    secret_names = {"cover_api_key", "openai_api_key", "google_api_key"}
    values: list[Any] = []
    for field in fields(settings):
        value = getattr(settings, field.name)
        if field.name in secret_names:
            values.append((field.name, bool(value)))
        else:
            values.append((field.name, _cache_value(value)))
    return tuple(values)


def _cache_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _cache_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_cache_value(item) for item in value)
    return value


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
    binding = api_binding_status(settings.api_host, settings.api_allow_remote)
    ok = ok and bool(binding["allowed"])
    security = {
        **binding,
        "legacy_secret_keys": sorted(legacy_secret_keys(settings.root)),
    }
    warnings: list[dict[str, Any]] = []
    if binding["warning_code"]:
        warnings.append({
            "code": binding["warning_code"],
            "severity": "error" if not binding["allowed"] else "warning",
            "message": binding["message"],
        })
    if security["legacy_secret_keys"]:
        warnings.append({
            "code": "plaintext_api_keys",
            "severity": "warning",
            "message": "AI provider keys are still stored in .env and can be migrated to the OS credential store.",
        })
    storage = _storage_health(settings)
    if storage.get("low_space"):
        warnings.append({
            "code": "low_disk_space",
            "severity": "warning",
            "message": "Free disk space is below the configured processing reserve.",
        })
    return {
        "ok": ok,
        "checks": results,
        "settings": _settings_payload(settings),
        "security": security,
        "storage": storage,
        "warnings": warnings,
    }


def _storage_health(settings: Settings) -> dict[str, Any]:
    target = settings.jobs_dir if settings.jobs_dir.exists() else settings.root
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {
            "path": str(target),
            "available": False,
            "error": str(exc),
            "low_space": False,
        }
    reserve = max(0, int(settings.min_free_disk_bytes))
    return {
        "path": str(target),
        "available": True,
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "min_free_bytes": reserve,
        "low_space": int(usage.free) < reserve,
    }


def _render_runtime_checks(settings: Settings) -> list[dict[str, Any]]:
    encoder = settings.render_video_encoder.strip().lower()
    if encoder not in {"h264_nvenc", "nvenc"}:
        return []
    compiled = _ffmpeg_has_encoder(settings.ffmpeg_path, "h264_nvenc")
    probe = probe_nvenc_encoder(settings.ffmpeg_path) if compiled else {
        "available": False,
        "detail": "FFmpeg was not built with h264_nvenc",
    }
    exists = bool(probe["available"])
    return [{
        "name": "h264_nvenc",
        "path": str(settings.ffmpeg_path),
        "exists": exists,
        "required": True,
        "optional": False,
        "status": "ok" if exists else "missing",
        "version": "NVIDIA NVENC H.264 encoder" if exists else "",
        "detail": str(probe.get("detail") or ""),
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
            "allow_remote": settings.api_allow_remote,
            "port": settings.api_port,
            "parallel_jobs": settings.api_parallel_jobs,
            "batch_limit": settings.api_batch_limit,
            "recording_upload_max_bytes": settings.recording_upload_max_bytes,
            "allowed_origins": ", ".join(settings.api_allowed_origins),
            "min_free_disk_bytes": settings.min_free_disk_bytes,
            "job_disk_multiplier": settings.job_disk_multiplier,
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
