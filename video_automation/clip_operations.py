from __future__ import annotations

from pathlib import Path
from typing import Any

from .clip_state import (
    ClipRefinementState,
    ClipWindow,
    RefinementAction,
    RefinementActionKind,
)
from .cuts import write_cuts_payload
from .io_utils import write_json_atomic


DOWNSTREAM_CLIP_ARTIFACTS = (
    "subtitles_clipped.ass",
    "render_preview.json",
    "final_render_preview.json",
    "review.mp4",
    "final.mp4",
    "web_preview.json",
    "web_preview.mp4",
)


def clip_windows_from_payload(payload: dict[str, Any]) -> tuple[ClipWindow, ...]:
    raw_clips = payload.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        raise RuntimeError("cuts.json has no clip windows")
    windows: list[ClipWindow] = []
    for index, raw in enumerate(raw_clips):
        if not isinstance(raw, dict):
            raise RuntimeError(f"cuts.json clip {index + 1} is invalid")
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"cuts.json clip {index + 1} has invalid bounds") from exc
        windows.append(
            ClipWindow(
                index=index,
                start=round(start, 3),
                end=round(end, 3),
                keep=bool(raw.get("keep", True)),
            )
        )
    return tuple(windows)


def apply_refinement_action(
    windows: tuple[ClipWindow, ...],
    action: RefinementAction,
    *,
    duration_seconds: float,
) -> tuple[ClipWindow, ...]:
    if action.kind != RefinementActionKind.ADJUST_BOUNDARY:
        return windows
    if action.clip_index is None:
        raise ValueError("boundary adjustment requires clip_index")

    updated: list[ClipWindow] = []
    found = False
    duration = max(0.0, float(duration_seconds))
    for window in windows:
        if window.index != action.clip_index:
            updated.append(window)
            continue
        found = True
        start = window.start if action.start is None else max(0.0, float(action.start))
        end = window.end if action.end is None else float(action.end)
        if duration:
            end = min(duration, end)
        if end <= start:
            raise ValueError("boundary adjustment produced an empty clip")
        updated.append(
            ClipWindow(
                index=window.index,
                start=round(start, 3),
                end=round(end, 3),
                keep=window.keep,
            )
        )
    if not found:
        raise ValueError(f"clip index does not exist: {action.clip_index}")

    ordered = tuple(sorted(updated, key=lambda window: window.index))
    kept = [window for window in ordered if window.keep]
    for previous, current in zip(kept, kept[1:]):
        if current.start < previous.end - 0.001:
            raise ValueError("boundary adjustment produced overlapping clips")
    return ordered


def merge_refined_windows(
    payload: dict[str, Any],
    windows: tuple[ClipWindow, ...],
    state: ClipRefinementState,
) -> dict[str, Any]:
    raw_clips = payload.get("clips")
    if not isinstance(raw_clips, list) or len(raw_clips) != len(windows):
        raise RuntimeError("cuts payload changed during refinement")
    by_index = {window.index: window for window in windows}
    clips: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_clips):
        if not isinstance(raw, dict) or index not in by_index:
            raise RuntimeError("cuts payload changed during refinement")
        window = by_index[index]
        value = dict(raw)
        value.update(
            {
                "start": round(window.start, 3),
                "end": round(window.end, 3),
                "duration": window.duration,
                "keep": window.keep,
            }
        )
        clips.append(value)

    updated = dict(payload)
    updated["clips"] = clips
    updated["refinement"] = {
        "schema_version": state.schema_version,
        "status": state.status,
        "changed": state.changed,
        "attempt_count": state.attempt_count,
        "stop_reason": state.stop_reason,
        "initial_score": (
            round(state.initial_report.score, 2) if state.initial_report is not None else None
        ),
        "final_score": (
            round(state.final_report.score, 2) if state.final_report is not None else None
        ),
        "state_file": "clip_refinement.json",
    }
    return updated


def persist_refinement_state(job_dir: Path, state: ClipRefinementState) -> None:
    write_json_atomic(job_dir / "clip_refinement.json", state.to_dict())


def commit_refined_cuts(
    job_dir: Path,
    payload: dict[str, Any],
    state: ClipRefinementState,
) -> None:
    if state.changed:
        invalidate_downstream_clip_artifacts(job_dir)
    write_cuts_payload(job_dir, merge_refined_windows(payload, state.current_windows, state))
    persist_refinement_state(job_dir, state)


def invalidate_downstream_clip_artifacts(job_dir: Path) -> list[str]:
    removed: list[str] = []
    for name in DOWNSTREAM_CLIP_ARTIFACTS:
        path = job_dir / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(name)
    return removed
