from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .io_utils import write_json_atomic
from .media import MEDIA_EXTENSIONS


DOWNLOAD_LOCK = threading.Lock()
PROGRESS_RE = re.compile(r"\[download\]\s+([0-9.]+)%")


def list_downloads(settings: Settings) -> dict[str, Any]:
    return _read_state(settings)


def start_download(settings: Settings, url: str) -> dict[str, Any]:
    if not settings.download_enabled:
        raise RuntimeError("DOWNLOAD_ENABLED is false")
    clean_url = url.strip()
    if len(clean_url) > 2048:
        raise RuntimeError("download URL is too long")
    if any(ord(char) < 32 for char in clean_url):
        raise RuntimeError("download URL contains invalid control characters")
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("download url must be http or https")
    record = {
        "id": _download_id(clean_url),
        "url": clean_url,
        "status": "queued",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "output_path": "",
        "error": "",
    }
    with DOWNLOAD_LOCK:
        state = _read_state(settings)
        existing = _find_download(state, record["id"])
        if existing and existing.get("status") in {"queued", "downloading"}:
            return existing
        if existing:
            state["downloads"] = [item for item in state["downloads"] if item.get("id") != record["id"]]
        state["downloads"].insert(0, record)
        _write_state(settings, state)
    thread = threading.Thread(target=_run_download, args=(settings, record["id"], clean_url), daemon=True)
    thread.start()
    return record


def get_download(settings: Settings, download_id: str) -> dict[str, Any] | None:
    return _find_download(_read_state(settings), download_id)


def _run_download(settings: Settings, download_id: str, url: str) -> None:
    root = settings.input_downloads_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    before = _media_files(root)
    _update_download(settings, download_id, status="downloading", progress=0.0)
    command = [
        str(settings.ytdlp_path),
        "--newline",
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-P",
        str(root),
        "-o",
        "%(title).80s-%(id)s.%(ext)s",
        url,
    ]
    if settings.ffmpeg_path.is_file():
        command.extend(["--ffmpeg-location", str(settings.ffmpeg_path.parent)])
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        _update_download(settings, download_id, status="failed", error=str(exc))
        return
    tail: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        text = line.strip()
        if text:
            tail.append(text)
            tail = tail[-20:]
        match = PROGRESS_RE.search(text)
        if match:
            _update_download(settings, download_id, progress=round(float(match.group(1)), 2), message=text)
    returncode = process.wait()
    if returncode != 0:
        _update_download(settings, download_id, status="failed", error="\n".join(tail[-8:]) or f"yt-dlp exited with {returncode}")
        return
    output = _newest_media(root, before)
    if output is None:
        _update_download(settings, download_id, status="failed", error="download finished but no media file was found")
        return
    _update_download(
        settings,
        download_id,
        status="done",
        progress=100.0,
        output_path=str(output),
        output_name=output.name,
        size_bytes=output.stat().st_size,
    )


def _download_id(url: str) -> str:
    now = datetime.now().strftime("%Y%m%d%H%M%S%f")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{now}-{digest}"


def _state_path(settings: Settings) -> Path:
    return settings.input_downloads_dir / "downloads.json"


def _read_state(settings: Settings) -> dict[str, Any]:
    try:
        payload = json.loads(_state_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    downloads = payload.get("downloads", []) if isinstance(payload, dict) else []
    return {"enabled": settings.download_enabled, "downloads": downloads if isinstance(downloads, list) else []}


def _write_state(settings: Settings, state: dict[str, Any]) -> None:
    payload = {"enabled": settings.download_enabled, "downloads": state.get("downloads", [])}
    write_json_atomic(_state_path(settings), payload)


def _find_download(state: dict[str, Any], download_id: str) -> dict[str, Any] | None:
    for item in state.get("downloads", []):
        if isinstance(item, dict) and item.get("id") == download_id:
            return item
    return None


def _update_download(settings: Settings, download_id: str, **updates: Any) -> None:
    with DOWNLOAD_LOCK:
        state = _read_state(settings)
        for item in state["downloads"]:
            if item.get("id") != download_id:
                continue
            item.update(updates)
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            break
        _write_state(settings, state)


def _media_files(root: Path) -> set[Path]:
    return {path.resolve() for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS}


def _newest_media(root: Path, before: set[Path]) -> Path | None:
    candidates = [path for path in _media_files(root) if path not in before]
    if not candidates:
        candidates = list(_media_files(root))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
