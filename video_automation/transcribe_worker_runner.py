from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import write_json_atomic
from .transcribe import create_funasr_model, transcribe_audio_funasr_with_model


def process_request(
    settings: Settings,
    model: Any,
    request: dict[str, Any],
    *,
    transcribe: Callable[..., None] = transcribe_audio_funasr_with_model,
) -> dict[str, Any]:
    response_path = Path(str(request["response_path"]))
    try:
        audio_path = Path(str(request["audio_path"]))
        job_dir = Path(str(request["job_dir"]))
        job_dir.mkdir(parents=True, exist_ok=True)
        transcribe(
            settings,
            model,
            audio_path,
            job_dir / "transcript.txt",
            job_dir / "transcript.srt",
            job_dir / "transcript.json",
        )
        payload = {"status": "ok"}
    except Exception as exc:
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
