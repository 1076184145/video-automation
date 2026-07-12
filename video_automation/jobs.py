from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .error_advisor import advise_error
from .events import publish_event
from .io_utils import write_json_atomic


TERMINAL_STATUSES = {"needs_review", "done", "failed"}
READY_STATUSES = {"needs_review", "done"}

WINDOWS_ABSOLUTE_IN_QUOTES_RE = re.compile(r"""["']([A-Za-z]:[\\/][^"']+)["']""")
MAX_PERSISTED_ERROR_BYTES = 16 * 1024
ERROR_TRUNCATION_MARKER = "\n...[error truncated]"


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
    batch_id: str | None = None
    batch_index: int | None = None
    batch_size: int | None = None
    status: str = "pending"
    error: str | None = None
    error_advice: dict[str, Any] | None = None
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
            "batch_id": self.batch_id,
            "batch_index": self.batch_index,
            "batch_size": self.batch_size,
            "status": self.status,
            "error": self.error,
            "error_advice": self.error_advice,
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
            payload = self.to_dict()
            write_json_atomic(self.state_path, payload)
        try:
            publish_event("job", payload)
        except Exception:
            logging.getLogger(__name__).debug("failed to publish job event", exc_info=True)

    def set_status(self, status: str) -> None:
        self.status = status
        self.error = None
        self.error_advice = None
        self.save()

    def start_stage(self, status: str, stage: str, *, message: str | None = None) -> None:
        self.status = status
        self.error = None
        self.error_advice = None
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
        bounded_error = _bounded_error(error)
        self.error = bounded_error
        self.error_advice = advise_error(bounded_error)
        self.save()


def _bounded_error(error: str) -> str:
    encoded = str(error).encode("utf-8", errors="replace")
    if len(encoded) <= MAX_PERSISTED_ERROR_BYTES:
        return encoded.decode("utf-8", errors="replace")
    marker = ERROR_TRUNCATION_MARKER.encode("utf-8")
    prefix = encoded[: MAX_PERSISTED_ERROR_BYTES - len(marker)].decode("utf-8", errors="ignore")
    return prefix + ERROR_TRUNCATION_MARKER


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
                # Trust the state file location, not the serialized job_dir.
                # A tampered job.json should not redirect API file
                # enumeration or future saves outside the jobs directory.
                job_dir=state_path.parent,
                batch_id=data.get("batch_id"),
                batch_index=data.get("batch_index"),
                batch_size=data.get("batch_size"),
                status=data.get("status", "pending"),
                error=data.get("error"),
                error_advice=data.get("error_advice") if isinstance(data.get("error_advice"), dict) else None,
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
            # The caller selected this state file; keep the job rooted beside
            # it even if a stale or tampered payload contains another job_dir.
            job_dir=state_path.parent,
            batch_id=data.get("batch_id"),
            batch_index=data.get("batch_index"),
            batch_size=data.get("batch_size"),
            status=data.get("status", "pending"),
            error=data.get("error"),
            error_advice=data.get("error_advice") if isinstance(data.get("error_advice"), dict) else None,
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


def create_job(
    settings: Settings,
    source_path: Path | str,
    *,
    force: bool = False,
    batch_id: str | None = None,
    batch_index: int | None = None,
    batch_size: int | None = None,
) -> Job:
    resolved = normalize_source_path(source_path).resolve()
    if not force:
        existing = find_existing_job(settings, resolved)
        if existing:
            return existing
    job_dir = settings.jobs_dir / f"{utc_stamp()}-{safe_stem(resolved)}-{uuid.uuid4().hex}"
    job = Job(
        source_path=resolved,
        job_dir=job_dir,
        batch_id=batch_id,
        batch_index=batch_index,
        batch_size=batch_size,
    )
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
