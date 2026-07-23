from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .clip_state import ClipQualityReport, ClipWindow, QualityIssue


@dataclass(frozen=True)
class WordWindow:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ExcludedWindow:
    start: float
    end: float
    reason: str


def clip_windows_fingerprint(windows: Iterable[ClipWindow]) -> str:
    payload = [
        {
            "index": window.index,
            "start": round(window.start, 3),
            "end": round(window.end, 3),
            "keep": window.keep,
        }
        for window in windows
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transcript_word_windows(transcript: dict[str, Any]) -> tuple[WordWindow, ...]:
    words: list[WordWindow] = []
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return ()
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        raw_words = segment.get("words")
        if not isinstance(raw_words, list):
            continue
        for raw_word in raw_words:
            if not isinstance(raw_word, dict):
                continue
            try:
                start = max(0.0, float(raw_word["start"]))
                end = float(raw_word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            text = str(raw_word.get("word") or raw_word.get("text") or "").strip()
            if end <= start or not text:
                continue
            words.append(WordWindow(round(start, 3), round(end, 3), text[:128]))
    return tuple(sorted(words, key=lambda word: (word.start, word.end)))


def excluded_windows_from_payload(payload: dict[str, Any]) -> tuple[ExcludedWindow, ...]:
    windows: list[ExcludedWindow] = []
    raw_segments = payload.get("invalid_segments")
    if not isinstance(raw_segments, list):
        return ()
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        try:
            start = max(0.0, float(raw["start"]))
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        windows.append(
            ExcludedWindow(
                start=round(start, 3),
                end=round(end, 3),
                reason=str(raw.get("reason") or "invalid_segment")[:128],
            )
        )
    return tuple(sorted(windows, key=lambda window: (window.start, window.end)))


def refinement_decision_fingerprint(
    words: tuple[WordWindow, ...],
    excluded_windows: tuple[ExcludedWindow, ...],
    *,
    policy: dict[str, float | int],
) -> str:
    payload = {
        "schema_version": 1,
        "words": [
            {
                "start": round(word.start, 3),
                "end": round(word.end, 3),
                "text": word.text,
            }
            for word in words
        ],
        "excluded_windows": [
            {
                "start": round(window.start, 3),
                "end": round(window.end, 3),
            }
            for window in excluded_windows
        ],
        "policy": {
            key: round(float(value), 6)
            for key, value in sorted(policy.items())
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_clip_windows(
    windows: tuple[ClipWindow, ...],
    words: tuple[WordWindow, ...],
    *,
    excluded_windows: tuple[ExcludedWindow, ...] = (),
    duration_seconds: float,
    min_clip_seconds: float,
    boundary_tolerance_seconds: float,
    max_boundary_shift_seconds: float,
) -> ClipQualityReport:
    issues: list[QualityIssue] = []
    kept = tuple(window for window in windows if window.keep)
    duration = max(0.0, float(duration_seconds))
    minimum = max(0.0, float(min_clip_seconds))
    tolerance = max(0.0, float(boundary_tolerance_seconds))
    max_shift = max(0.0, float(max_boundary_shift_seconds))

    if not kept:
        issues.append(QualityIssue("no_kept_clips", "No kept clips are available.", blocking=True))

    previous: ClipWindow | None = None
    for position, window in enumerate(kept):
        if window.start < 0 or window.end <= window.start or (duration and window.end > duration + 0.001):
            issues.append(
                QualityIssue(
                    "invalid_window",
                    "Clip bounds are outside the source duration.",
                    clip_index=window.index,
                    blocking=True,
                )
            )
            continue
        if previous is not None and window.start < previous.end - 0.001:
            issues.append(
                QualityIssue(
                    "overlapping_clips",
                    "Kept clips overlap.",
                    clip_index=window.index,
                    blocking=True,
                )
            )
        if window.duration < minimum:
            issues.append(
                QualityIssue(
                    "clip_too_short",
                    "Clip is shorter than the configured minimum.",
                    clip_index=window.index,
                    blocking=True,
                )
            )
        previous_end = kept[position - 1].end if position > 0 else 0.0
        next_start = kept[position + 1].start if position + 1 < len(kept) else duration
        start_issue = _boundary_issue(
            window,
            words,
            boundary=window.start,
            kind="start",
            lower_bound=previous_end,
            upper_bound=window.end,
            tolerance=tolerance,
            max_shift=max_shift,
            minimum_duration=minimum,
            excluded_windows=excluded_windows,
        )
        if start_issue is not None:
            issues.append(start_issue)
        end_issue = _boundary_issue(
            window,
            words,
            boundary=window.end,
            kind="end",
            lower_bound=window.start,
            upper_bound=next_start or duration,
            tolerance=tolerance,
            max_shift=max_shift,
            minimum_duration=minimum,
            excluded_windows=excluded_windows,
            source_edge=position + 1 == len(kept) and bool(duration),
        )
        if end_issue is not None:
            issues.append(end_issue)
        previous = window

    blocking_count = sum(1 for issue in issues if issue.blocking)
    repairable_count = sum(1 for issue in issues if issue.repairable)
    score = max(0.0, 100.0 - blocking_count * 25.0 - repairable_count * 5.0)
    total_kept = round(sum(window.duration for window in kept), 3)
    minimum_duration = round(min((window.duration for window in kept), default=0.0), 3)
    fingerprint = clip_windows_fingerprint(windows)
    return ClipQualityReport(
        passed=not issues,
        score=score,
        issues=tuple(issues),
        metrics={
            "clip_count": len(windows),
            "kept_clip_count": len(kept),
            "total_kept_seconds": total_kept,
            "minimum_clip_seconds": minimum_duration,
            "boundary_issue_count": sum(
                1 for issue in issues if issue.code in {"start_inside_word", "end_inside_word"}
            ),
            "blocking_issue_count": blocking_count,
        },
        fingerprint=fingerprint,
    )


def _boundary_issue(
    window: ClipWindow,
    words: tuple[WordWindow, ...],
    *,
    boundary: float,
    kind: str,
    lower_bound: float,
    upper_bound: float,
    tolerance: float,
    max_shift: float,
    minimum_duration: float,
    excluded_windows: tuple[ExcludedWindow, ...],
    source_edge: bool = False,
) -> QualityIssue | None:
    if boundary <= tolerance or (
        kind == "end" and source_edge and abs(boundary - upper_bound) <= tolerance
    ):
        return None
    for word in words:
        if word.start >= boundary:
            break
        if not (word.start + tolerance < boundary < word.end - tolerance):
            continue
        proposals = (
            (word.start, word.end)
            if kind == "start"
            else (word.end, word.start)
        )
        proposed = next(
            (
                candidate
                for candidate in proposals
                if _safe_boundary_candidate(
                    window,
                    candidate,
                    kind=kind,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                    max_shift=max_shift,
                    minimum_duration=minimum_duration,
                    excluded_windows=excluded_windows,
                )
            ),
            None,
        )
        code = "start_inside_word" if kind == "start" else "end_inside_word"
        message = f"Clip {kind} cuts through a transcribed word."
        if proposed is None:
            return QualityIssue(code, message, clip_index=window.index, blocking=True)
        return QualityIssue(
            code,
            message,
            clip_index=window.index,
            suggested_start=proposed if kind == "start" else None,
            suggested_end=proposed if kind == "end" else None,
        )
    return None


def _safe_boundary_candidate(
    window: ClipWindow,
    proposed: float,
    *,
    kind: str,
    lower_bound: float,
    upper_bound: float,
    max_shift: float,
    minimum_duration: float,
    excluded_windows: tuple[ExcludedWindow, ...],
) -> bool:
    if abs(proposed - (window.start if kind == "start" else window.end)) > max_shift:
        return False
    start = proposed if kind == "start" else window.start
    end = window.end if kind == "start" else proposed
    if start < lower_bound - 0.001 or end > upper_bound + 0.001:
        return False
    if end <= start + 0.001 or end - start < minimum_duration - 0.001:
        return False
    return all(
        _overlap_seconds(start, end, excluded.start, excluded.end)
        <= _overlap_seconds(
            window.start,
            window.end,
            excluded.start,
            excluded.end,
        )
        + 0.001
        for excluded in excluded_windows
    )


def _overlaps(
    start: float,
    end: float,
    other_start: float,
    other_end: float,
) -> bool:
    return start < other_end - 0.001 and end > other_start + 0.001


def _overlap_seconds(
    start: float,
    end: float,
    other_start: float,
    other_end: float,
) -> float:
    if not _overlaps(start, end, other_start, other_end):
        return 0.0
    return max(0.0, min(end, other_end) - max(start, other_start))
