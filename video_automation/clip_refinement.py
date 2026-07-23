from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .agent_policy import DeterministicClipPolicy
from .clip_evaluator import (
    ExcludedWindow,
    WordWindow,
    clip_windows_fingerprint,
    excluded_windows_from_payload,
    evaluate_clip_windows,
    refinement_decision_fingerprint,
    transcript_word_windows,
)
from .clip_operations import (
    apply_refinement_action,
    clip_windows_from_payload,
    commit_refined_cuts,
    persist_refinement_state,
)
from .clip_state import (
    ClipQualityReport,
    ClipRefinementState,
    ClipWindow,
    RefinementActionKind,
    RefinementAttempt,
)
from .config import Settings
from .io_utils import read_json_file
from .task_queue import QueueControlRequested


TERMINAL_REFINEMENT_STATUSES = {"accepted", "needs_review"}


def refine_clip_boundaries(
    settings: Settings,
    job_dir: Path,
    *,
    force: bool = False,
    control_callback: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Run a bounded deterministic Think-Act-Observe loop over clip windows."""
    cuts = read_json_file(job_dir / "cuts.json")
    transcript = read_json_file(job_dir / "transcript.json")
    if not isinstance(cuts, dict):
        raise RuntimeError("cuts.json is missing or invalid")
    if not isinstance(transcript, dict):
        transcript = {}

    original_windows = clip_windows_from_payload(cuts)
    words = transcript_word_windows(transcript)
    excluded_windows = excluded_windows_from_payload(cuts)
    max_attempts = max(1, int(settings.clip_refinement_max_attempts))
    budget_seconds = max(
        0.05,
        float(settings.clip_refinement_time_budget_seconds),
    )
    decision_fingerprint = refinement_decision_fingerprint(
        words,
        excluded_windows,
        policy={
            "duration_seconds": float(cuts.get("duration_seconds") or 0.0),
            "min_clip_seconds": float(settings.cut_min_clip_seconds),
            "boundary_tolerance_seconds": float(
                settings.clip_refinement_boundary_tolerance_seconds
            ),
            "max_boundary_shift_seconds": float(
                settings.clip_refinement_max_shift_seconds
            ),
            "max_attempts": max_attempts,
            "time_budget_seconds": budget_seconds,
        },
    )
    windows_fingerprint = clip_windows_fingerprint(original_windows)
    existing = _load_state(job_dir / "clip_refinement.json")
    if (
        not force
        and existing is not None
        and existing.status in TERMINAL_REFINEMENT_STATUSES
        and existing.decision_fingerprint == decision_fingerprint
        and existing.final_report is not None
        and existing.final_report.fingerprint == windows_fingerprint
    ):
        return existing.to_dict()

    if (
        not force
        and existing is not None
        and existing.status == "running"
        and existing.source_fingerprint == windows_fingerprint
        and existing.decision_fingerprint == decision_fingerprint
        and existing.max_attempts == max_attempts
    ):
        state = existing
    else:
        state = ClipRefinementState(
            schema_version=1,
            job_name=job_dir.name,
            source_fingerprint=windows_fingerprint,
            decision_fingerprint=decision_fingerprint,
            original_windows=original_windows,
            current_windows=original_windows,
            max_attempts=max_attempts,
        )

    policy = DeterministicClipPolicy()
    started_at = time.monotonic()
    seen = {
        clip_windows_fingerprint(state.current_windows),
        *(attempt.output_fingerprint for attempt in state.attempts),
    }

    while state.attempt_count < state.max_attempts:
        _check_control(control_callback)
        if time.monotonic() - started_at > budget_seconds:
            state.status = "needs_review"
            state.stop_reason = "time_budget_exhausted"
            break

        report = _evaluate(
            settings,
            state.current_windows,
            words,
            excluded_windows,
            cuts,
        )
        if state.initial_report is None:
            state.initial_report = report
        action = policy.choose_action(state, report)
        output_windows = state.current_windows

        if action.kind == RefinementActionKind.ADJUST_BOUNDARY:
            output_windows = apply_refinement_action(
                state.current_windows,
                action,
                duration_seconds=float(cuts.get("duration_seconds") or 0.0),
            )

        output_fingerprint = clip_windows_fingerprint(output_windows)
        state.attempts.append(
            RefinementAttempt(
                number=state.attempt_count + 1,
                input_fingerprint=report.fingerprint,
                report=report,
                action=action,
                output_fingerprint=output_fingerprint,
            )
        )

        if action.kind == RefinementActionKind.ACCEPT:
            state.status = "accepted"
            state.stop_reason = "quality_checks_passed"
            state.final_report = report
            break
        if action.kind == RefinementActionKind.REQUIRE_REVIEW:
            state.status = "needs_review"
            state.stop_reason = "manual_review_required"
            state.final_report = report
            break
        if output_fingerprint in seen:
            state.status = "needs_review"
            state.stop_reason = "no_progress_or_oscillation"
            state.final_report = report
            break

        seen.add(output_fingerprint)
        state.current_windows = output_windows
        state.changed = state.current_windows != state.original_windows
        persist_refinement_state(job_dir, state)

    if state.final_report is None:
        state.final_report = _evaluate(
            settings,
            state.current_windows,
            words,
            excluded_windows,
            cuts,
        )
        state.status = "accepted" if state.final_report.passed else "needs_review"
        state.stop_reason = (
            "quality_checks_passed"
            if state.final_report.passed
            else state.stop_reason or "attempt_budget_exhausted"
        )

    _rollback_regression_if_needed(
        settings,
        state,
        words,
        excluded_windows,
        cuts,
    )
    state.changed = state.current_windows != state.original_windows
    commit_refined_cuts(job_dir, cuts, state)
    return state.to_dict()


def _evaluate(
    settings: Settings,
    windows: tuple[ClipWindow, ...],
    words: tuple[WordWindow, ...],
    excluded_windows: tuple[ExcludedWindow, ...],
    cuts: dict[str, object],
) -> ClipQualityReport:
    return evaluate_clip_windows(
        windows,
        words,
        excluded_windows=excluded_windows,
        duration_seconds=float(cuts.get("duration_seconds") or 0.0),
        min_clip_seconds=float(settings.cut_min_clip_seconds),
        boundary_tolerance_seconds=float(
            settings.clip_refinement_boundary_tolerance_seconds
        ),
        max_boundary_shift_seconds=float(settings.clip_refinement_max_shift_seconds),
    )


def _rollback_regression_if_needed(
    settings: Settings,
    state: ClipRefinementState,
    words: tuple[WordWindow, ...],
    excluded_windows: tuple[ExcludedWindow, ...],
    cuts: dict[str, object],
) -> None:
    initial = state.initial_report
    final = state.final_report
    if initial is None or final is None:
        return
    initial_blocking = int(initial.metrics.get("blocking_issue_count") or 0)
    final_blocking = int(final.metrics.get("blocking_issue_count") or 0)
    if final.score + 0.001 >= initial.score and final_blocking <= initial_blocking:
        return
    state.current_windows = state.original_windows
    state.final_report = _evaluate(
        settings,
        state.original_windows,
        words,
        excluded_windows,
        cuts,
    )
    state.status = "needs_review"
    state.changed = False
    state.stop_reason = "regression_rolled_back"


def _check_control(
    control_callback: Callable[[], str | None] | None,
) -> None:
    action = control_callback() if control_callback else None
    if action in {"paused", "canceled"}:
        raise QueueControlRequested(action)


def _load_state(path: Path) -> ClipRefinementState | None:
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        return None
    try:
        return ClipRefinementState.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return None
