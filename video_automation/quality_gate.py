from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _item(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _subtitle_line_counts(path: Path) -> list[int]:
    """Return the rendered line count for every Dialogue event in an ASS file."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []

    counts: list[int] = []
    for raw_line in content.splitlines():
        if not raw_line.lstrip().lower().startswith("dialogue:"):
            continue
        fields = raw_line.split(",", 9)
        if len(fields) < 10:
            continue
        # ASS uses \N for a hard line break and \n for a soft line break.
        counts.append(len(re.split(r"\\[Nn]", fields[9])))
    return counts


def _rendered_subtitle_lines(root: Path) -> tuple[Path | None, list[int]]:
    """Read the subtitle track used by the edited render, falling back to source timing."""
    for name in ("subtitles_clipped.ass", "subtitles.ass"):
        path = root / name
        if not path.is_file():
            continue
        counts = _subtitle_line_counts(path)
        if counts:
            return path, counts
    return None, []


def evaluate_quality_gate(job_dir: Path | str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(job_dir)
    policy = policy if isinstance(policy, dict) else {}
    manifest = _read_json(root / "manifest.json")
    transcript = _read_json(root / "transcript.json")
    blocking: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    passed: list[dict[str, Any]] = []

    output = root / "final.mp4"
    if not output.is_file():
        output = root / "review.mp4"
    if not output.is_file() or output.stat().st_size < 1:
        blocking.append(_item("render_missing", "A final or review video is required."))
    else:
        passed.append(_item("render_ready", "Rendered video is available.", path=str(output)))

    duration = float(manifest.get("duration_seconds") or 0)
    minimum = float(policy.get("duration_min_seconds") or 0)
    maximum = float(policy.get("duration_max_seconds") or 0)
    if duration <= 0:
        blocking.append(_item("duration_invalid", "Video duration could not be verified."))
    elif (minimum and duration < minimum) or (maximum and duration > maximum):
        blocking.append(_item("duration_limit", "Video duration is outside the configured platform range.", duration_seconds=duration))
    else:
        passed.append(_item("duration_ok", "Video duration is within the configured range.", duration_seconds=duration))

    expected_aspect = str(policy.get("aspect") or "").strip()
    width = int(manifest.get("width") or 0)
    height = int(manifest.get("height") or 0)
    if expected_aspect:
        try:
            expected_width, expected_height = [float(value) for value in expected_aspect.split(":", 1)]
            expected_ratio = expected_width / expected_height
        except (TypeError, ValueError, ZeroDivisionError):
            expected_ratio = 0
        actual_ratio = width / height if width > 0 and height > 0 else 0
        if not actual_ratio or not expected_ratio or abs(actual_ratio - expected_ratio) / expected_ratio > 0.03:
            blocking.append(_item(
                "aspect_ratio",
                "Video aspect ratio does not match the creator kit.",
                expected=expected_aspect,
                actual=f"{width}:{height}" if width and height else "unknown",
            ))
        else:
            passed.append(_item("aspect_ratio_ok", "Video aspect ratio matches the creator kit."))

    max_lines = max(1, int(policy.get("subtitle_max_lines") or 2))
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    subtitle_path, rendered_line_counts = _rendered_subtitle_lines(root)
    overflowing = [index for index, count in enumerate(rendered_line_counts) if count > max_lines]
    if overflowing:
        blocking.append(_item(
            "subtitle_overflow",
            "One or more subtitles exceed the configured line limit.",
            event_indexes=overflowing[:50],
            count=len(overflowing),
            maximum_lines=max(rendered_line_counts),
            allowed_lines=max_lines,
            path=str(subtitle_path) if subtitle_path else "",
        ))
    elif rendered_line_counts:
        passed.append(_item(
            "subtitles_fit",
            "Subtitle lines fit the configured limit.",
            maximum_lines=max(rendered_line_counts),
            allowed_lines=max_lines,
            path=str(subtitle_path) if subtitle_path else "",
        ))
    elif segments:
        advisory.append(_item(
            "subtitles_unverified",
            "Rendered subtitle lines are unavailable; regenerate the preview before publishing.",
        ))

    if bool(policy.get("cover_required", False)):
        cover_names = [
            "cover_selected.jpg", "cover_vertical.jpg", "cover_landscape.jpg",
            "cover_selected.png", "cover_vertical.png", "cover_landscape.png",
        ]
        if not any((root / name).is_file() for name in cover_names):
            blocking.append(_item("cover_missing", "A selected platform cover is required."))
        else:
            passed.append(_item("cover_ready", "Platform cover is available."))

    loudness_min = policy.get("loudness_min_lufs")
    loudness_max = policy.get("loudness_max_lufs")
    if loudness_min is not None or loudness_max is not None:
        raw_loudness = manifest.get("audio_loudness_lufs")
        if raw_loudness is None:
            advisory.append(_item("audio_loudness_missing", "Audio loudness metadata is unavailable; listen before publishing."))
        else:
            loudness = float(raw_loudness)
            below = loudness_min is not None and loudness < float(loudness_min)
            above = loudness_max is not None and loudness > float(loudness_max)
            if below or above:
                blocking.append(_item("audio_loudness", "Audio loudness is outside the configured range.", loudness_lufs=loudness))
            else:
                passed.append(_item("audio_loudness_ok", "Audio loudness is within the configured range.", loudness_lufs=loudness))

    status = "blocked" if blocking else "advisory" if advisory else "passed"
    return {
        "status": status,
        "blocking": blocking,
        "advisory": advisory,
        "passed": passed,
        "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
