from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Settings
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
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
    manifest = read_json_file(job_dir / "manifest.json") or {}
    selected_platforms = _normalize_platforms(platforms or list(settings.export_platforms))
    covers = _selected_covers(job_dir, cover_manifest)
    platform_entries = [
        _platform_entry(job_dir, name, video, segments, metadata, manifest)
        for name in selected_platforms
    ]
    extension_manifest = _write_extension_manifest(job_dir, video, platform_entries, covers)
    package = {
        "status": "ready",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "publish_enabled": settings.publish_enabled,
        "configured_providers": list(settings.publish_providers),
        "source_video": _file_entry(job_dir, video),
        "covers": covers,
        "metadata": metadata,
        "segments_manifest": "segments_manifest.json" if segments else "",
        "platforms": platform_entries,
        "publish_extension": extension_manifest,
        "publish_center": {
            "mode": "manual_handoff_plus_extension_manifest",
            "connectors": ["douyin", "bilibili"],
            "note": "Use manual text files now, or let a trusted browser extension read publish_extension_manifest.json.",
        },
        "notes": [
            "This package is for manual upload. It does not call platform publishing APIs.",
            "publish_extension_manifest.json is a local handoff contract for a future browser extension; it does not contain credentials.",
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


def _platform_entry(
    job_dir: Path,
    platform: str,
    video: Path,
    segments: dict[str, Any],
    metadata: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    preset = PLATFORM_PRESETS.get(platform, {"resolution": "source", "max_seconds": None})
    platform_segments = []
    for item in segments.get("platforms", []):
        if isinstance(item, dict) and item.get("name") == platform:
            platform_segments = item.get("segments", [])
            break
    handoff = _write_platform_handoff(job_dir, platform, video, metadata, preset)
    return {
        "name": platform,
        "preset": preset,
        "video": _file_entry(job_dir, video),
        "segments": platform_segments if isinstance(platform_segments, list) else [],
        "metadata_preview": _metadata_preview(metadata, platform),
        "handoff": handoff,
        "checks": _platform_checks(job_dir, video, preset, platform_segments, metadata, manifest),
    }


def _platform_checks(
    job_dir: Path,
    video: Path,
    preset: dict[str, Any],
    platform_segments: Any,
    metadata: dict[str, Any],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    checks = []
    max_seconds = preset.get("max_seconds")
    duration = _duration_seconds(manifest)
    if max_seconds and duration:
        checks.append({
            "name": "duration_limit",
            "ok": duration <= float(max_seconds),
            "message": f"{duration:.1f}s / max {max_seconds}s",
        })
    if max_seconds and isinstance(platform_segments, list) and platform_segments:
        too_long = [item for item in platform_segments if float(item.get("duration") or 0) > float(max_seconds)]
        checks.append({"name": "segment_duration", "ok": not too_long, "message": f"max {max_seconds}s"})
    checks.append({"name": "video_exists", "ok": video.exists(), "message": video.name})
    checks.append({"name": "metadata_ready", "ok": bool(_first(metadata.get("titles"))), "message": "title available"})
    checks.append({"name": "cover_ready", "ok": _has_any_cover(job_dir), "message": "cover_vertical/landscape or selected cover"})
    checks.append({"name": "resolution_advice", "ok": True, "message": f"target {preset.get('resolution', 'source')}"})
    checks.append({"name": "manual_review", "ok": True, "message": "Review platform policy and copyright before upload."})
    return checks


def _write_platform_handoff(
    job_dir: Path,
    platform: str,
    video: Path,
    metadata: dict[str, Any],
    preset: dict[str, Any],
) -> dict[str, Any]:
    output_dir = job_dir / "publish_packages" / platform
    preview = _metadata_preview(metadata, platform)
    files = {
        "title": output_dir / "title.txt",
        "description": output_dir / "description.txt",
        "tags": output_dir / "tags.txt",
        "hashtags": output_dir / "hashtags.txt",
        "video_path": output_dir / "video_path.txt",
        "checklist": output_dir / "upload_checklist.txt",
        "readme": output_dir / "README_UPLOAD.txt",
    }
    write_text_atomic(files["title"], preview["title"])
    write_text_atomic(files["description"], preview["description"])
    write_text_atomic(files["tags"], "\n".join(preview["tags"]))
    write_text_atomic(files["hashtags"], " ".join(preview["hashtags"]))
    write_text_atomic(files["video_path"], str(video))
    write_text_atomic(files["checklist"], _checklist_text(platform, preset, preview))
    write_text_atomic(files["readme"], _readme_text(platform, video, preset))
    write_json_atomic(output_dir / "platform_metadata.json", {
        "platform": platform,
        "metadata": preview,
        "target": preset,
        "video": _file_entry(job_dir, video),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    result_files = [_file_entry(job_dir, path) for path in [*files.values(), output_dir / "platform_metadata.json"]]
    return {
        "directory": str(output_dir.relative_to(job_dir)).replace("\\", "/"),
        "files": result_files,
        "mode": "manual_upload",
    }


def _write_extension_manifest(
    job_dir: Path,
    video: Path,
    platform_entries: list[dict[str, Any]],
    covers: list[dict[str, Any]],
) -> dict[str, Any]:
    output_path = job_dir / "publish_extension_manifest.json"
    manifest = {
        "protocol_version": 1,
        "status": "ready",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "job": job_dir.name,
        "api": {
            "package_url": _job_file_url(job_dir, "publish_package.json"),
            "manifest_url": _job_file_url(job_dir, "publish_extension_manifest.json"),
        },
        "video": _file_entry(job_dir, video),
        "covers": covers,
        "platforms": [_extension_platform_entry(job_dir, entry) for entry in platform_entries],
        "notes": [
            "This manifest is intended for a browser extension that the user explicitly installs.",
            "The extension should ask for confirmation before filling or submitting platform upload forms.",
            "No cookies, passwords, OAuth tokens, or platform credentials are stored here.",
        ],
    }
    write_json_atomic(output_path, manifest)
    return _file_entry(job_dir, output_path) | {
        "protocol_version": 1,
        "api_url": f"/jobs/{job_dir.name}/files/publish_extension_manifest.json",
    }


def _extension_platform_entry(job_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name") or "")
    preview = entry.get("metadata_preview") if isinstance(entry.get("metadata_preview"), dict) else {}
    handoff = entry.get("handoff") if isinstance(entry.get("handoff"), dict) else {}
    return {
        "platform": name,
        "label": _platform_label(name),
        "uploader_url": _platform_upload_url(name),
        "mode": "form_fill_handoff",
        "fields": {
            "title": str(preview.get("title") or ""),
            "description": str(preview.get("description") or ""),
            "tags": _string_items(preview.get("tags")),
            "hashtags": _string_items(preview.get("hashtags")),
        },
        "handoff_directory": handoff.get("directory", ""),
        "handoff_files": handoff.get("files", []),
        "package_metadata_url": _job_file_url(job_dir, f"publish_packages/{name}/platform_metadata.json"),
        "checks": entry.get("checks", []),
    }


def _platform_upload_url(platform: str) -> str:
    return {
        "douyin": "https://creator.douyin.com/creator-micro/content/upload",
        "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
        "youtube_shorts": "https://studio.youtube.com/",
    }.get(platform, "")


def _job_file_url(job_dir: Path, relative_path: str) -> str:
    return f"/jobs/{quote(job_dir.name, safe='')}/files/{quote(relative_path, safe='/')}"


def _metadata_preview(metadata: dict[str, Any], platform: str) -> dict[str, Any]:
    title = _truncate(_first(metadata.get("titles")) or _first(metadata.get("cover_titles")) or "", 55 if platform == "douyin" else 80)
    description = _first(metadata.get("descriptions")) or ""
    tags = _string_items(metadata.get("tags"))[:12]
    hashtags = [_hashtag(item) for item in _string_items(metadata.get("hashtags"))[:12]]
    platform_notes = _string_items(metadata.get("platform_notes"))[:8]
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "platform_notes": platform_notes,
    }


def _checklist_text(platform: str, preset: dict[str, Any], preview: dict[str, Any]) -> str:
    label = _platform_label(platform)
    lines = [
        f"{label} upload checklist",
        "",
        f"- Target resolution: {preset.get('resolution', 'source')}",
        f"- Max duration reference: {preset.get('max_seconds', 'platform default')}s",
        "- Confirm title, description, tags, and cover before publishing.",
        "- Confirm music, source footage, portrait rights, and platform policy compliance.",
        "- This package does not log in or upload automatically.",
        "",
        "Suggested title:",
        preview["title"] or "(fill manually)",
        "",
        "Suggested hashtags:",
        " ".join(preview["hashtags"]) or "(fill manually)",
    ]
    return "\n".join(lines)


def _readme_text(platform: str, video: Path, preset: dict[str, Any]) -> str:
    label = _platform_label(platform)
    return "\n".join([
        f"{label} manual upload package",
        "",
        "Files in this folder:",
        "- title.txt: suggested title",
        "- description.txt: suggested description",
        "- tags.txt: one tag per line",
        "- hashtags.txt: hashtag string",
        "- video_path.txt: local path to the rendered video",
        "- upload_checklist.txt: platform handoff checklist",
        "",
        f"Video: {video}",
        f"Target: {preset.get('resolution', 'source')}",
        "",
        "Open the platform uploader, select the video, then copy the text files into the corresponding fields.",
    ])


def _duration_seconds(manifest: dict[str, Any]) -> float:
    try:
        return float(manifest.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0


def _has_any_cover(job_dir: Path) -> bool:
    return any((job_dir / name).exists() for name in ["cover_vertical.jpg", "cover_landscape.jpg"])


def _first(value: Any) -> str:
    items = _string_items(value)
    return items[0] if items else ""


def _string_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _hashtag(value: str) -> str:
    item = value.strip()
    if not item:
        return ""
    return item if item.startswith("#") else f"#{item}"


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _platform_label(platform: str) -> str:
    return {"douyin": "Douyin", "bilibili": "Bilibili", "youtube_shorts": "YouTube Shorts"}.get(platform, platform)


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
        "relative_path": str(path.relative_to(job_dir)).replace("\\", "/") if _inside(path, job_dir) else path.name,
        "size_bytes": stat.st_size,
        "modified_at": int(stat.st_mtime),
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
