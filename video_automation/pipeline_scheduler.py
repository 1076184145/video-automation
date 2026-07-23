from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .io_utils import write_json_atomic
from .jobs import Job
from .pipeline_spec import PIPELINE_STAGE_SELECTION_DEPENDENCIES
from .stage_runs import StageRunRepository
from .task_queue import QueueControlRequested


class ProgressReporter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def emit(self, event: str, **payload: Any) -> None:
        if not self.enabled:
            return
        data = {
            "event": event,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        print(json.dumps(data, ensure_ascii=False), flush=True)


@dataclass(frozen=True)
class PipelineStage:
    name: str
    status: str
    enabled: bool
    run: Callable[[dict[str, Any]], None]
    dependencies: frozenset[str] = frozenset()
    exclusive_resources: frozenset[str] = frozenset()


def expand_stage_selection(selected_stages: list[str] | None) -> set[str] | None:
    if not selected_stages:
        return None
    requested = {str(stage).strip() for stage in selected_stages if str(stage).strip()}
    unknown = sorted(requested - PIPELINE_STAGE_SELECTION_DEPENDENCIES.keys())
    if unknown:
        raise ValueError(f"unknown pipeline stage: {unknown[0]}")
    expanded = set(requested)
    pending = list(requested)
    while pending:
        stage = pending.pop()
        for dependency in PIPELINE_STAGE_SELECTION_DEPENDENCIES[stage]:
            if dependency not in expanded:
                expanded.add(dependency)
                pending.append(dependency)
    return expanded


def build_pipeline_batches(
    stages: list[PipelineStage],
    *,
    max_parallel_stages: int,
) -> list[list[PipelineStage]]:
    """Build stable dependency batches without sharing exclusive resources."""
    if not stages:
        return []
    concurrency = max(1, int(max_parallel_stages))
    stage_names = {stage.name for stage in stages}
    if len(stage_names) != len(stages):
        raise ValueError("pipeline stage names must be unique")

    remaining = list(stages)
    completed: set[str] = set()
    batches: list[list[PipelineStage]] = []
    while remaining:
        ready = [
            stage
            for stage in remaining
            if (stage.dependencies & stage_names).issubset(completed)
        ]
        if not ready:
            blocked = ", ".join(stage.name for stage in remaining)
            raise ValueError(
                f"pipeline dependency cycle or unresolved dependency among: {blocked}"
            )

        batch: list[PipelineStage] = []
        resources_in_use: set[str] = set()
        for stage in ready:
            if len(batch) >= concurrency:
                break
            if stage.exclusive_resources & resources_in_use:
                continue
            batch.append(stage)
            resources_in_use.update(stage.exclusive_resources)
        if not batch:
            batch = [ready[0]]

        batches.append(batch)
        selected = {stage.name for stage in batch}
        completed.update(selected)
        remaining = [stage for stage in remaining if stage.name not in selected]
    return batches


def _execute_pipeline_stage(
    progress: ProgressReporter,
    job: Job,
    stage: PipelineStage,
    context: dict[str, Any],
    *,
    stage_number: int,
    total_stages: int,
    job_name: str,
    pipeline_run_id: str | None,
    stage_repository: StageRunRepository | None,
    control_callback: Callable[[], str | None] | None,
) -> tuple[dict[str, Any], BaseException | None]:
    stage_payload = {
        "job_dir": str(job.job_dir),
        "source_path": str(job.source_path),
        "stage": stage.name,
        "stage_number": stage_number,
        "total_stages": total_stages,
    }
    action = control_callback() if control_callback else None
    if action in {"paused", "canceled"}:
        return (
            {
                "stage": stage.name,
                "status": action,
                "stage_number": stage_number,
                "total_stages": total_stages,
                "duration_seconds": 0.0,
            },
            QueueControlRequested(action),
        )
    if not stage.enabled:
        progress.emit("stage:skip", **stage_payload, reason="disabled")
        timing = {
            "stage": stage.name,
            "status": "skipped",
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": 0.0,
            "reason": "disabled",
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status="skipped",
                duration_seconds=0.0,
            )
        return timing, None

    started_at = time.monotonic()
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.record_stage(
            pipeline_run_id,
            job_name,
            stage.name,
            stage_number=stage_number,
            total_stages=total_stages,
            status="running",
        )
    progress.emit("stage:start", **stage_payload, status=job.status)
    try:
        stage.run(context)
    except QueueControlRequested as exc:
        duration = time.monotonic() - started_at
        metrics = _take_stage_metrics(context, stage.name)
        progress.emit(
            "stage:control",
            **stage_payload,
            status=exc.action,
            duration_seconds=round(duration, 3),
            **metrics,
        )
        timing = {
            "stage": stage.name,
            "status": exc.action,
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": round(duration, 3),
            **metrics,
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status=exc.action,
                duration_seconds=duration,
            )
        return timing, exc
    except Exception as exc:
        duration = time.monotonic() - started_at
        metrics = _take_stage_metrics(context, stage.name)
        progress.emit(
            "stage:error",
            **stage_payload,
            status=job.status,
            duration_seconds=round(duration, 3),
            error=str(exc),
            **metrics,
        )
        timing = {
            "stage": stage.name,
            "status": "failed",
            "stage_number": stage_number,
            "total_stages": total_stages,
            "duration_seconds": round(duration, 3),
            "error": str(exc),
            **metrics,
        }
        if stage_repository is not None and pipeline_run_id is not None:
            stage_repository.record_stage(
                pipeline_run_id,
                job_name,
                stage.name,
                stage_number=stage_number,
                total_stages=total_stages,
                status="failed",
                duration_seconds=duration,
                error=str(exc),
            )
        return timing, exc

    duration = time.monotonic() - started_at
    metrics = _take_stage_metrics(context, stage.name)
    timing = {
        "stage": stage.name,
        "status": "complete",
        "stage_number": stage_number,
        "total_stages": total_stages,
        "duration_seconds": round(duration, 3),
        **metrics,
    }
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.record_stage(
            pipeline_run_id,
            job_name,
            stage.name,
            stage_number=stage_number,
            total_stages=total_stages,
            status="complete",
            duration_seconds=duration,
        )
    progress.emit(
        "stage:complete",
        **stage_payload,
        status=job.status,
        duration_seconds=round(duration, 3),
        **metrics,
    )
    return timing, None


def _take_stage_metrics(context: dict[str, Any], stage_name: str) -> dict[str, float]:
    metrics_by_stage = context.get("_stage_metrics")
    if not isinstance(metrics_by_stage, dict):
        return {}
    raw = metrics_by_stage.pop(stage_name, None)
    if not isinstance(raw, dict):
        return {}
    metrics: dict[str, float] = {}
    for key in ("resource_wait_seconds", "execution_seconds"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            metrics[key] = round(max(0.0, float(value)), 3)
    return metrics


def run_pipeline(
    progress: ProgressReporter,
    job: Job,
    stages: list[PipelineStage],
    context: dict[str, Any],
    *,
    control_callback: Callable[[], str | None] | None = None,
    stage_repository: StageRunRepository | None = None,
) -> None:
    total_stages = len(stages)
    if stage_repository is None:
        candidate = context.get("_stage_repository")
        if isinstance(candidate, StageRunRepository):
            stage_repository = candidate
    job_name = Path(job.job_dir).name
    pipeline_run_id = (
        stage_repository.start_pipeline(job_name, total_stages=total_stages)
        if stage_repository is not None
        else None
    )
    timings: list[dict[str, Any]] = []
    pipeline_started_at = datetime.now().isoformat(timespec="seconds")
    _write_stage_timings(
        job,
        timings,
        status="running",
        total_stages=total_stages,
        started_at=pipeline_started_at,
    )
    progress.emit(
        "pipeline:start",
        job_dir=str(job.job_dir),
        source_path=str(job.source_path),
        total_stages=total_stages,
    )
    max_parallel_stages = max(1, int(context.get("_max_parallel_stages", 1)))
    batches = build_pipeline_batches(stages, max_parallel_stages=max_parallel_stages)
    stage_numbers = {stage.name: index for index, stage in enumerate(stages, start=1)}
    for batch in batches:
        action = control_callback() if control_callback else None
        if action in {"paused", "canceled"}:
            if stage_repository is not None and pipeline_run_id is not None:
                stage_repository.finish_pipeline(pipeline_run_id, action)
            raise QueueControlRequested(action)
        primary_stage = next((stage for stage in batch if stage.enabled), None)
        if primary_stage is not None:
            job.start_stage(primary_stage.status, primary_stage.name)
        batch_results: list[tuple[dict[str, Any], BaseException | None]] = []
        if len(batch) == 1:
            stage = batch[0]
            batch_results.append(
                _execute_pipeline_stage(
                    progress,
                    job,
                    stage,
                    context,
                    stage_number=stage_numbers[stage.name],
                    total_stages=total_stages,
                    job_name=job_name,
                    pipeline_run_id=pipeline_run_id,
                    stage_repository=stage_repository,
                    control_callback=control_callback,
                )
            )
        else:
            with ThreadPoolExecutor(
                max_workers=len(batch),
                thread_name_prefix="pipeline-stage",
            ) as executor:
                futures = [
                    executor.submit(
                        _execute_pipeline_stage,
                        progress,
                        job,
                        stage,
                        context,
                        stage_number=stage_numbers[stage.name],
                        total_stages=total_stages,
                        job_name=job_name,
                        pipeline_run_id=pipeline_run_id,
                        stage_repository=stage_repository,
                        control_callback=control_callback,
                    )
                    for stage in batch
                ]
                batch_results.extend(future.result() for future in as_completed(futures))

        batch_results.sort(key=lambda result: int(result[0]["stage_number"]))
        timings.extend(result[0] for result in batch_results)
        timings.sort(key=lambda timing: int(timing["stage_number"]))
        errors = [result[1] for result in batch_results if result[1] is not None]
        pipeline_status = "running"
        if errors:
            pipeline_status = (
                errors[0].action
                if isinstance(errors[0], QueueControlRequested)
                else "failed"
            )
        _write_stage_timings(
            job,
            timings,
            status=pipeline_status,
            total_stages=total_stages,
            started_at=pipeline_started_at,
        )
        if errors:
            error = errors[0]
            if stage_repository is not None and pipeline_run_id is not None:
                if isinstance(error, QueueControlRequested):
                    stage_repository.finish_pipeline(pipeline_run_id, error.action)
                else:
                    stage_repository.finish_pipeline(
                        pipeline_run_id,
                        "failed",
                        error=str(error),
                    )
            raise error
        if primary_stage is not None:
            job.complete_stage()
    _write_stage_timings(
        job,
        timings,
        status="complete",
        total_stages=total_stages,
        started_at=pipeline_started_at,
    )
    if stage_repository is not None and pipeline_run_id is not None:
        stage_repository.finish_pipeline(pipeline_run_id, "complete")


def _write_stage_timings(
    job: Job,
    stages: list[dict[str, Any]],
    *,
    status: str,
    total_stages: int,
    started_at: str,
) -> None:
    payload = {
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "total_stages": total_stages,
        "total_duration_seconds": round(
            sum(float(item.get("duration_seconds") or 0.0) for item in stages),
            3,
        ),
        "stages": stages,
    }
    write_json_atomic(job.job_dir / "stage_timings.json", payload)
