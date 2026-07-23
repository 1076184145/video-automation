from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file


TERMINAL_JOB_STATUSES = frozenset({"needs_review", "done", "failed", "canceled"})
INTERMEDIATE_FILE_NAMES = frozenset({"audio.wav", "audio_hq.flac"})
CLEANUP_MODES = frozenset({"all", "intermediates"})


def cleanup_jobs(
    settings: Settings,
    *,
    days: int,
    dry_run: bool = False,
    mode: str = "all",
) -> dict[str, Any]:
    if int(days) <= 0:
        raise ValueError("cleanup retention days must be a positive integer")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in CLEANUP_MODES:
        raise ValueError(f"unsupported cleanup mode: {mode}")

    cutoff = datetime.now() - timedelta(days=int(days))
    removed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    candidates: list[str] = []
    reclaimed_bytes = 0
    jobs_dir = Path(settings.jobs_dir)
    if not jobs_dir.is_dir():
        return _cleanup_result(
            dry_run=dry_run,
            days=int(days),
            mode=normalized_mode,
            cutoff=cutoff,
            removed=removed,
            skipped=skipped,
            candidates=candidates,
            reclaimed_bytes=0,
        )

    for job_dir in sorted(jobs_dir.iterdir(), key=lambda path: path.name.lower()):
        if not job_dir.is_dir() or job_dir.is_symlink() or job_dir.name.startswith("."):
            continue
        state = read_json_file(job_dir / "job.json")
        status = str(state.get("status") or "").strip().lower() if isinstance(state, dict) else ""
        updated_at = str(state.get("updated_at") or "") if isinstance(state, dict) else ""
        updated = _parse_datetime(updated_at) or datetime.fromtimestamp(job_dir.stat().st_mtime)
        item = {"job_dir": str(job_dir), "status": status, "updated_at": updated.isoformat(timespec="seconds")}
        if updated >= cutoff:
            skipped.append({**item, "reason": "retention_not_reached"})
            continue
        if not status:
            skipped.append({**item, "reason": "unknown_status"})
            continue
        if status not in TERMINAL_JOB_STATUSES:
            skipped.append({**item, "reason": "active_job"})
            continue
        if not _inside_jobs_dir(jobs_dir, job_dir):
            skipped.append({**item, "reason": "outside_jobs_dir"})
            continue

        if normalized_mode == "intermediates":
            if status != "done" or not (job_dir / "final.mp4").is_file():
                skipped.append({**item, "reason": "final_output_required"})
                continue
            paths = _intermediate_paths(job_dir)
            candidates.extend(str(path) for path in paths)
            bytes_for_job = sum(_path_size(path) for path in paths)
            if not dry_run:
                for path in paths:
                    _remove_path(path)
                reclaimed_bytes += bytes_for_job
            removed.append({**item, "files": len(paths), "bytes": bytes_for_job})
            continue

        bytes_for_job = _path_size(job_dir)
        candidates.append(str(job_dir))
        if not dry_run:
            shutil.rmtree(job_dir)
            reclaimed_bytes += bytes_for_job
        removed.append({**item, "files": None, "bytes": bytes_for_job})

    return _cleanup_result(
        dry_run=dry_run,
        days=int(days),
        mode=normalized_mode,
        cutoff=cutoff,
        removed=removed,
        skipped=skipped,
        candidates=candidates,
        reclaimed_bytes=reclaimed_bytes,
    )


def _cleanup_result(
    *,
    dry_run: bool,
    days: int,
    mode: str,
    cutoff: datetime,
    removed: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    candidates: list[str],
    reclaimed_bytes: int,
) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "days": days,
        "mode": mode,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "removed_count": len(removed),
        "kept_count": len(skipped),
        "removed": removed,
        "skipped": skipped,
        "candidates": sorted(candidates),
        "reclaimed_bytes": int(reclaimed_bytes),
    }


def _intermediate_paths(job_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in job_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        name = path.name.lower()
        if name in INTERMEDIATE_FILE_NAMES or _is_temporary_name(name):
            paths.append(path)
    return sorted(paths, key=lambda path: str(path).lower())


def _is_temporary_name(name: str) -> bool:
    value = str(name or "").lower()
    stem = Path(value).stem
    return (
        value.endswith(".tmp")
        or value.endswith(".uploading")
        or ".tmp." in value
        or stem.endswith(".tmp")
    )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _path_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in files:
            file_path = Path(root) / name
            if file_path.is_symlink():
                continue
            try:
                total += int(file_path.stat().st_size)
            except OSError:
                continue
    return total


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _inside_jobs_dir(jobs_dir: Path, job_dir: Path) -> bool:
    try:
        relative = job_dir.resolve().relative_to(jobs_dir.resolve())
    except ValueError:
        return False
    return bool(relative.parts)
