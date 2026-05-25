from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic
from .plans import PLATFORM_PRESETS


def generate_platform_segments(
    settings: Settings,
    job_dir: Path,
    *,
    platforms: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    source = _preferred_video(job_dir)
    if source is None:
        raise RuntimeError("final.mp4 or review.mp4 is required before generating segments")
    selected = _normalize_platforms(platforms or list(settings.export_platforms))
    duration = _probe_duration(settings, source)
    if duration <= 0:
        raise RuntimeError("could not determine rendered video duration")

    boundaries = _clip_boundaries(job_dir, duration)
    output_dir = job_dir / "segments"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "status": "ready",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_video": str(source),
        "duration_seconds": round(duration, 3),
        "platforms": [],
    }
    for platform in selected:
        preset = PLATFORM_PRESETS.get(platform, {"resolution": "source", "max_seconds": None})
        max_seconds = _max_seconds(preset.get("max_seconds"), duration)
        ranges = _segment_ranges(duration, max_seconds, boundaries)
        segments = []
        for index, (start, end) in enumerate(ranges, start=1):
            output = output_dir / f"{platform}_part_{index:02d}.mp4"
            if force or not output.exists() or output.stat().st_size == 0:
                _copy_segment(settings, source, output, start, end - start)
            segments.append({
                "index": index,
                "file": str(output.relative_to(job_dir)),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })
        manifest["platforms"].append({
            "name": platform,
            "resolution": preset.get("resolution", "source"),
            "max_seconds": max_seconds,
            "segment_count": len(segments),
            "segments": segments,
        })
    write_json_atomic(job_dir / "segments_manifest.json", manifest)
    return manifest


def _preferred_video(job_dir: Path) -> Path | None:
    for name in ["final.mp4", "review.mp4"]:
        path = job_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _normalize_platforms(platforms: list[str]) -> list[str]:
    values = []
    for item in platforms:
        name = str(item).strip().lower()
        if name and name not in PLATFORM_PRESETS:
            raise RuntimeError(f"unsupported platform: {name}")
        if name and name not in values:
            values.append(name)
    return values or ["douyin"]


def _max_seconds(value: Any, duration: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return duration
    return seconds if seconds > 0 else duration


def _clip_boundaries(job_dir: Path, duration: float) -> list[float]:
    preview = read_json_file(job_dir / "final_render_preview.json") or read_json_file(job_dir / "render_preview.json") or {}
    boundaries: list[float] = []
    cursor = 0.0
    for clip in preview.get("clips", []):
        if not isinstance(clip, dict):
            continue
        try:
            clip_duration = float(clip.get("duration") or (float(clip.get("end")) - float(clip.get("start"))))
        except (TypeError, ValueError):
            continue
        if clip_duration <= 0:
            continue
        cursor += clip_duration
        if 0.5 < cursor < duration - 0.5:
            boundaries.append(round(cursor, 3))
    return sorted(set(boundaries))


def _segment_ranges(duration: float, max_seconds: float, boundaries: list[float]) -> list[tuple[float, float]]:
    if duration <= max_seconds + 0.5:
        return [(0.0, duration)]
    ranges = []
    start = 0.0
    while start < duration - 0.5:
        target = min(duration, start + max_seconds)
        if target >= duration - 0.5:
            end = duration
        else:
            min_end = start + min(max_seconds * 0.35, 60.0)
            candidates = [value for value in boundaries if min_end <= value <= target]
            end = candidates[-1] if candidates else target
        if end <= start + 0.5:
            end = min(duration, start + max_seconds)
        ranges.append((round(start, 3), round(end, 3)))
        start = end
    return ranges


def _probe_duration(settings: Settings, source: Path) -> float:
    command = [
        str(settings.ffprobe_path),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe returned an invalid duration") from exc


def _copy_segment(settings: Settings, source: Path, output: Path, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg segment export failed: {result.stderr.strip()}")
