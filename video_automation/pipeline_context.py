from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .stage_runs import StageRunRepository


@dataclass
class PipelineContext:
    """Typed, job-local state shared by pipeline stages.

    Durable job and stage lifecycle state remains owned by ``Job`` and
    ``StageRunRepository``. This object only carries data produced and consumed
    during one pipeline execution.
    """

    audio_path: Path
    high_quality_audio_path: Path | None
    manifest: dict[str, Any] | None = None
    media_outputs_prepared: bool = False
    visual_events_prepared: bool = False
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    stage_repository: StageRunRepository | None = None
    max_parallel_stages: int = 1
    _stage_metrics: dict[str, dict[str, float]] = field(default_factory=dict)

    def record_stage_metrics(
        self,
        stage_name: str,
        *,
        resource_wait_seconds: float,
        execution_seconds: float,
    ) -> None:
        self._stage_metrics[stage_name] = {
            "resource_wait_seconds": round(max(0.0, float(resource_wait_seconds)), 3),
            "execution_seconds": round(max(0.0, float(execution_seconds)), 3),
        }

    def take_stage_metrics(self, stage_name: str) -> dict[str, float]:
        raw = self._stage_metrics.pop(stage_name, None)
        if not isinstance(raw, dict):
            return {}
        return {
            key: round(max(0.0, float(value)), 3)
            for key in ("resource_wait_seconds", "execution_seconds")
            if isinstance((value := raw.get(key)), (int, float))
        }

    def require_review(self, reason: str) -> None:
        value = str(reason).strip()
        self.requires_review = True
        if value and value not in self.review_reasons:
            self.review_reasons.append(value)
