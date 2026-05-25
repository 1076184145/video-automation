from __future__ import annotations

import json
import logging
import re
import threading
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import write_json_atomic


TERMINAL_STATUSES = {"needs_review", "done", "failed"}
READY_STATUSES = {"needs_review", "done"}

WINDOWS_ABSOLUTE_IN_QUOTES_RE = re.compile(r"""["']([A-Za-z]:[\\/][^"']+)["']""")


def utc_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_stem(path: Path) -> str:
    allowed = []
    for char in path.stem:
        if char.isalnum() or char in ("-", "_"):
            allowed.append(char)
        elif char.isspace():
            allowed.append("-")
    value = "".join(allowed).strip("-_")
    return value[:80] or "recording"


@dataclass
class Job:
    source_path: Path
    job_dir: Path
    status: str = "pending"
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    current_stage: str | None = None
    stage_progress: float | None = None
    stage_message: str | None = None
    stage_started_at: str | None = None
    stage_estimate_seconds: float | None = None
    _save_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    @property
    def state_path(self) -> Path:
        return self.job_dir / "job.json"

    @property
    def log_path(self) -> Path:
        return self.job_dir / "job.log"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "job_dir": str(self.job_dir),
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_stage": self.current_stage,
            "stage_progress": self.stage_progress,
            "stage_message": self.stage_message,
            "stage_started_at": self.stage_started_at,
            "stage_estimate_seconds": self.stage_estimate_seconds,
        }

    def save(self) -> None:
        with self._save_lock:
            self.updated_at = datetime.now().isoformat(timespec="seconds")
            self.job_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.state_path, self.to_dict())

    def set_status(self, status: str) -> None:
        self.status = status
        self.error = None
        self.save()

    def start_stage(self, status: str, stage: str, *, message: str | None = None) -> None:
        self.status = status
        self.error = None
        self.current_stage = stage
        self.stage_progress = 0.0
        self.stage_message = message
        self.stage_started_at = datetime.now().isoformat(timespec="seconds")
        self.save()

    def update_stage_progress(self, percent: float | None, *, message: str | None = None) -> None:
        self.stage_progress = None if percent is None else round(max(0.0, min(100.0, percent)), 2)
        if message is not None:
            self.stage_message = message
        self.save()

    def complete_stage(self) -> None:
        self.stage_progress = 100.0
        self.save()

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.error = error
        self.save()


def configure_job_logger(job: Job) -> logging.Logger:
    logger = logging.getLogger(f"video_automation.job.{job.job_dir.name}")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = RotatingFileHandler(job.log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def find_existing_job(settings: Settings, source_path: Path) -> Job | None:
    if not settings.jobs_dir.exists():
        return None
    source_text = str(source_path.resolve())
    for state_path in sorted(settings.jobs_dir.glob("*/job.json"), reverse=True):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("source_path") == source_text:
            return Job(
                source_path=Path(data["source_path"]),
                job_dir=Path(data["job_dir"]),
                status=data.get("status", "pending"),
                error=data.get("error"),
                created_at=data.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                updated_at=data.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
                current_stage=data.get("current_stage"),
                stage_progress=data.get("stage_progress"),
                stage_message=data.get("stage_message"),
                stage_started_at=data.get("stage_started_at"),
                stage_estimate_seconds=data.get("stage_estimate_seconds"),
            )
    return None


def load_job(state_path: Path) -> Job | None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    try:
        return Job(
            source_path=Path(data["source_path"]),
            job_dir=Path(data.get("job_dir") or state_path.parent),
            status=data.get("status", "pending"),
            error=data.get("error"),
            created_at=data.get("created_at") or datetime.now().isoformat(timespec="seconds"),
            updated_at=data.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
            current_stage=data.get("current_stage"),
            stage_progress=data.get("stage_progress"),
            stage_message=data.get("stage_message"),
            stage_started_at=data.get("stage_started_at"),
            stage_estimate_seconds=data.get("stage_estimate_seconds"),
        )
    except (TypeError, KeyError):
        return None


def list_jobs(settings: Settings) -> list[Job]:
    if not settings.jobs_dir.exists():
        return []
    jobs = [job for path in settings.jobs_dir.glob("*/job.json") if (job := load_job(path))]
    return sorted(jobs, key=lambda job: job.updated_at, reverse=True)


def find_resume_jobs(settings: Settings) -> list[Job]:
    return [job for job in list_jobs(settings) if job.status not in READY_STATUSES]


def create_job(settings: Settings, source_path: Path | str, *, force: bool = False) -> Job:
    resolved = normalize_source_path(source_path).resolve()
    if not force:
        existing = find_existing_job(settings, resolved)
        if existing:
            return existing
    job_dir = settings.jobs_dir / f"{utc_stamp()}-{safe_stem(resolved)}"
    job = Job(source_path=resolved, job_dir=job_dir)
    job.save()
    return job


def normalize_source_path(source_path: Path | str) -> Path:
    text = str(source_path).strip()
    quoted_windows_path = WINDOWS_ABSOLUTE_IN_QUOTES_RE.search(text)
    if quoted_windows_path:
        return Path(quoted_windows_path.group(1).strip())
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return Path(text)
