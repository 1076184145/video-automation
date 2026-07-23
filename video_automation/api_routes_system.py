from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from .api_job_utils import bounded_text, safe_int
from .api_routes_base import CHUNK_SIZE
from .api_settings import (
    CredentialUpdateError,
    apply_settings_updates,
    migrate_legacy_secrets,
    normalize_env_updates,
)
from .api_system import (
    health_response,
    recording_upload_path,
    run_tools_install,
    set_tools_install_state,
    tools_install_snapshot,
)
from .config import Settings
from .events import publish_event
from .health import clear_health_cache, health_payload
from .jobs import Job, create_job, normalize_source_path
from .library_api import attach_job_context, automation_repository_for
from .routing import RouteMatch
from .runtime_config import snapshot_runtime_settings


TERMINAL_STATUSES = frozenset({"needs_review", "done", "failed", "canceled"})


class SystemRoutes:
    """Settings, health tooling, uploads, and new-job submission routes."""

    def _route_update_settings(self, _matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        payload = self._read_json()
        if payload is None:
            return
        raw_updates = payload.get("env")
        if not isinstance(raw_updates, dict):
            self._json({"error": "env must be an object"}, status=400)
            return
        try:
            updates = normalize_env_updates(raw_updates)
            changed = apply_settings_updates(settings.root, updates)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=400)
            return
        except CredentialUpdateError as exc:
            self._json(
                {
                    "error": {
                        "code": "credential_store_unavailable",
                        "message": f"Unable to update secure credentials: {exc}",
                    }
                },
                status=503,
            )
            return
        except OSError as exc:
            self._json({"error": f"Unable to update .env: {exc}"}, status=500)
            return
        updated_settings = Settings.load()
        self.api_context.replace_settings(updated_settings)
        clear_health_cache()
        publish_event("settings", {"changed": sorted(changed)})
        response = health_payload(updated_settings)
        response["changed"] = sorted(changed)
        self._json(response)

    def _route_migrate_settings_secrets(self, _matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        try:
            migrated = migrate_legacy_secrets(settings.root)
        except CredentialUpdateError as exc:
            self._json(
                {
                    "error": {
                        "code": "credential_store_unavailable",
                        "message": f"Unable to migrate secure credentials: {exc}",
                    }
                },
                status=503,
            )
            return
        updated_settings = Settings.load()
        self.api_context.replace_settings(updated_settings)
        clear_health_cache()
        response = health_response(updated_settings)
        response["migrated_secret_keys"] = sorted(migrated)
        publish_event("settings", {"migrated_secret_keys": sorted(migrated)})
        self._json(response)

    def _route_install_tools(self, _matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        payload = self._read_json()
        if payload is None:
            return
        state = tools_install_snapshot()
        if state.get("status") == "running":
            self._json(
                {"error": "tool installation is already running", "tools_install": state},
                status=409,
            )
            return
        script = settings.root / "tools" / "install_desktop_tools.ps1"
        if not script.is_file():
            self._json({"error": "install_desktop_tools.ps1 was not found"}, status=400)
            return
        install_ffmpeg = bool(payload.get("install_ffmpeg", True))
        if not install_ffmpeg:
            self._json({"error": "nothing selected to install"}, status=400)
            return
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
        if bool(payload.get("force", False)):
            command.append("-Force")
        set_tools_install_state(
            status="running",
            started_at=datetime.now().isoformat(timespec="seconds"),
            completed_at="",
            failed_at="",
            message="Starting tool installation",
            returncode=None,
            log_tail=[],
        )
        try:
            thread = threading.Thread(
                target=run_tools_install,
                args=(settings, command),
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            set_tools_install_state(
                status="failed",
                failed_at=datetime.now().isoformat(timespec="seconds"),
                message=str(exc),
            )
            self._json(
                {"error": str(exc), "tools_install": tools_install_snapshot()},
                status=500,
            )
            return
        self._json({"tools_install": tools_install_snapshot()}, status=202)

    def _route_process_one(self, _matched: RouteMatch, _query: str) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            job, status, queue_item = self._submit_process_payload(payload)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=400)
            return
        response = self._job_payload(job)
        if queue_item:
            response["queue"] = queue_item
        self._json(response, status=status)

    def _route_process_batch(self, _matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        payload = self._read_json()
        if payload is None:
            return
        raw_items = payload.get("items")
        if raw_items is None:
            raw_paths = payload.get("paths") or []
            raw_items = [{"path": path} for path in raw_paths]
        if not isinstance(raw_items, list) or not raw_items:
            self._json({"error": "items must be a non-empty list"}, status=400)
            return
        if len(raw_items) > settings.api_batch_limit:
            self._json(
                {"error": f"batch is limited to {settings.api_batch_limit} items"},
                status=400,
            )
            return
        batch_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        batch_size = len(raw_items)
        jobs: list[dict[str, Any]] = []
        for batch_index, raw_item in enumerate(raw_items, start=1):
            if isinstance(raw_item, str):
                item_payload = dict(payload)
                item_payload["path"] = raw_item
            elif isinstance(raw_item, dict):
                item_payload = {**payload, **raw_item}
            else:
                self._json(
                    {"error": "each batch item must be an object or path string"},
                    status=400,
                )
                return
            item_payload.pop("items", None)
            item_payload.pop("paths", None)
            item_payload["batch_id"] = batch_id
            item_payload["batch_index"] = batch_index
            item_payload["batch_size"] = batch_size
            try:
                job, status, queue_item = self._submit_process_payload(item_payload)
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            jobs.append(
                {
                    **self._job_payload(job),
                    "http_status": status,
                    "queue": queue_item,
                }
            )
        self._json(
            {
                "batch_id": batch_id,
                "jobs": jobs,
                "count": len(jobs),
                "parallel_jobs": settings.api_parallel_jobs,
            },
            status=202,
        )

    def _submit_process_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[Job, int, dict[str, Any] | None]:
        settings = self.api_context.settings
        queue_repository = self.api_context.queue_repository
        source = payload.get("path") or payload.get("source_path")
        if not source:
            raise ValueError("missing path")
        try:
            job = create_job(
                settings,
                normalize_source_path(str(source)),
                force=bool(payload.get("force", False)),
                batch_id=bounded_text(payload.get("batch_id"), 80) or None,
                batch_index=safe_int(payload.get("batch_index")),
                batch_size=safe_int(payload.get("batch_size")),
            )
        except OSError as exc:
            raise ValueError(str(exc)) from exc
        attach_job_context(settings, job, payload)
        if job.status in TERMINAL_STATUSES and not bool(payload.get("force", False)):
            return job, 200, queue_repository.get_by_job(job.job_dir.name)
        if job.status != "pending" and not bool(payload.get("force", False)):
            return job, 202, queue_repository.get_by_job(job.job_dir.name)
        job.set_status("queued")
        queued_payload = dict(payload)
        queued_payload["path"] = str(job.source_path)
        queued_payload["_runtime_settings_snapshot"] = snapshot_runtime_settings(settings)
        recipe_id = str(queued_payload.get("recipe_id") or "").strip()
        if recipe_id:
            recipe = automation_repository_for(settings).get_recipe(recipe_id)
            if recipe is None:
                raise ValueError(f"recipe not found: {recipe_id}")
            queued_payload["_recipe_snapshot"] = recipe
        queue_item = queue_repository.enqueue(
            job.job_dir.name,
            queued_payload,
            priority=int(payload.get("priority") or 0),
        )
        return job, 202, queue_item

    def _route_upload_recording(self, _matched: RouteMatch, query: str) -> None:
        settings = self.api_context.settings
        params = parse_qs(query)
        filename = (params.get("filename") or [""])[0]
        if not filename:
            self._json({"error": "missing filename"}, status=400)
            return
        try:
            target = recording_upload_path(settings, filename)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=400)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._json({"error": "invalid Content-Length"}, status=400)
            return
        if length <= 0:
            self._json({"error": "empty upload"}, status=400)
            return
        if settings.recording_upload_max_bytes > 0 and length > settings.recording_upload_max_bytes:
            self._json(
                {
                    "error": (
                        "upload exceeds RECORDING_UPLOAD_MAX_BYTES "
                        f"({settings.recording_upload_max_bytes})"
                    ),
                    "max_bytes": settings.recording_upload_max_bytes,
                },
                status=413,
            )
            return
        settings.input_recordings_dir.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f".{target.name}.uploading")
        remaining = length
        try:
            with temp_path.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise OSError("upload interrupted")
                    handle.write(chunk)
                    remaining -= len(chunk)
            temp_path.replace(target)
        except OSError as exc:
            try:
                temp_path.unlink()
            except OSError:
                pass
            self._json({"error": str(exc)}, status=500)
            return
        stat = target.stat()
        self._json(
            {
                "name": target.name,
                "path": str(target.resolve()),
                "relative_path": str(
                    target.relative_to(settings.input_recordings_dir.resolve())
                ),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            },
            status=201,
        )
