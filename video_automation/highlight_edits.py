from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .cuts import snap_to_silence_valley
from .llm_evaluator import finite_time


def protected_speech(sentences: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Without reliable word times protect the entire ASR sentence."""
    return sorted((float(w["start"]), float(w["end"])) for s in sentences for w in (s.get("words") or [s]))


def _overlaps(a: float, b: float, speech: list[tuple[float, float]]) -> bool:
    return any(min(b, end) - max(a, start) > 1e-6 for start, end in speech)


def _quiet_parts(a: float, b: float, speech: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = []
    cursor = a
    for start, end in speech:
        if end <= cursor or start >= b:
            continue
        if start > cursor:
            result.append((cursor, min(start, b)))
        cursor = max(cursor, end)
    if cursor < b:
        result.append((cursor, b))
    return result


def build_highlight_edit(
    candidate: dict[str, Any], sentences: list[dict[str, Any]], silences: list[dict[str, Any]],
    audio_path: Path, *, source_duration: float, fps: int = 30, pause_seconds: float = .2,
) -> dict[str, Any]:
    """Produce one source-time EDL; do not rewrite media or ASR timestamps."""
    speech = protected_speech(sentences)
    start, end = float(candidate["start"]), float(candidate["end"])
    if not (0 <= start < end <= source_duration) or fps <= 0:
        raise ValueError("Highlight is outside the source timeline.")
    snapped_start = snap_to_silence_valley(str(audio_path), start, True)
    snapped_end = min(source_duration, snap_to_silence_valley(str(audio_path), end, False))
    if not _overlaps(snapped_start, start, speech):
        start = snapped_start
    if not _overlaps(end, snapped_end, speech):
        end = snapped_end
    # Quantize all media and subtitle boundaries to the same output-frame grid.
    # Outward rounding preserves complete words; reject if it picks up a neighbor.
    a, b = math.floor(start * fps) / fps, math.ceil(end * fps) / fps
    if _overlaps(a, start, speech) or _overlaps(end, b, speech) or b > source_duration + 1e-6:
        raise ValueError("No frame-aligned boundary clear of adjacent speech.")
    start, end = a, b
    quiet = []
    for silence in silences:
        a, b = finite_time(silence["start"]), finite_time(silence["end"])
        if b <= a:
            continue
        a, b = max(start, a), min(end, b)
        if b > a:
            quiet.extend(_quiet_parts(a, b, speech))
    merged: list[list[float]] = []
    for a, b in sorted(quiet):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    removed = []
    for a, b in merged:
        # Only long pauses (>0.6s) are compressed. Preserve ~100ms at each end;
        # round inward into silence so no protected speech is ever removed.
        if b - a <= .6:
            continue
        left = math.ceil((a + pause_seconds / 2) * fps) / fps
        right = math.floor((b - pause_seconds / 2) * fps) / fps
        if right > left:
            removed.append((left, right))
    spans, cursor, offset = [], start, 0.
    for a, b in [*removed, (end, end)]:
        if a > cursor + 1e-6:
            spans.append({"start": cursor, "end": a, "output_start": offset, "output_end": offset + a - cursor})
            offset += a - cursor
        cursor = b
    if not 30 - 1e-6 <= offset <= 75 + 1e-6:
        raise ValueError("Edited duration is outside 30-75 seconds after pause compression and snapping.")
    return {**candidate, "start": start, "end": end, "spans": spans, "duration": offset,
            "fps": fps, "pause_target_seconds": pause_seconds, "audio_fade_seconds": .015}


def map_interval(start: float, end: float, spans: list[dict[str, float]]) -> list[tuple[float, float]]:
    """The shared original -> output mapping, including split subtitle events."""
    return [(span["output_start"] + max(start, span["start"]) - span["start"],
             span["output_start"] + min(end, span["end"]) - span["start"])
            for span in spans if min(end, span["end"]) > max(start, span["start"])]
