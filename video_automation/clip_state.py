from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RefinementActionKind(StrEnum):
    ACCEPT = "accept"
    ADJUST_BOUNDARY = "adjust_boundary"
    REQUIRE_REVIEW = "require_review"


@dataclass(frozen=True)
class ClipWindow:
    index: int
    start: float
    end: float
    keep: bool = True

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end - self.start), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": self.duration,
            "keep": self.keep,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClipWindow":
        return cls(
            index=int(payload["index"]),
            start=float(payload["start"]),
            end=float(payload["end"]),
            keep=bool(payload.get("keep", True)),
        )


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    clip_index: int | None = None
    blocking: bool = False
    suggested_start: float | None = None
    suggested_end: float | None = None

    @property
    def repairable(self) -> bool:
        return self.suggested_start is not None or self.suggested_end is not None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "repairable": self.repairable,
        }
        if self.clip_index is not None:
            payload["clip_index"] = self.clip_index
        if self.suggested_start is not None:
            payload["suggested_start"] = round(self.suggested_start, 3)
        if self.suggested_end is not None:
            payload["suggested_end"] = round(self.suggested_end, 3)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QualityIssue":
        return cls(
            code=str(payload["code"]),
            message=str(payload.get("message") or ""),
            clip_index=(
                int(payload["clip_index"]) if payload.get("clip_index") is not None else None
            ),
            blocking=bool(payload.get("blocking", False)),
            suggested_start=(
                float(payload["suggested_start"])
                if payload.get("suggested_start") is not None
                else None
            ),
            suggested_end=(
                float(payload["suggested_end"])
                if payload.get("suggested_end") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ClipQualityReport:
    passed: bool
    score: float
    issues: tuple[QualityIssue, ...]
    metrics: dict[str, float | int]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": dict(self.metrics),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClipQualityReport":
        raw_issues = payload.get("issues")
        raw_metrics = payload.get("metrics")
        return cls(
            passed=bool(payload.get("passed", False)),
            score=float(payload.get("score") or 0.0),
            issues=tuple(
                QualityIssue.from_dict(issue)
                for issue in raw_issues
                if isinstance(issue, dict)
            )
            if isinstance(raw_issues, list)
            else (),
            metrics=dict(raw_metrics) if isinstance(raw_metrics, dict) else {},
            fingerprint=str(payload.get("fingerprint") or ""),
        )


@dataclass(frozen=True)
class RefinementAction:
    kind: RefinementActionKind
    reason: str
    clip_index: int | None = None
    start: float | None = None
    end: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "reason": self.reason,
        }
        if self.clip_index is not None:
            payload["clip_index"] = self.clip_index
        if self.start is not None:
            payload["start"] = round(self.start, 3)
        if self.end is not None:
            payload["end"] = round(self.end, 3)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefinementAction":
        return cls(
            kind=RefinementActionKind(str(payload["kind"])),
            reason=str(payload.get("reason") or ""),
            clip_index=(
                int(payload["clip_index"]) if payload.get("clip_index") is not None else None
            ),
            start=float(payload["start"]) if payload.get("start") is not None else None,
            end=float(payload["end"]) if payload.get("end") is not None else None,
        )


@dataclass(frozen=True)
class RefinementAttempt:
    number: int
    input_fingerprint: str
    report: ClipQualityReport
    action: RefinementAction
    output_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "input_fingerprint": self.input_fingerprint,
            "report": self.report.to_dict(),
            "action": self.action.to_dict(),
            "output_fingerprint": self.output_fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefinementAttempt":
        report = payload.get("report")
        action = payload.get("action")
        if not isinstance(report, dict) or not isinstance(action, dict):
            raise ValueError("refinement attempt is invalid")
        return cls(
            number=int(payload["number"]),
            input_fingerprint=str(payload.get("input_fingerprint") or ""),
            report=ClipQualityReport.from_dict(report),
            action=RefinementAction.from_dict(action),
            output_fingerprint=str(payload.get("output_fingerprint") or ""),
        )


@dataclass
class ClipRefinementState:
    schema_version: int
    job_name: str
    source_fingerprint: str
    decision_fingerprint: str
    original_windows: tuple[ClipWindow, ...]
    current_windows: tuple[ClipWindow, ...]
    max_attempts: int
    status: str = "running"
    attempts: list[RefinementAttempt] = field(default_factory=list)
    initial_report: ClipQualityReport | None = None
    final_report: ClipQualityReport | None = None
    changed: bool = False
    stop_reason: str = ""

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_name": self.job_name,
            "source_fingerprint": self.source_fingerprint,
            "decision_fingerprint": self.decision_fingerprint,
            "status": self.status,
            "max_attempts": self.max_attempts,
            "attempt_count": self.attempt_count,
            "changed": self.changed,
            "stop_reason": self.stop_reason,
            "original_windows": [window.to_dict() for window in self.original_windows],
            "current_windows": [window.to_dict() for window in self.current_windows],
            "initial_report": self.initial_report.to_dict() if self.initial_report else None,
            "final_report": self.final_report.to_dict() if self.final_report else None,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClipRefinementState":
        original = payload.get("original_windows")
        current = payload.get("current_windows")
        attempts = payload.get("attempts")
        if not isinstance(original, list) or not isinstance(current, list):
            raise ValueError("refinement windows are invalid")
        if (
            not original
            or len(original) != len(current)
            or any(not isinstance(item, dict) for item in (*original, *current))
        ):
            raise ValueError("refinement windows are incomplete")
        if attempts is not None and (
            not isinstance(attempts, list)
            or any(not isinstance(item, dict) for item in attempts)
        ):
            raise ValueError("refinement attempts are invalid")
        initial_report = payload.get("initial_report")
        final_report = payload.get("final_report")
        state = cls(
            schema_version=int(payload.get("schema_version") or 1),
            job_name=str(payload.get("job_name") or ""),
            source_fingerprint=str(
                payload.get("source_fingerprint")
                or payload.get("inputs_fingerprint")
                or ""
            ),
            decision_fingerprint=str(
                payload.get("decision_fingerprint")
                or payload.get("inputs_fingerprint")
                or ""
            ),
            original_windows=tuple(ClipWindow.from_dict(item) for item in original),
            current_windows=tuple(ClipWindow.from_dict(item) for item in current),
            max_attempts=max(1, int(payload.get("max_attempts") or 1)),
            status=str(payload.get("status") or "running"),
            attempts=[
                RefinementAttempt.from_dict(item)
                for item in attempts
            ]
            if isinstance(attempts, list)
            else [],
            initial_report=(
                ClipQualityReport.from_dict(initial_report)
                if isinstance(initial_report, dict)
                else None
            ),
            final_report=(
                ClipQualityReport.from_dict(final_report)
                if isinstance(final_report, dict)
                else None
            ),
            changed=bool(payload.get("changed", False)),
            stop_reason=str(payload.get("stop_reason") or ""),
        )
        original_indexes = [window.index for window in state.original_windows]
        current_indexes = [window.index for window in state.current_windows]
        if (
            len(set(original_indexes)) != len(original_indexes)
            or original_indexes != current_indexes
        ):
            raise ValueError("refinement window indexes are invalid")
        return state
