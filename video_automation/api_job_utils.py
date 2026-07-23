from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .events import publish_event
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .jobs import Job, load_job
from .queue_worker import process_is_alive


TERMINAL_JOB_STATUSES = frozenset({"needs_review", "done", "failed", "canceled"})


def job_is_terminal(job: Job) -> bool:
    return job.status in TERMINAL_JOB_STATUSES


def job_runtime_state(
    job_status: str,
    queue_item: dict[str, Any] | None,
    pipeline_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    queue_status = str((queue_item or {}).get("status") or "")
    queue_pid = safe_int((queue_item or {}).get("worker_pid"))
    queue_active = queue_status in {"pending", "paused", "running"}
    latest_run = pipeline_runs[0] if pipeline_runs else None
    pipeline_pid = safe_int((latest_run or {}).get("worker_pid"))
    pipeline_active = bool(
        latest_run
        and latest_run.get("status") == "running"
        and pid_is_alive(pipeline_pid)
    )
    terminal = job_status in TERMINAL_JOB_STATUSES
    # Queue ownership is authoritative even if job.json temporarily contains
    # a terminal projection while the worker finishes its acknowledgement.
    active = queue_active or (not terminal and pipeline_active)
    stale = not terminal and not active
    queue_summary = None
    if queue_item is not None:
        queue_summary = {
            "id": queue_item.get("id"),
            "status": queue_status,
            "worker_pid": queue_item.get("worker_pid"),
            "heartbeat_at": queue_item.get("heartbeat_at"),
            "cancel_requested": bool(queue_item.get("cancel_requested")),
        }
    return {
        "active": active,
        "stale": stale,
        "can_cancel": not terminal
        and not bool((queue_item or {}).get("cancel_requested"))
        and (active or stale),
        "can_delete": not active and (terminal or stale),
        "queue": queue_summary,
        "pipeline": {
            "id": latest_run.get("id"),
            "status": latest_run.get("status"),
            "worker_pid": latest_run.get("worker_pid"),
        }
        if latest_run
        else None,
    }


def pid_is_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    return process_is_alive(pid)


def job_feedback(job_dir: Path) -> dict[str, Any]:
    return read_json_file(job_dir / "feedback.json") or {"items": []}


def save_clip_feedback(job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    action = bounded_text(payload.get("action"), 20)
    if action not in {"accepted", "rejected", "clear"}:
        raise ValueError("action must be accepted, rejected, or clear")
    clip_key = bounded_text(payload.get("clip_key"), 120)
    if not clip_key:
        raise ValueError("clip_key is required")
    current = job_feedback(job_dir)
    items = current.get("items") if isinstance(current.get("items"), list) else []
    items = [item for item in items if isinstance(item, dict) and item.get("clip_key") != clip_key]
    if action != "clear":
        items.append({
            "clip_key": clip_key,
            "action": action,
            "index": safe_int(payload.get("index")),
            "start": safe_float(payload.get("start")),
            "end": safe_float(payload.get("end")),
            "reason": bounded_text(payload.get("reason"), 200),
            "text": bounded_text(payload.get("text"), 500),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    result = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items[-1000:],
    }
    write_json_atomic(job_dir / "feedback.json", result)
    return result


def record_transcript_preferences(
    repository: Any,
    job_name: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> int:
    """Record explicit text edits only; timing changes are not preference signals."""
    before_segments = before.get("segments") if isinstance(before.get("segments"), list) else []
    after_segments = after.get("segments") if isinstance(after.get("segments"), list) else []
    recorded = 0
    for previous, current in zip(before_segments, after_segments):
        if not isinstance(previous, dict) or not isinstance(current, dict):
            continue
        previous_text = str(previous.get("text") or "").strip()
        current_text = str(current.get("text") or "").strip()
        if not previous_text or not current_text or previous_text == current_text:
            continue
        repository.record(
            "subtitle_correction",
            {"before": previous_text[:500], "after": current_text[:500]},
            job_name=job_name,
        )
        recorded += 1
    return recorded


def bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def job_files(job_dir: Path) -> list[dict[str, Any]]:
    if not job_dir.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        relative_name = str(path.relative_to(job_dir)).replace("\\", "/")
        files.append({
            "name": relative_name,
            "path": str(path),
            "size_bytes": stat.st_size,
            "modified_at": int(stat.st_mtime),
        })
    return files


def string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def publish_job_dir_event(job_dir: Path) -> None:
    job = load_job(job_dir / "job.json")
    if job is not None:
        publish_event("job", job.to_dict())


def update_transcript_from_editor(job_dir: Path, segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(segments, list):
        raise RuntimeError("segments must be a list")
    current = read_json_file(job_dir / "transcript.json") or {}
    normalized = validate_transcript_segments(segments)
    payload = dict(current) if isinstance(current, dict) else {}
    payload["segments"] = normalized
    payload["edited_in_web"] = True
    write_json_atomic(job_dir / "transcript.json", payload)
    write_text_atomic(
        job_dir / "transcript.txt",
        "\n".join(segment["text"] for segment in normalized if segment["text"]).strip() + "\n",
    )
    write_text_atomic(job_dir / "transcript.srt", segments_to_srt(normalized))
    return payload


def validate_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise RuntimeError(f"transcript segment {index} is invalid")
        try:
            start = round(max(0.0, float(segment["start"])), 3)
            end = round(max(start, float(segment["end"])), 3)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"transcript segment {index} start/end is invalid") from exc
        value = dict(segment)
        value["start"] = start
        value["end"] = end
        value["text"] = str(segment.get("text") or "").strip()
        normalized.append(value)
    return sorted(normalized, key=lambda item: (float(item["start"]), float(item["end"])))


def transcript_summary(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    segments = transcript.get("segments") if isinstance(transcript, dict) else []
    if not isinstance(segments, list):
        return summary
    for segment in segments[:200]:
        if not isinstance(segment, dict):
            continue
        summary.append({
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": str(segment.get("text", "")).strip(),
        })
    return summary


def segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n"
            f"{srt_time(float(segment['start']))} --> {srt_time(float(segment['end']))}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def remove_render_outputs(job_dir: Path) -> None:
    for name in [
        "review.mp4",
        "final.mp4",
        "render_preview.json",
        "render_review.ps1",
        "final_render_preview.json",
    ]:
        path = job_dir / name
        if path.exists():
            path.unlink()
