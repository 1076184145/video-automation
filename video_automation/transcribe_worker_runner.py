from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import write_json_atomic
from .transcribe import create_funasr_model, transcribe_audio_funasr_with_model


def _write_heartbeat(request: dict[str, Any], phase: str, **details: Any) -> None:
    raw_path = request.get("heartbeat_path")
    if not raw_path:
        return
    payload = {
        "phase": phase,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **details,
    }
    write_json_atomic(Path(str(raw_path)), payload)


def process_request(
    settings: Settings,
    model: Any,
    request: dict[str, Any],
    *,
    transcribe: Callable[..., None] = transcribe_audio_funasr_with_model,
) -> dict[str, Any]:
    response_path = Path(str(request["response_path"]))
    try:
        if request.get("warmup"):
            _write_heartbeat(request, "model_ready")
            payload = {"status": "ok", "warmup": True}
            write_json_atomic(response_path, payload)
            return payload
        audio_path = Path(str(request["audio_path"]))
        job_dir = Path(str(request["job_dir"]))
        job_dir.mkdir(parents=True, exist_ok=True)
        _write_heartbeat(request, "transcribing", audio_path=str(audio_path))
        transcribe(
            settings,
            model,
            audio_path,
            job_dir / "transcript.txt",
            job_dir / "transcript.srt",
            job_dir / "transcript.json",
        )
        _write_heartbeat(request, "writing_outputs")
        payload = {"status": "ok"}
    except Exception as exc:
        _write_heartbeat(request, "failed", error_type=type(exc).__name__)
        payload = {"status": "error", "error": str(exc)}
    write_json_atomic(response_path, payload)
    return payload


def main() -> int:
    settings = Settings.load()
    model = create_funasr_model(settings)
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except ValueError:
            continue
        if not isinstance(request, dict) or not request.get("response_path"):
            continue
        process_request(settings, model, request)
    # GPU-backed libraries can crash during ordinary interpreter shutdown on
    # Windows. The parent treats EOF as a normal worker stop.
    os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
