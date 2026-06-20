from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
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

    engine = settings.audio_separation_engine.strip().lower()
    if engine == "demucs":
        return _generate_demucs_separation(settings, job_dir, output_path, force=force)
    if engine in {"", "none", "plan", "uvr"}:
        return _write_plan_only_payload(settings, job_dir, output_path, engine=engine or "plan")
    return _write_payload(
        output_path,
        _base_payload(settings, job_dir, engine=engine)
        | {
            "status": "unsupported_engine",
            "error": f"Unsupported AUDIO_SEPARATION_ENGINE: {settings.audio_separation_engine}",
            "notes": [
                "Supported values are plan, none, uvr, and demucs.",
                "Set AUDIO_SEPARATION_ENGINE=demucs after installing Demucs to run native separation.",
            ],
        },
    )


def _write_plan_only_payload(settings: Settings, job_dir: Path, output_path: Path, *, engine: str) -> dict[str, Any]:
    configured = settings.uvr_path is not None
    payload = _base_payload(settings, job_dir, engine=engine) | {
        "status": "ready" if configured else "not_configured",
        "tool": "Ultimate Vocal Remover",
        "tool_path": str(settings.uvr_path) if settings.uvr_path else "",
        "recommended_outputs": _stable_outputs(job_dir),
        "notes": [
            "Plan-only mode does not launch the UVR GUI automatically.",
            "Set AUDIO_SEPARATION_ENGINE=demucs to run a CLI audio separation step inside this worker.",
            "Keep audio_hq.flac as the source for separation; do not use the 16 kHz Whisper audio.",
        ],
    }
    return _write_payload(output_path, payload)


def _generate_demucs_separation(settings: Settings, job_dir: Path, output_path: Path, *, force: bool) -> dict[str, Any]:
    audio_hq_path = job_dir / "audio_hq.flac"
    stable_outputs = _stable_outputs(job_dir)
    base = _base_payload(settings, job_dir, engine="demucs") | {
        "tool": "Demucs",
        "tool_path": str(settings.demucs_path),
        "model": settings.demucs_model,
        "device": settings.demucs_device,
        "outputs": stable_outputs,
        "started_at": _now(),
    }
    if not audio_hq_path.exists():
        return _write_payload(
            output_path,
            base
            | {
                "status": "missing_input",
                "error": "audio_hq.flac is missing; run extract_audio first.",
                "completed_at": _now(),
            },
        )

    executable = _resolve_tool(settings.demucs_path)
    if not executable:
        return _write_payload(
            output_path,
            base
            | {
                "status": "missing_tool",
                "error": "Demucs executable not found. Install demucs or set DEMUCS_PATH.",
                "completed_at": _now(),
                "notes": [
                    "Example install: .\\venv\\Scripts\\python.exe -m pip install demucs",
                    "Then set AUDIO_SEPARATION_ENGINE=demucs and DEMUCS_PATH=demucs.",
                ],
            },
        )

    output_dir = job_dir / "uvr"
    work_dir = output_dir / "demucs_work"
    if force and work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        executable,
        "--two-stems",
        "vocals",
        "-n",
        settings.demucs_model,
        "-o",
        str(work_dir),
    ]
    device = settings.demucs_device.strip()
    if device and device.lower() != "auto":
        command.extend(["-d", device])
    command.append(str(audio_hq_path))

    payload = base | {"status": "running", "command": command}
    write_json_atomic(output_path, payload)

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.audio_separation_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _write_payload(
            output_path,
            payload
            | {
                "status": "failed",
                "error": str(exc),
                "completed_at": _now(),
            },
        )

    vocals = _first_match(work_dir, "vocals.*")
    instrumental = _first_match(work_dir, "no_vocals.*")
    if vocals is not None:
        shutil.copy2(vocals, output_dir / "vocals.wav")
    if instrumental is not None:
        shutil.copy2(instrumental, output_dir / "instrumental.wav")

    ok = result.returncode == 0 and (output_dir / "vocals.wav").exists() and (output_dir / "instrumental.wav").exists()
    return _write_payload(
        output_path,
        payload
        | {
            "status": "done" if ok else "failed",
            "returncode": result.returncode,
            "completed_at": _now(),
            "output_tail": _tail(result.stdout or ""),
            "outputs": _stable_outputs(job_dir),
            "error": "" if ok else "Demucs did not produce both vocals.wav and instrumental.wav.",
        },
    )


def _base_payload(settings: Settings, job_dir: Path, *, engine: str) -> dict[str, Any]:
    return {
        "engine": engine,
        "input_audio": str(job_dir / "audio_hq.flac"),
        "recommended_outputs": _stable_outputs(job_dir),
        "timeout_seconds": settings.audio_separation_timeout_seconds,
    }


def _stable_outputs(job_dir: Path) -> dict[str, str]:
    output_dir = job_dir / "uvr"
    return {
        "vocals": str(output_dir / "vocals.wav"),
        "instrumental": str(output_dir / "instrumental.wav"),
    }


def _write_payload(output_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    write_json_atomic(output_path, payload)
    return payload


def _resolve_tool(path: Path) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return str(candidate)
    found = shutil.which(raw)
    return found or ""


def _first_match(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.rglob(pattern), key=lambda path: len(path.parts), reverse=True)
    return matches[0] if matches else None


def _tail(text: str, limit: int = 8000) -> str:
    return text[-limit:] if len(text) > limit else text


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
