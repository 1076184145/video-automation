from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


def generate_uvr_plan(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "uvr_plan.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached

    audio_hq_path = job_dir / "audio_hq.flac"
    configured = settings.uvr_path is not None
    payload: dict[str, Any] = {
        "status": "ready" if configured else "not_configured",
        "tool": "Ultimate Vocal Remover",
        "tool_path": str(settings.uvr_path) if settings.uvr_path else "",
        "input_audio": str(audio_hq_path),
        "recommended_outputs": {
            "vocals": str(job_dir / "uvr" / "vocals.wav"),
            "instrumental": str(job_dir / "uvr" / "instrumental.wav"),
        },
        "notes": [
            "This worker does not launch the UVR GUI automatically.",
            "Use this JSON as the integration contract for a future CLI-capable UVR step.",
            "Keep audio_hq.flac as the source for separation; do not use the 16 kHz Whisper audio.",
        ],
    }
    write_json_atomic(output_path, payload)
    return payload
