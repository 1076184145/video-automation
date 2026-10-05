from __future__ import annotations

import json
import re
from dataclasses import dataclass
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


@dataclass
class _GateContext:
    root: Path
    policy: dict[str, Any]
    manifest: dict[str, Any]
    transcript: dict[str, Any]
    blocking: list[dict[str, Any]]
    advisory: list[dict[str, Any]]
    passed: list[dict[str, Any]]


def _check_refinement(context: _GateContext) -> None:
    refinement_path = context.root / "clip_refinement.json"
    if not refinement_path.is_file():
        return
    refinement = _read_json(refinement_path)
    refinement_status = str(refinement.get("status") or "invalid")
    final_report = (
        refinement.get("final_report")
        if isinstance(refinement.get("final_report"), dict)
        else {}
    )
    try:
        refinement_score = float(final_report.get("score") or 0.0)
    except (TypeError, ValueError):
        refinement_score = 0.0
    details = {
        "status": refinement_status,
        "stop_reason": str(refinement.get("stop_reason") or ""),
        "score": refinement_score,
    }
    if refinement_status == "accepted":
        context.passed.append(_item(
            "clip_refinement_passed", "Clip-boundary checks passed.", **details,
        ))
    else:
        context.blocking.append(_item(
            "clip_refinement_required", "Clip-boundary checks require manual review.", **details,
        ))


def _check_render(context: _GateContext) -> None:
    output = context.root / "final.mp4"
    if not output.is_file():
        output = context.root / "review.mp4"
    if not output.is_file() or output.stat().st_size < 1:
        context.blocking.append(_item("render_missing", "A final or review video is required."))
    else:
        context.passed.append(_item("render_ready", "Rendered video is available.", path=str(output)))


def _check_duration(context: _GateContext) -> None:
    duration = float(context.manifest.get("duration_seconds") or 0)
    minimum = float(context.policy.get("duration_min_seconds") or 0)
    maximum = float(context.policy.get("duration_max_seconds") or 0)
    if duration <= 0:
        context.blocking.append(_item("duration_invalid", "Video duration could not be verified."))
    elif (minimum and duration < minimum) or (maximum and duration > maximum):
        context.blocking.append(_item(
            "duration_limit", "Video duration is outside the configured platform range.",
            duration_seconds=duration,
        ))
    else:
        context.passed.append(_item(
            "duration_ok", "Video duration is within the configured range.", duration_seconds=duration,
        ))


def _check_aspect(context: _GateContext) -> None:
    expected_aspect = str(context.policy.get("aspect") or "").strip()
    width = int(context.manifest.get("width") or 0)
    height = int(context.manifest.get("height") or 0)
    if not expected_aspect:
        return
    try:
        expected_width, expected_height = [float(value) for value in expected_aspect.split(":", 1)]
        expected_ratio = expected_width / expected_height
    except (TypeError, ValueError, ZeroDivisionError):
        expected_ratio = 0
    actual_ratio = width / height if width > 0 and height > 0 else 0
    if not actual_ratio or not expected_ratio or abs(actual_ratio - expected_ratio) / expected_ratio > 0.03:
        context.blocking.append(_item(
            "aspect_ratio", "Video aspect ratio does not match the creator kit.",
            expected=expected_aspect,
            actual=f"{width}:{height}" if width and height else "unknown",
        ))
    else:
        context.passed.append(_item("aspect_ratio_ok", "Video aspect ratio matches the creator kit."))


def _check_subtitles(context: _GateContext) -> None:
    max_lines = max(1, int(context.policy.get("subtitle_max_lines") or 2))
    segments = context.transcript.get("segments") if isinstance(context.transcript.get("segments"), list) else []
    subtitle_path, rendered_line_counts = _rendered_subtitle_lines(context.root)
    overflowing = [index for index, count in enumerate(rendered_line_counts) if count > max_lines]
    if overflowing:
        context.blocking.append(_item(
            "subtitle_overflow", "One or more subtitles exceed the configured line limit.",
            event_indexes=overflowing[:50],
            count=len(overflowing),
            maximum_lines=max(rendered_line_counts),
            allowed_lines=max_lines,
            path=str(subtitle_path) if subtitle_path else "",
        ))
    elif rendered_line_counts:
        context.passed.append(_item(
            "subtitles_fit", "Subtitle lines fit the configured limit.",
            maximum_lines=max(rendered_line_counts),
            allowed_lines=max_lines,
            path=str(subtitle_path) if subtitle_path else "",
        ))
    elif segments:
        context.advisory.append(_item(
            "subtitles_unverified",
            "Rendered subtitle lines are unavailable; regenerate the preview before publishing.",
        ))


def _check_cover(context: _GateContext) -> None:
    if not bool(context.policy.get("cover_required", False)):
        return
    cover_names = [
        "cover_selected.jpg", "cover_vertical.jpg", "cover_landscape.jpg",
        "cover_selected.png", "cover_vertical.png", "cover_landscape.png",
    ]
    if not any((context.root / name).is_file() for name in cover_names):
        context.blocking.append(_item("cover_missing", "A selected platform cover is required."))
    else:
        context.passed.append(_item("cover_ready", "Platform cover is available."))


def _check_loudness(context: _GateContext) -> None:
    loudness_min = context.policy.get("loudness_min_lufs")
    loudness_max = context.policy.get("loudness_max_lufs")
    if loudness_min is None and loudness_max is None:
        return
    raw_loudness = context.manifest.get("audio_loudness_lufs")
    if raw_loudness is None:
        context.advisory.append(_item(
            "audio_loudness_missing", "Audio loudness metadata is unavailable; listen before publishing.",
        ))
        return
    loudness = float(raw_loudness)
    below = loudness_min is not None and loudness < float(loudness_min)
    above = loudness_max is not None and loudness > float(loudness_max)
    if below or above:
        context.blocking.append(_item(
            "audio_loudness", "Audio loudness is outside the configured range.", loudness_lufs=loudness,
        ))
    else:
        context.passed.append(_item(
            "audio_loudness_ok", "Audio loudness is within the configured range.", loudness_lufs=loudness,
        ))


def evaluate_quality_gate(job_dir: Path | str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(job_dir)
    context = _GateContext(
        root=root,
        policy=policy if isinstance(policy, dict) else {},
        manifest=_read_json(root / "manifest.json"),
        transcript=_read_json(root / "transcript.json"),
        blocking=[],
        advisory=[],
        passed=[],
    )
    for check in (
        _check_refinement, _check_render, _check_duration, _check_aspect,
        _check_subtitles, _check_cover, _check_loudness,
    ):
        check(context)
    status = "blocked" if context.blocking else "advisory" if context.advisory else "passed"
    return {
        "status": status,
        "blocking": context.blocking,
        "advisory": context.advisory,
        "passed": context.passed,
        "checked_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
