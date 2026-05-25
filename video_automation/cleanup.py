from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings
from .jobs import list_jobs


def cleanup_jobs(settings: Settings, *, days: int, dry_run: bool = False) -> dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=days)
    removed = []
    kept = []
    for job in list_jobs(settings):
        updated = _parse_datetime(job.updated_at)
        if updated is None or updated >= cutoff:
            kept.append({"job_dir": str(job.job_dir), "status": job.status, "updated_at": job.updated_at})
            continue
        item = {"job_dir": str(job.job_dir), "status": job.status, "updated_at": job.updated_at}
        removed.append(item)
        if not dry_run and _inside_jobs_dir(settings.jobs_dir, job.job_dir):
            shutil.rmtree(job.job_dir, ignore_errors=True)
    return {
        "dry_run": dry_run,
        "days": days,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "removed_count": len(removed),
        "kept_count": len(kept),
        "removed": removed,
    }


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _inside_jobs_dir(jobs_dir: Path, job_dir: Path) -> bool:
    try:
        job_dir.resolve().relative_to(jobs_dir.resolve())
    except ValueError:
        return False
    return True
