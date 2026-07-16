from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator


QUEUE_STATUSES = {"pending", "running", "paused", "completed", "failed", "canceled"}


class QueueControlRequested(RuntimeError):
    def __init__(self, action: str):
        if action not in {"paused", "canceled"}:
            raise ValueError("queue control action must be paused or canceled")
        self.action = action
        super().__init__(f"Queue item {action} at a safe stage boundary")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class QueueRepository:
    """SQLite-backed queue state that survives application restarts."""

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
                    CREATE TABLE IF NOT EXISTS task_queue (
                        id TEXT PRIMARY KEY,
                        job_name TEXT NOT NULL UNIQUE,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        priority INTEGER NOT NULL DEFAULT 0,
                        position INTEGER NOT NULL DEFAULT 0,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        retry_stage TEXT,
                        worker_pid INTEGER,
                        heartbeat_at TEXT,
                        error TEXT,
                        pause_requested INTEGER NOT NULL DEFAULT 0,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_queue_claim
                    ON task_queue(status, priority DESC, position ASC, created_at ASC);

                    CREATE TABLE IF NOT EXISTS queue_control (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        paused INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT OR IGNORE INTO queue_control(id, paused, updated_at) VALUES(1, 0, ?)",
                    (_now(),),
                )
            self._initialized = True

    def enqueue(self, job_name: str, payload: dict[str, Any], *, priority: int = 0) -> dict[str, Any]:
        normalized_job = str(job_name or "").strip()
        if not normalized_job:
            raise ValueError("job_name is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM task_queue WHERE job_name = ?", (normalized_job,)
            ).fetchone()
            if existing and existing["status"] in {"pending", "running", "paused"}:
                return self._payload(existing)
            position = int(
                connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM task_queue"
                ).fetchone()["next_position"]
            )
            queue_id = existing["id"] if existing else f"queue_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO task_queue(
                    id, job_name, payload_json, status, priority, position, attempt,
                    retry_stage, worker_pid, heartbeat_at, error, pause_requested,
                    cancel_requested, created_at, updated_at, started_at, completed_at
                ) VALUES(?, ?, ?, 'pending', ?, ?, 0, NULL, NULL, NULL, NULL, 0, 0, ?, ?, NULL, NULL)
                ON CONFLICT(job_name) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    status = 'pending', priority = excluded.priority, position = excluded.position,
                    retry_stage = NULL, worker_pid = NULL, heartbeat_at = NULL, error = NULL,
                    pause_requested = 0, cancel_requested = 0, updated_at = excluded.updated_at,
                    started_at = NULL, completed_at = NULL
                """,
                (
                    queue_id,
                    normalized_job,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    int(priority),
                    position,
                    now,
                    now,
                ),
            )
        return self.get(queue_id) or {}

    def get(self, queue_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM task_queue WHERE id = ?", (queue_id,)).fetchone()
        return self._payload(row) if row else None

    def get_by_job(self, job_name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE job_name = ?", (job_name,)
            ).fetchone()
        return self._payload(row) if row else None

    def list_items(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_queue ORDER BY priority DESC, position ASC, created_at ASC"
            ).fetchall()
        return [self._payload(row) for row in rows]

    def claim_next(self, *, worker_pid: int | None = None) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            control = connection.execute("SELECT paused FROM queue_control WHERE id = 1").fetchone()
            if control and bool(control["paused"]):
                return None
            row = connection.execute(
                """
                SELECT * FROM task_queue
                WHERE status = 'pending'
                ORDER BY priority DESC, position ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            now = _now()
            connection.execute(
                """
                UPDATE task_queue
                SET status = 'running', worker_pid = ?, heartbeat_at = ?, started_at = ?,
                    updated_at = ?, error = NULL
                WHERE id = ? AND status = 'pending'
                """,
                (worker_pid or os.getpid(), now, now, now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM task_queue WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._payload(claimed) if claimed else None

    def complete(self, queue_id: str) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        status = "canceled" if current["cancel_requested"] else "completed"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue SET status = ?, worker_pid = NULL, heartbeat_at = NULL,
                    pause_requested = 0, updated_at = ?, completed_at = ? WHERE id = ?
                """,
                (status, now, now, queue_id),
            )
        return self.get(queue_id)

    def fail(self, queue_id: str, error: str) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        status = "canceled" if current["cancel_requested"] else "failed"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue SET status = ?, error = ?, worker_pid = NULL, heartbeat_at = NULL,
                    updated_at = ?, completed_at = ? WHERE id = ?
                """,
                (status, str(error)[:4000], now, now, queue_id),
            )
        return self.get(queue_id)

    def acknowledge_control(self, queue_id: str, action: str) -> dict[str, Any] | None:
        if action not in {"paused", "canceled"}:
            raise ValueError("queue control action must be paused or canceled")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue SET status = ?, worker_pid = NULL, heartbeat_at = NULL,
                    pause_requested = 0, cancel_requested = CASE WHEN ? = 'canceled' THEN 1 ELSE 0 END,
                    updated_at = ?, completed_at = CASE WHEN ? = 'canceled' THEN ? ELSE NULL END
                WHERE id = ?
                """,
                (action, action, now, action, now, queue_id),
            )
        return self.get(queue_id)

    def heartbeat(self, queue_id: str, *, worker_pid: int | None = None) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE task_queue SET heartbeat_at = ?, worker_pid = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now(), worker_pid or os.getpid(), _now(), queue_id),
            )
        return cursor.rowcount > 0

    def pause(self, queue_id: str) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        with self._connect() as connection:
            if current["status"] == "pending":
                connection.execute(
                    "UPDATE task_queue SET status = 'paused', updated_at = ? WHERE id = ?",
                    (_now(), queue_id),
                )
            elif current["status"] == "running":
                connection.execute(
                    "UPDATE task_queue SET pause_requested = 1, updated_at = ? WHERE id = ?",
                    (_now(), queue_id),
                )
        return self.get(queue_id)

    def resume(self, queue_id: str) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue SET status = CASE WHEN status = 'paused' THEN 'pending' ELSE status END,
                    pause_requested = 0, updated_at = ? WHERE id = ?
                """,
                (_now(), queue_id),
            )
        return self.get(queue_id)

    def cancel(self, queue_id: str) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        with self._connect() as connection:
            if current["status"] == "running":
                connection.execute(
                    """
                    UPDATE task_queue SET cancel_requested = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (_now(), queue_id),
                )
            elif current["status"] in {"pending", "paused", "failed"}:
                connection.execute(
                    """
                    UPDATE task_queue SET status = 'canceled', cancel_requested = 1,
                        updated_at = ?, completed_at = ? WHERE id = ?
                    """,
                    (_now(), _now(), queue_id),
                )
        return self.get(queue_id)

    def retry_stage(
        self,
        queue_id: str,
        stage: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get(queue_id)
        if current is None:
            return None
        if current["status"] in {"running", "paused"}:
            raise ValueError("cannot retry a stage while the queue item is active")
        normalized_stage = str(stage or "").strip()
        if not normalized_stage:
            raise ValueError("stage is required")
        payload_json = None
        if payload is not None:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue SET status = 'pending', retry_stage = ?, attempt = attempt + 1,
                    worker_pid = NULL, heartbeat_at = NULL, error = NULL, pause_requested = 0,
                    cancel_requested = 0, payload_json = COALESCE(?, payload_json),
                    updated_at = ?, started_at = NULL, completed_at = NULL
                WHERE id = ?
                """,
                (normalized_stage[:80], payload_json, _now(), queue_id),
            )
        return self.get(queue_id)

    def reorder(self, queue_ids: list[str]) -> list[dict[str, Any]]:
        if not isinstance(queue_ids, list):
            raise ValueError("ids must be a list")
        with self._connect() as connection:
            for position, queue_id in enumerate(queue_ids):
                connection.execute(
                    "UPDATE task_queue SET position = ?, updated_at = ? WHERE id = ?",
                    (position, _now(), str(queue_id)),
                )
        by_id = {item["id"]: item for item in self.list_items()}
        ordered = [by_id[queue_id] for queue_id in queue_ids if queue_id in by_id]
        ordered_ids = {item["id"] for item in ordered}
        ordered.extend(item for item in self.list_items() if item["id"] not in ordered_ids)
        return ordered

    def set_global_paused(self, paused: bool) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE queue_control SET paused = ?, updated_at = ? WHERE id = 1",
                (1 if paused else 0, _now()),
            )
        return self.control_state()

    def control_state(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM queue_control WHERE id = 1").fetchone()
        return {"paused": bool(row["paused"]), "updated_at": row["updated_at"]}

    def recover_interrupted(self, stale_before: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE task_queue SET status = 'pending', worker_pid = NULL, heartbeat_at = NULL,
                    error = 'Recovered after interrupted worker', updated_at = ?, started_at = NULL
                WHERE status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)
                """,
                (_now(), stale_before),
            )
        return cursor.rowcount

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_name": row["job_name"],
            "payload": _decode(row["payload_json"], {}),
            "status": row["status"],
            "priority": int(row["priority"]),
            "position": int(row["position"]),
            "attempt": int(row["attempt"]),
            "retry_stage": row["retry_stage"],
            "worker_pid": row["worker_pid"],
            "heartbeat_at": row["heartbeat_at"],
            "error": row["error"],
            "pause_requested": bool(row["pause_requested"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }


class QueueService:
    def __init__(
        self,
        repository: QueueRepository,
        executor: Callable[[dict[str, Any]], None],
        *,
        poll_interval: float = 0.25,
        heartbeat_interval: float = 5.0,
    ):
        self.repository = repository
        self.executor = executor
        self.poll_interval = max(0.05, poll_interval)
        self.heartbeat_interval = max(0.1, heartbeat_interval)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def run_once(self) -> bool:
        item = self.repository.claim_next(worker_pid=os.getpid())
        if item is None:
            return False
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(self.heartbeat_interval):
                if not self.repository.heartbeat(item["id"], worker_pid=os.getpid()):
                    return

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            self.executor(item)
            latest = self.repository.get(item["id"]) or item
            if latest.get("cancel_requested"):
                self.repository.acknowledge_control(item["id"], "canceled")
            elif latest.get("pause_requested"):
                self.repository.acknowledge_control(item["id"], "paused")
            else:
                self.repository.complete(item["id"])
        except QueueControlRequested as exc:
            self.repository.acknowledge_control(item["id"], exc.action)
        except Exception as exc:
            self.repository.fail(item["id"], str(exc))
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
        return True

    def start(self, *, workers: int = 1) -> None:
        if self._threads:
            return
        self._stop.clear()
        for index in range(max(1, workers)):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"video-automation-queue-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads = []

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll_interval)
