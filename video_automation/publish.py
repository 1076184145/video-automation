from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic
from .plans import PLATFORM_PRESETS


def generate_publish_package(
    settings: Settings,
    job_dir: Path,
    *,
    platforms: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    output_path = job_dir / "publish_package.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    video = _preferred_video(job_dir)
    if video is None:
        raise RuntimeError("final.mp4 or review.mp4 is required before generating a publish package")
    metadata = read_json_file(job_dir / "metadata.json") or {}
    cover_manifest = read_json_file(job_dir / "cover_manifest.json") or {}
    segments = read_json_file(job_dir / "segments_manifest.json") or {}
    selected_platforms = _normalize_platforms(platforms or list(settings.export_platforms))
    package = {
        "status": "ready",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "publish_enabled": settings.publish_enabled,
        "configured_providers": list(settings.publish_providers),
        "source_video": _file_entry(job_dir, video),
        "covers": _selected_covers(job_dir, cover_manifest),
        "metadata": metadata,
        "segments_manifest": "segments_manifest.json" if segments else "",
        "platforms": [_platform_entry(job_dir, name, video, segments) for name in selected_platforms],
        "notes": [
            "This package is for manual upload. It does not call platform publishing APIs.",
            "Enable platform connectors only after OAuth/API credentials are configured.",
        ],
    }
    write_json_atomic(output_path, package)
    return package


def _preferred_video(job_dir: Path) -> Path | None:
    for name in ["final.mp4", "review.mp4"]:
        path = job_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _normalize_platforms(platforms: list[str]) -> list[str]:
    values = []
    for item in platforms:
        value = str(item).strip().lower()
        if value and value not in PLATFORM_PRESETS:
            raise RuntimeError(f"unsupported platform: {value}")
        if value and value not in values:
            values.append(value)
    return values or ["douyin"]


def _platform_entry(job_dir: Path, platform: str, video: Path, segments: dict[str, Any]) -> dict[str, Any]:
    preset = PLATFORM_PRESETS.get(platform, {"resolution": "source", "max_seconds": None})
    platform_segments = []
    for item in segments.get("platforms", []):
        if isinstance(item, dict) and item.get("name") == platform:
            platform_segments = item.get("segments", [])
            break
    return {
        "name": platform,
        "preset": preset,
        "video": _file_entry(job_dir, video),
        "segments": platform_segments if isinstance(platform_segments, list) else [],
        "checks": _platform_checks(video, preset, platform_segments),
    }


def _platform_checks(video: Path, preset: dict[str, Any], platform_segments: Any) -> list[dict[str, Any]]:
    checks = []
    max_seconds = preset.get("max_seconds")
    if max_seconds and isinstance(platform_segments, list) and platform_segments:
        too_long = [item for item in platform_segments if float(item.get("duration") or 0) > float(max_seconds)]
        checks.append({"name": "segment_duration", "ok": not too_long, "message": f"max {max_seconds}s"})
    checks.append({"name": "video_exists", "ok": video.exists(), "message": video.name})
    checks.append({"name": "manual_review", "ok": True, "message": "Review platform policy before upload."})
    return checks


def _selected_covers(job_dir: Path, cover_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    covers = []
    for aspect, filename in (cover_manifest.get("selected") or {}).items():
        if not filename:
            continue
        path = _safe_job_file(job_dir, str(filename))
        if path.exists():
            covers.append({"aspect": aspect, **_file_entry(job_dir, path)})
    for fallback in ["cover_vertical.jpg", "cover_landscape.jpg"]:
        path = job_dir / fallback
        if path.exists() and all(item.get("name") != fallback for item in covers):
            covers.append({"aspect": "9:16" if "vertical" in fallback else "16:9", **_file_entry(job_dir, path)})
    return covers


def _safe_job_file(job_dir: Path, filename: str) -> Path:
    root = job_dir.resolve()
    path = (root / filename).resolve()
    path.relative_to(root)
    return path


def _file_entry(job_dir: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "relative_path": str(path.relative_to(job_dir)) if _inside(path, job_dir) else path.name,
        "size_bytes": stat.st_size,
        "modified_at": int(stat.st_mtime),
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
