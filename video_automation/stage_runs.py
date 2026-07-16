from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


TERMINAL_RUN_STATUSES = {"complete", "failed", "paused", "canceled"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StageRunRepository:
    """Persistent pipeline and stage state shared by API and worker processes."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self._schema_lock = threading.Lock()
        self._initialized = False
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._schema_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS pipeline_runs (
                        id TEXT PRIMARY KEY,
                        job_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        worker_pid INTEGER,
                        total_stages INTEGER NOT NULL,
                        started_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        error TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_pipeline_runs_job
                    ON pipeline_runs(job_name, started_at DESC);

                    CREATE TABLE IF NOT EXISTS stage_runs (
                        pipeline_run_id TEXT NOT NULL,
                        job_name TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        stage_number INTEGER NOT NULL,
                        total_stages INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        worker_pid INTEGER,
                        started_at TEXT,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        duration_seconds REAL,
                        error TEXT,
                        PRIMARY KEY(pipeline_run_id, stage),
                        FOREIGN KEY(pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_stage_runs_job
                    ON stage_runs(job_name, pipeline_run_id, stage_number);
                    """
                )
            self._initialized = True

    def start_pipeline(self, job_name: str, *, total_stages: int) -> str:
        run_id = f"run_{uuid.uuid4().hex}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pipeline_runs(
                    id, job_name, status, worker_pid, total_stages,
                    started_at, updated_at, completed_at, error
                ) VALUES(?, ?, 'running', ?, ?, ?, ?, NULL, NULL)
                """,
                (run_id, str(job_name), os.getpid(), max(0, int(total_stages)), now, now),
            )
        return run_id

    def finish_pipeline(self, run_id: str, status: str, *, error: str | None = None) -> None:
        now = _now()
        completed_at = now if status in TERMINAL_RUN_STATUSES else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, updated_at = ?, completed_at = ?, error = ?
                WHERE id = ?
                """,
                (str(status), now, completed_at, _bounded_error(error), str(run_id)),
            )

    def record_stage(
        self,
        run_id: str,
        job_name: str,
        stage: str,
        *,
        stage_number: int,
        total_stages: int,
        status: str,
        duration_seconds: float | None = None,
        error: str | None = None,
    ) -> None:
        now = _now()
        terminal = status in {"complete", "failed", "skipped", "paused", "canceled"}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO stage_runs(
                    pipeline_run_id, job_name, stage, stage_number, total_stages,
                    status, worker_pid, started_at, updated_at, completed_at,
                    duration_seconds, error
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_run_id, stage) DO UPDATE SET
                    status = excluded.status,
                    worker_pid = excluded.worker_pid,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at,
                    duration_seconds = excluded.duration_seconds,
                    error = excluded.error
                """,
                (
                    str(run_id),
                    str(job_name),
                    str(stage),
                    int(stage_number),
                    int(total_stages),
                    str(status),
                    os.getpid(),
                    now,
                    now,
                    now if terminal else None,
                    None if duration_seconds is None else round(max(0.0, float(duration_seconds)), 3),
                    _bounded_error(error),
                ),
            )

    def list_for_job(self, job_name: str, *, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection:
            runs = connection.execute(
                """
                SELECT * FROM pipeline_runs
                WHERE job_name = ? ORDER BY started_at DESC, id DESC LIMIT ?
                """,
                (str(job_name), max(1, int(limit))),
            ).fetchall()
            run_ids = [str(row["id"]) for row in runs]
            stages: list[sqlite3.Row] = []
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                stages = connection.execute(
                    f"SELECT * FROM stage_runs WHERE pipeline_run_id IN ({placeholders}) "
                    "ORDER BY pipeline_run_id, stage_number ASC",
                    run_ids,
                ).fetchall()
        by_run: dict[str, list[dict[str, Any]]] = {run_id: [] for run_id in run_ids}
        for row in stages:
            by_run[str(row["pipeline_run_id"])].append(_stage_payload(row))
        return [
            {
                "id": row["id"],
                "job_name": row["job_name"],
                "status": row["status"],
                "worker_pid": row["worker_pid"],
                "total_stages": int(row["total_stages"]),
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
                "stages": by_run.get(str(row["id"]), []),
            }
            for row in runs
        ]


def _stage_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stage": row["stage"],
        "stage_number": int(row["stage_number"]),
        "total_stages": int(row["total_stages"]),
        "status": row["status"],
        "worker_pid": row["worker_pid"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "duration_seconds": row["duration_seconds"],
        "error": row["error"],
    }


def _bounded_error(error: str | None) -> str | None:
    if error is None:
        return None
    return str(error)[:4000]
