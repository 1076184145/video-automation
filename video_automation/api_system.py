from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .config import Settings
from .events import publish_event
from .health import clear_health_cache, health_payload
from .io_utils import read_json_file, write_json_atomic
from .jobs import list_jobs
from .media import MEDIA_EXTENSIONS
from .process_tree import (
    attach_process_tree,
    process_group_popen_kwargs,
    release_process_tree,
    terminate_process_tree,
)


TOOLS_INSTALL_LOCK = threading.Lock()
TOOLS_INSTALL_STATE: dict[str, Any] = {"status": "idle", "message": "", "log_tail": []}


def health_response(settings: Settings) -> dict[str, Any]:
    payload = health_payload(settings)
    payload["tools_install"] = tools_install_snapshot()
    return payload


def publish_package_queue(settings: Settings) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for job in list_jobs(settings):
        package_path = job.job_dir / "publish_package.json"
        if not package_path.exists():
            continue
        package = read_json_file(package_path)
        if not isinstance(package, dict):
            continue
        extension_manifest = read_json_file(job.job_dir / "publish_extension_manifest.json") or {}
        items.append({
            "job": job.to_dict(),
            "status": package.get("status", "ready"),
            "generated_at": package.get("generated_at", ""),
            "source_video": package.get("source_video", {}),
            "covers": package.get("covers", []),
            "platforms": package.get("platforms", []),
            "publish_extension": package.get("publish_extension", {}),
            "extension_manifest": extension_manifest if isinstance(extension_manifest, dict) else {},
        })
    return {
        "status": "ready",
        "count": len(items),
        "items": items,
        "notes": [
            "This endpoint lists local publish handoff packages for trusted browser extensions.",
            "It does not contain platform credentials and does not upload automatically.",
        ],
    }


def tools_install_snapshot() -> dict[str, Any]:
    with TOOLS_INSTALL_LOCK:
        snapshot = dict(TOOLS_INSTALL_STATE)
        snapshot["log_tail"] = list(TOOLS_INSTALL_STATE.get("log_tail") or [])
        return snapshot


def schedule_tombstone_cleanup(
    path: Path,
    *,
    attempts: int = 60,
    delay_seconds: float = 1.0,
) -> None:
    def cleanup() -> None:
        for _ in range(max(1, attempts)):
            try:
                shutil.rmtree(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(max(0.05, delay_seconds))

    threading.Thread(
        target=cleanup,
        name=f"job-cleanup-{path.name[-12:]}",
        daemon=True,
    ).start()


def resume_tombstone_cleanup(jobs_dir: Path) -> None:
    if not jobs_dir.exists():
        return
    for path in jobs_dir.glob(".*.deleting-*"):
        if path.is_dir():
            schedule_tombstone_cleanup(path)


def set_tools_install_state(**updates: Any) -> dict[str, Any]:
    with TOOLS_INSTALL_LOCK:
        if "log_append" in updates:
            line = str(updates.pop("log_append") or "").strip()
            if line:
                tail = list(TOOLS_INSTALL_STATE.get("log_tail") or [])
                tail.append(line)
                TOOLS_INSTALL_STATE["log_tail"] = tail[-80:]
                TOOLS_INSTALL_STATE["message"] = line
        for key, value in updates.items():
            if key == "log_tail":
                TOOLS_INSTALL_STATE[key] = list(value or [])[-80:]
            else:
                TOOLS_INSTALL_STATE[key] = value
        snapshot = dict(TOOLS_INSTALL_STATE)
        snapshot["log_tail"] = list(TOOLS_INSTALL_STATE.get("log_tail") or [])
    publish_event("tools_install", snapshot)
    return snapshot


def run_tools_install(settings: Settings, command: list[str]) -> None:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **process_group_popen_kwargs(),
        )
        attach_process_tree(process)
    except Exception as exc:
        if process is not None:
            terminate_process_tree(process)
        set_tools_install_state(
            status="failed",
            failed_at=datetime.now().isoformat(timespec="seconds"),
            message=str(exc),
        )
        return

    try:
        if process.stdout is not None:
            for line in process.stdout:
                set_tools_install_state(log_append=line)
        returncode = process.wait()
    except Exception as exc:
        terminate_process_tree(process)
        set_tools_install_state(
            status="failed",
            failed_at=datetime.now().isoformat(timespec="seconds"),
            message=f"Tool installation interrupted: {exc}",
        )
        return
    finally:
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        release_process_tree(process)
    if returncode == 0:
        clear_health_cache()
        set_tools_install_state(
            status="done",
            completed_at=datetime.now().isoformat(timespec="seconds"),
            returncode=returncode,
            message="Tool installation finished",
        )
        publish_event("health", health_response(Settings.load()))
        return
    set_tools_install_state(
        status="failed",
        failed_at=datetime.now().isoformat(timespec="seconds"),
        returncode=returncode,
        message=f"Tool installation failed with exit code {returncode}",
    )


def recording_files(settings: Settings) -> list[dict[str, Any]]:
    root = settings.input_recordings_dir.resolve()
    if not root.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if ".deleted_recordings" in path.relative_to(root).parts:
            continue
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        try:
            stat = path.stat()
            relative = str(path.relative_to(root))
        except OSError:
            continue
        files.append({
            "name": path.name,
            "relative_path": relative,
            "path": str(path.resolve()),
            "size_bytes": stat.st_size,
            "modified_at": int(stat.st_mtime),
        })
    return sorted(files, key=lambda item: item["modified_at"], reverse=True)[:200]


def delete_recording(settings: Settings, relative_path: str) -> dict[str, Any]:
    """Move an unreferenced recording into a private, recoverable holding area."""
    root = settings.input_recordings_dir.resolve()
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute() or ".." in relative.parts or ".deleted_recordings" in relative.parts:
        raise ValueError("invalid recording path")
    candidate = root / relative
    target = candidate.resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("recording must be inside recordings directory")
    if any(part.is_symlink() for part in [candidate, *candidate.parents] if part != root and part.is_relative_to(root)):
        raise ValueError("symbolic links cannot be deleted here")
    if not target.is_file():
        raise FileNotFoundError("recording not found")
    if target.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("not a supported recording")
    if any(job.source_path.resolve() == target for job in list_jobs(settings)):
        raise RuntimeError("recording is referenced by a job; remove the job first")
    trash = root / ".deleted_recordings"
    if trash.is_symlink() or trash.resolve() != trash:
        raise ValueError("invalid recording recovery directory")
    trash.mkdir(exist_ok=True)
    recovery_dir = Path(tempfile.mkdtemp(prefix="recording-", dir=trash))
    # No media extension: recursive watchers must not treat deleted files as input.
    recovery_file = recovery_dir / "payload.deleted"
    write_json_atomic(recovery_dir / "restore.json", {"original_path": str(target), "relative_path": str(relative)})
    target.rename(recovery_file)
    return {"status": "deleted", "recovery_path": str(recovery_file), "original_path": str(target)}


def recording_upload_path(settings: Settings, filename: str) -> Path:
    raw_name = Path(unquote(filename)).name.strip()
    if not raw_name:
        raise ValueError("invalid filename")
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
    if not safe_name:
        raise ValueError("invalid filename")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in MEDIA_EXTENSIONS:
        raise ValueError(f"unsupported media type: {suffix or 'none'}")
    root = settings.input_recordings_dir.resolve()
    target = (root / safe_name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("invalid upload path") from exc
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 1000):
        candidate = (root / f"{stem}-{index}{suffix}").resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("invalid upload path") from exc
        if not candidate.exists():
            return candidate
    raise ValueError("too many duplicate filenames")
