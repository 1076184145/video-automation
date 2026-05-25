from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(12):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    try:
        tmp_path.unlink(missing_ok=True)
    finally:
        if last_error is not None:
            raise last_error


def valid_json_file(path: Path) -> bool:
    return read_json_file(path) is not None
