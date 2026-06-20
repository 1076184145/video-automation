from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServerEvent:
    id: int
    type: str
    payload: dict[str, Any]


EVENT_BUFFER_SIZE = 500
_EVENTS: deque[ServerEvent] = deque(maxlen=EVENT_BUFFER_SIZE)
_CONDITION = threading.Condition()
_NEXT_ID = itertools.count(1)


def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    event = ServerEvent(next(_NEXT_ID), event_type, payload)
    with _CONDITION:
        _EVENTS.append(event)
        _CONDITION.notify_all()


def current_event_id() -> int:
    with _CONDITION:
        return _EVENTS[-1].id if _EVENTS else 0


def wait_for_events(last_id: int, *, timeout_seconds: float = 15.0) -> list[ServerEvent]:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    with _CONDITION:
        while not _events_after_locked(last_id):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            _CONDITION.wait(remaining)
        return _events_after_locked(last_id)


def _events_after_locked(last_id: int) -> list[ServerEvent]:
    return [event for event in _EVENTS if event.id > last_id]
