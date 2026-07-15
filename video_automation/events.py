from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class ServerEvent:
    id: int
    type: str
    payload: dict[str, Any]


EVENT_BUFFER_SIZE = 500
_EVENTS: deque[ServerEvent] = deque(maxlen=EVENT_BUFFER_SIZE)
_CONDITION = threading.Condition()
_NEXT_ID = itertools.count(1)
_EVENT_DATABASE: Path | None = None
_DATABASE_LOCK = threading.Lock()


def configure_event_store(database_path: Path | str | None) -> None:
    global _EVENT_DATABASE  # noqa: PLW0603
    path = Path(database_path).resolve() if database_path is not None else None
    with _DATABASE_LOCK:
        _EVENT_DATABASE = path
        if path is not None:
            _ensure_database(path)


def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    event = _publish_database_event(event_type, payload)
    if event is None:
        event = ServerEvent(next(_NEXT_ID), event_type, payload)
    with _CONDITION:
        _EVENTS.append(event)
        _CONDITION.notify_all()


def current_event_id() -> int:
    database_id = _database_current_id()
    if database_id is not None:
        return database_id
    with _CONDITION:
        return _EVENTS[-1].id if _EVENTS else 0


def wait_for_events(last_id: int, *, timeout_seconds: float = 15.0) -> list[ServerEvent]:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    if _EVENT_DATABASE is not None:
        while True:
            events = _database_events_after(last_id)
            if events is not None and events:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            with _CONDITION:
                _CONDITION.wait(min(0.25, remaining))
    with _CONDITION:
        while not _events_after_locked(last_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            _CONDITION.wait(remaining)
        return _events_after_locked(last_id)


def _events_after_locked(last_id: int) -> list[ServerEvent]:
    return [event for event in _EVENTS if event.id > last_id]


def _ensure_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _database_connection(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS server_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_server_events_created ON server_events(created_at);
            """
        )


def _publish_database_event(event_type: str, payload: dict[str, Any]) -> ServerEvent | None:
    path = _EVENT_DATABASE
    if path is None:
        return None
    try:
        with _database_connection(path) as connection:
            connection.execute("PRAGMA busy_timeout = 10000")
            cursor = connection.execute(
                "INSERT INTO server_events(type, payload_json) VALUES(?, ?)",
                (str(event_type), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )
            event_id = int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM server_events WHERE id <= (SELECT MAX(id) - ? FROM server_events)",
                (EVENT_BUFFER_SIZE,),
            )
        return ServerEvent(event_id, str(event_type), payload)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _database_current_id() -> int | None:
    path = _EVENT_DATABASE
    if path is None:
        return None
    try:
        with _database_connection(path) as connection:
            row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM server_events").fetchone()
        return int(row[0]) if row else 0
    except (OSError, sqlite3.Error):
        return None


def _database_events_after(last_id: int) -> list[ServerEvent] | None:
    path = _EVENT_DATABASE
    if path is None:
        return None
    try:
        with _database_connection(path) as connection:
            rows = connection.execute(
                "SELECT id, type, payload_json FROM server_events WHERE id > ? ORDER BY id ASC",
                (int(last_id),),
            ).fetchall()
        return [
            ServerEvent(int(row[0]), str(row[1]), json.loads(row[2]))
            for row in rows
        ]
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


@contextmanager
def _database_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=10)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
