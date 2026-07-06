from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PreferenceRepository:
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
                    CREATE TABLE IF NOT EXISTS preference_events (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        job_name TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_preference_events_kind
                    ON preference_events(kind, created_at DESC);
                    """
                )
            self._initialized = True

    def record(self, kind: str, payload: dict[str, Any], *, job_name: str | None = None) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip()
        if not normalized_kind:
            raise ValueError("preference event kind is required")
        if not isinstance(payload, dict):
            raise ValueError("preference event payload must be an object")
        event = {
            "id": f"preference_{uuid.uuid4().hex}",
            "kind": normalized_kind,
            "job_name": str(job_name).strip() if job_name else None,
            "payload": payload,
            "created_at": _now(),
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO preference_events(id, kind, job_name, payload_json, created_at) VALUES(?, ?, ?, ?, ?)",
                (
                    event["id"], event["kind"], event["job_name"],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    event["created_at"],
                ),
            )
        return event

    def events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM preference_events ORDER BY created_at ASC, id ASC"
            ).fetchall()
        values = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except ValueError:
                payload = {}
            values.append({
                "id": row["id"], "kind": row["kind"], "job_name": row["job_name"],
                "payload": payload if isinstance(payload, dict) else {}, "created_at": row["created_at"],
            })
        return values

    def summary(self) -> dict[str, Any]:
        clip_feedback = {"accepted": 0, "rejected": 0, "cleared": 0}
        replacements: dict[str, str] = {}
        platforms: dict[str, int] = {}
        events = self.events()
        for event in events:
            payload = event["payload"]
            if event["kind"] == "clip_feedback":
                action = str(payload.get("action") or "").strip()
                key = "cleared" if action == "clear" else action
                if key in clip_feedback:
                    clip_feedback[key] += 1
            elif event["kind"] == "subtitle_correction":
                before = str(payload.get("before") or "").strip()
                after = str(payload.get("after") or "").strip()
                if before and after and before != after:
                    replacements[before] = after
            elif event["kind"] == "publish_selection":
                platform = str(payload.get("platform") or "").strip()
                if platform:
                    platforms[platform] = platforms.get(platform, 0) + 1
        return {
            "event_count": len(events),
            "clip_feedback": clip_feedback,
            "subtitle_replacements": replacements,
            "platforms": platforms,
        }

    def export(self) -> dict[str, Any]:
        return {"version": 1, "exported_at": _now(), "summary": self.summary(), "events": self.events()}

    def clear(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM preference_events")
        return cursor.rowcount
