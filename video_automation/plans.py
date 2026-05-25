from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


PLATFORM_PRESETS: dict[str, dict[str, Any]] = {
    "douyin": {"resolution": "1080x1920", "max_seconds": 180, "caption_safe_margin": "bottom_220px"},
    "bilibili": {"resolution": "1920x1080", "max_seconds": 600, "caption_safe_margin": "bottom_120px"},
    "youtube_shorts": {"resolution": "1080x1920", "max_seconds": 60, "caption_safe_margin": "bottom_220px"},
}


def generate_platform_export_plan(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "platform_export_plan.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    media = _preferred_video(job_dir)
    payload = {
        "status": "ready" if media else "waiting_for_render",
        "source_video": str(media) if media else "",
        "platforms": [
            {"name": name, **PLATFORM_PRESETS.get(name, {"resolution": "source", "max_seconds": None})}
            for name in settings.export_platforms
        ],
        "notes": [
            "This is an export contract. Platform-specific rendering can consume this plan later.",
            "Use final.mp4 when available; otherwise review.mp4 is the preview source.",
        ],
    }
    write_json_atomic(output_path, payload)
    return payload


def generate_bgm_mix_plan(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "bgm_mix_plan.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    media = _preferred_video(job_dir)
    payload = {
        "status": "ready" if settings.bgm_path and media else "not_configured",
        "source_video": str(media) if media else "",
        "bgm_path": str(settings.bgm_path) if settings.bgm_path else "",
        "output_path": str(job_dir / "bgm_mix.mp4"),
        "default_mix": {
            "video_audio_volume": settings.source_audio_volume,
            "bgm_volume": settings.bgm_volume,
            "ducking": "none",
        },
        "notes": [
            "Set BGM_PATH in .env to enable automatic final-render BGM mixing.",
            "When BGM_PATH exists, render_final uses the BGM as a looped second audio input and mixes it under source audio.",
        ],
    }
    write_json_atomic(output_path, payload)
    return payload


def generate_webhook_plan(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "webhook_plan.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    payload = {
        "status": "ready" if settings.webhook_url else "not_configured",
        "url": settings.webhook_url,
        "events": ["job.done", "job.failed", "job.needs_review"],
        "payload_fields": ["job_dir", "source_path", "status", "files"],
        "notes": ["Set WEBHOOK_URL in .env to enable outbound notification in a later execution step."],
    }
    write_json_atomic(output_path, payload)
    return payload


def _preferred_video(job_dir: Path) -> Path | None:
    for name in ["final.mp4", "review.mp4"]:
        path = job_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None
