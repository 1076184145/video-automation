from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Protocol


PUBLISH_TRANSITIONS = {
    "draft": {"validating"},
    "validating": {"uploading", "failed"},
    "uploading": {"processing", "published", "failed"},
    "processing": {"published", "failed"},
    "failed": {"validating", "uploading"},
    "published": set(),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _decode(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class PublishProvider(Protocol):
    def validate(self, attempt: dict[str, Any]) -> dict[str, Any]: ...
    def upload(self, attempt: dict[str, Any], progress) -> dict[str, Any]: ...
    def query(self, attempt: dict[str, Any]) -> dict[str, Any]: ...


class PublishRepository:
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
                    CREATE TABLE IF NOT EXISTS publish_attempts (
                        id TEXT PRIMARY KEY,
                        job_name TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL,
                        credential_ref TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        upload_url TEXT,
                        uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                        total_bytes INTEGER NOT NULL DEFAULT 0,
                        remote_id TEXT,
                        error TEXT,
                        retryable INTEGER NOT NULL DEFAULT 0,
                        action TEXT,
                        manual_package_path TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_publish_attempts_job
                    ON publish_attempts(job_name, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_publish_attempts_status
                    ON publish_attempts(status, updated_at DESC);
                    """
                )
            self._initialized = True

    def create_attempt(
        self,
        job_name: str,
        provider: str,
        *,
        credential_ref: str | None = None,
        payload: dict[str, Any],
        total_bytes: int = 0,
        manual_package_path: str | None = None,
    ) -> dict[str, Any]:
        if not str(job_name or "").strip():
            raise ValueError("job_name is required")
        if not str(provider or "").strip():
            raise ValueError("provider is required")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        attempt_id = f"publish_{uuid.uuid4().hex}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO publish_attempts(
                    id, job_name, provider, status, credential_ref, payload_json,
                    uploaded_bytes, total_bytes, retryable, manual_package_path,
                    created_at, updated_at
                ) VALUES(?, ?, ?, 'draft', ?, ?, 0, ?, 0, ?, ?, ?)
                """,
                (
                    attempt_id,
                    str(job_name).strip(),
                    str(provider).strip(),
                    str(credential_ref).strip() if credential_ref else None,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    max(0, int(total_bytes)),
                    str(manual_package_path) if manual_package_path else None,
                    now,
                    now,
                ),
            )
        return self.get(attempt_id) or {}

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM publish_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return self._payload(row) if row else None

    def list_attempts(self, *, job_name: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM publish_attempts"
        params: tuple[Any, ...] = ()
        if job_name:
            query += " WHERE job_name = ?"
            params = (job_name,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._payload(row) for row in rows]

    def transition(self, attempt_id: str, status: str, **updates: Any) -> dict[str, Any]:
        current = self.get(attempt_id)
        if current is None:
            raise ValueError("publish attempt not found")
        if status not in PUBLISH_TRANSITIONS.get(current["status"], set()):
            raise ValueError(f"invalid publish transition: {current['status']} -> {status}")
        values = {
            "remote_id": updates.get("remote_id", current["remote_id"]),
            "error": updates.get("error"),
            "retryable": bool(updates.get("retryable", False)),
            "action": updates.get("action"),
        }
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE publish_attempts
                SET status = ?, remote_id = ?, error = ?, retryable = ?, action = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    values["remote_id"],
                    str(values["error"])[:4000] if values["error"] else None,
                    1 if values["retryable"] else 0,
                    str(values["action"]) if values["action"] else None,
                    _now(),
                    attempt_id,
                ),
            )
        return self.get(attempt_id) or {}

    def record_progress(self, attempt_id: str, uploaded_bytes: int, *, upload_url: str | None = None) -> dict[str, Any]:
        current = self.get(attempt_id)
        if current is None:
            raise ValueError("publish attempt not found")
        uploaded = max(current["uploaded_bytes"], int(uploaded_bytes))
        if current["total_bytes"]:
            uploaded = min(uploaded, current["total_bytes"])
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE publish_attempts SET uploaded_bytes = ?, upload_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (uploaded, upload_url or current["upload_url"], _now(), attempt_id),
            )
        return self.get(attempt_id) or {}

    @staticmethod
    def _payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_name": row["job_name"],
            "provider": row["provider"],
            "status": row["status"],
            "credential_ref": row["credential_ref"],
            "payload": _decode(row["payload_json"], {}),
            "upload_url": row["upload_url"],
            "uploaded_bytes": int(row["uploaded_bytes"]),
            "total_bytes": int(row["total_bytes"]),
            "remote_id": row["remote_id"],
            "error": row["error"],
            "retryable": bool(row["retryable"]),
            "action": row["action"],
            "manual_package_path": row["manual_package_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class PublishService:
    def __init__(self, repository: PublishRepository, providers: dict[str, PublishProvider]):
        self.repository = repository
        self.providers = providers

    def run_attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.repository.get(attempt_id)
        if attempt is None:
            raise ValueError("publish attempt not found")
        provider = self.providers.get(attempt["provider"])
        if provider is None:
            return self._fail(attempt, RuntimeError("publish provider is unavailable"), retryable=False)
        try:
            attempt = self.repository.transition(attempt_id, "validating")
            provider.validate(attempt)
            attempt = self.repository.transition(attempt_id, "uploading")

            def progress(uploaded_bytes: int, upload_url: str | None = None) -> None:
                self.repository.record_progress(
                    attempt_id, uploaded_bytes, upload_url=upload_url
                )

            result = provider.upload(self.repository.get(attempt_id) or attempt, progress)
            remote_id = result.get("remote_id")
            status = "published" if result.get("status") == "published" else "processing"
            return self.repository.transition(attempt_id, status, remote_id=remote_id)
        except PermissionError as exc:
            return self._fail(self.repository.get(attempt_id) or attempt, exc, retryable=False)
        except Exception as exc:
            return self._fail(self.repository.get(attempt_id) or attempt, exc, retryable=True)

    def sync_attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.repository.get(attempt_id)
        if attempt is None:
            raise ValueError("publish attempt not found")
        provider = self.providers.get(attempt["provider"])
        if provider is None:
            return self._fail(attempt, RuntimeError("publish provider is unavailable"), retryable=False)
        try:
            result = provider.query(attempt)
            status = str(result.get("status") or "processing")
            if status == "published" and attempt["status"] == "processing":
                return self.repository.transition(
                    attempt_id, "published", remote_id=result.get("remote_id")
                )
            if status == "failed" and attempt["status"] == "processing":
                return self.repository.transition(
                    attempt_id,
                    "failed",
                    error=result.get("error") or "platform processing failed",
                    retryable=bool(result.get("retryable", False)),
                    action=self._fallback_action(attempt),
                )
            return attempt
        except Exception as exc:
            if attempt["status"] != "processing":
                raise
            return self._fail(attempt, exc, retryable=True)

    def _fail(self, attempt: dict[str, Any], error: Exception, *, retryable: bool) -> dict[str, Any]:
        if attempt["status"] == "draft":
            attempt = self.repository.transition(attempt["id"], "validating")
        if attempt["status"] == "failed":
            return attempt
        return self.repository.transition(
            attempt["id"],
            "failed",
            error=str(error),
            retryable=retryable,
            action=self._fallback_action(attempt),
        )

    @staticmethod
    def _fallback_action(attempt: dict[str, Any]) -> str:
        path = attempt.get("manual_package_path")
        return "open_manual_package" if path and Path(path).exists() else "open_publish_settings"
