from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .api_security import require_safe_api_binding
from .api_settings import (
    CredentialUpdateError,
    apply_settings_updates,
    migrate_legacy_secrets,
    normalize_env_updates as _normalize_env_updates,
    update_env_file as _update_env_file,
)
from .config import Settings
from .covers import cover_manifest, generate_cover_candidates, mark_cover_generation_started, normalize_cover_options, select_cover
from .cuts import update_cuts_from_editor
from .events import configure_event_store, current_event_id, publish_event, wait_for_events
from .hooks import generate_uvr_plan
from .health import clear_health_cache, health_payload
from .highlight_cut import generate_highlight_cut
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .jobs import Job, create_job, list_jobs, load_job, normalize_source_path
from .library_api import (
    attach_job_context,
    delete_job_records,
    automation_repository_for,
    dispatch_library_request,
    job_library_fields,
    job_library_fields_map,
    library_database_path,
    queue_repository_for,
    record_job_revision,
    evaluate_job_quality,
    preference_repository_for,
    structured_error,
)
from .llm_tools import generate_highlights, generate_metadata, save_metadata
from .media import MEDIA_EXTENSIONS
from .pipeline_spec import PIPELINE_STAGE_SPECS
from .publish import generate_publish_package
from .profiles import apply_profile_flags, apply_profile_settings
from .project_exports import generate_project_exports
from .render import generate_render_preview, render_final_video, render_highlight_video
from .segments import generate_platform_segments
from .subtitle_translation import translate_subtitles, translated_clipped_ass_name, translated_final_video_name
from .subtitles import generate_ass_subtitles, generate_clipped_ass_subtitles
from .queue_worker import QueueWorkerProcess, process_is_alive
from .routing import CORE_ROUTER
from .runtime_config import apply_runtime_settings_snapshot, snapshot_runtime_settings
from .stage_runs import StageRunRepository
from .task_queue import QueueControlRequested
from .recovery import backup_database, ensure_database_ready, ensure_job_capacity
from .transcribe import warm_transcription_backend
from .worker import process_job

mimetypes.add_type("font/woff2", ".woff2")

CHUNK_SIZE = 1024 * 1024
MAX_JSON_BODY_SIZE = 2 * 1024 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
TERMINAL_STATUSES = {"needs_review", "done", "failed", "canceled"}
RERUN_STATUS = {name: spec.status for name, spec in PIPELINE_STAGE_SPECS.items()}
TOOLS_INSTALL_LOCK = threading.Lock()
TOOLS_INSTALL_STATE: dict[str, Any] = {"status": "idle", "message": "", "log_tail": []}


class AutomationHTTPServer(ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can allow multiple live processes to bind the
    # same port and split incoming requests between different code versions.
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        queue_worker: QueueWorkerProcess | None,
    ):
        self.queue_worker = queue_worker
        super().__init__(server_address, handler)

    def server_close(self) -> None:
        if self.queue_worker is not None:
            self.queue_worker.stop()
        super().server_close()


def create_server(settings: Settings, *, start_queue_worker: bool = True) -> ThreadingHTTPServer:
    require_safe_api_binding(settings)
    database_path = library_database_path(settings)
    ensure_database_ready(database_path)
    configure_event_store(database_path)
    handler = _handler_class(settings)
    if database_path.is_file():
        backup_database(database_path, keep=5)
    _resume_tombstone_cleanup(settings.jobs_dir)
    queue_worker = QueueWorkerProcess(settings) if start_queue_worker else None
    server = AutomationHTTPServer((settings.api_host, settings.api_port), handler, queue_worker)
    if queue_worker is not None:
        try:
            queue_worker.start()
        except Exception:
            server.server_close()
            raise
    return server


def serve(settings: Settings) -> None:
    server = create_server(settings)
    print(f"Video Automation API listening on http://{settings.api_host}:{settings.api_port}", flush=True)
    server.serve_forever()


def _start_transcription_warmup(settings: Settings) -> None:
    if settings.whisper_backend not in {"funasr", "funasr-whisper", "funasr-faster-whisper"}:
        return
    if not settings.funasr_persistent_worker:
        return
    threading.Thread(target=warm_transcription_backend, args=(settings,), daemon=True).start()


def _handler_class(settings: Settings) -> type[BaseHTTPRequestHandler]:
    allowed_origins = _allowed_api_origins(settings)
    queue_repository = queue_repository_for(settings)

    class Handler(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
            super().end_headers()

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._require_allowed_origin():
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_allowed_origin():
                return
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/v1/"):
                response = dispatch_library_request(settings, "GET", unquote(parsed.path))
                if response is not None:
                    status, payload = response
                    self._json(payload, status=status)
                    return
            if not self._dispatch_core_route("GET", parsed.path, parsed.query):
                self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            nonlocal settings, allowed_origins
            if not self._require_allowed_origin():
                return
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/v1/"):
                payload = self._read_json()
                if payload is None:
                    return
                response = dispatch_library_request(settings, "POST", unquote(parsed.path), payload)
                if response is not None:
                    status, body = response
                    self._json(body, status=status)
                    return
            if not self._dispatch_core_route("POST", parsed.path, parsed.query):
                self._json({"error": "not found"}, status=404)

        def _update_settings(self) -> None:
            nonlocal settings, allowed_origins
            payload = self._read_json()
            if payload is None:
                return
            raw_updates = payload.get("env")
            if not isinstance(raw_updates, dict):
                self._json({"error": "env must be an object"}, status=400)
                return
            try:
                updates = _normalize_env_updates(raw_updates)
                changed = apply_settings_updates(settings.root, updates)
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            except CredentialUpdateError as exc:
                self._json({
                    "error": {
                        "code": "credential_store_unavailable",
                        "message": f"Unable to update secure credentials: {exc}",
                    }
                }, status=503)
                return
            except OSError as exc:
                self._json({"error": f"Unable to update .env: {exc}"}, status=500)
                return
            settings = Settings.load()
            allowed_origins = _allowed_api_origins(settings)
            clear_health_cache()
            publish_event("settings", {"changed": sorted(changed)})
            response = health_payload(settings)
            response["changed"] = sorted(changed)
            self._json(response)

        def _migrate_settings_secrets(self) -> None:
            nonlocal settings
            try:
                migrated = migrate_legacy_secrets(settings.root)
            except CredentialUpdateError as exc:
                self._json({
                    "error": {
                        "code": "credential_store_unavailable",
                        "message": f"Unable to migrate secure credentials: {exc}",
                    }
                }, status=503)
                return
            settings = Settings.load()
            clear_health_cache()
            response = _health_response(settings)
            response["migrated_secret_keys"] = sorted(migrated)
            publish_event("settings", {"migrated_secret_keys": sorted(migrated)})
            self._json(response)

        def _install_health_tools(self) -> None:
            payload = self._read_json()
            if payload is None:
                return
            state = _tools_install_snapshot()
            if state.get("status") == "running":
                self._json({"error": "tool installation is already running", "tools_install": state}, status=409)
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
            if not install_ffmpeg:
                command.append("-SkipFfmpeg")
            _set_tools_install_state(
                status="running",
                started_at=datetime.now().isoformat(timespec="seconds"),
                completed_at="",
                failed_at="",
                message="Starting tool installation",
                returncode=None,
                log_tail=[],
            )
            try:
                thread = threading.Thread(target=_run_tools_install, args=(settings, command), daemon=True)
                thread.start()
            except Exception as exc:
                _set_tools_install_state(
                    status="failed",
                    failed_at=datetime.now().isoformat(timespec="seconds"),
                    message=str(exc),
                )
                self._json({"error": str(exc), "tools_install": _tools_install_snapshot()}, status=500)
                return
            self._json({"tools_install": _tools_install_snapshot()}, status=202)

        def _process_one(self) -> None:
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

        def _process_batch(self) -> None:
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
                self._json({"error": f"batch is limited to {settings.api_batch_limit} items"}, status=400)
                return
            batch_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            batch_size = len(raw_items)
            jobs = []
            for batch_index, raw_item in enumerate(raw_items, start=1):
                if isinstance(raw_item, str):
                    item_payload = dict(payload)
                    item_payload["path"] = raw_item
                elif isinstance(raw_item, dict):
                    item_payload = {**payload, **raw_item}
                else:
                    self._json({"error": "each batch item must be an object or path string"}, status=400)
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
                jobs.append({
                    **self._job_payload(job),
                    "http_status": status,
                    "queue": queue_item,
                })
            self._json({
                "batch_id": batch_id,
                "jobs": jobs,
                "count": len(jobs),
                "parallel_jobs": settings.api_parallel_jobs,
            }, status=202)

        def _submit_process_payload(
            self,
            payload: dict[str, Any],
        ) -> tuple[Job, int, dict[str, Any] | None]:
            source = payload.get("path") or payload.get("source_path")
            if not source:
                raise ValueError("missing path")
            try:
                job = create_job(
                    settings,
                    normalize_source_path(str(source)),
                    force=bool(payload.get("force", False)),
                    batch_id=_bounded_text(payload.get("batch_id"), 80) or None,
                    batch_index=_safe_int(payload.get("batch_index")),
                    batch_size=_safe_int(payload.get("batch_size")),
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

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_allowed_origin():
                return
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/v1/"):
                response = dispatch_library_request(settings, "DELETE", unquote(parsed.path))
                if response is not None:
                    status, payload = response
                    self._json(payload, status=status)
                    return
            if not self._dispatch_core_route("DELETE", parsed.path, parsed.query):
                self._json({"error": "not found"}, status=404)

        def _dispatch_core_route(self, method: str, path: str, query: str) -> bool:
            matched = CORE_ROUTER.resolve(method, path)
            if matched is None:
                return False
            job_name = matched.params.get("job_name", "")
            handlers: dict[str, Callable[[], None]] = {
                "root": lambda: self._send_static_file("index.html"),
                "static_file": lambda: self._send_static_file(matched.params["asset_path"]),
                "health": self._send_health,
                "recordings": lambda: self._json(_recording_files(settings)),
                "publish_packages": lambda: self._json(_publish_package_queue(settings)),
                "events": lambda: self._send_events(query),
                "jobs": self._send_jobs,
                "job_file": lambda: self._send_job_file(job_name, matched.params["filename"], query),
                "job": lambda: self._send_job(job_name),
                "install_tools": self._install_health_tools,
                "update_settings": self._update_settings,
                "migrate_settings_secrets": self._migrate_settings_secrets,
                "upload_recording": lambda: self._upload_recording(query),
                "approve_job": lambda: self._approve_job(job_name),
                "cancel_job": lambda: self._cancel_job(job_name),
                "update_job_cuts": lambda: self._update_job_cuts(job_name),
                "update_job_transcript": lambda: self._update_job_transcript(job_name),
                "save_clip_feedback": lambda: self._save_clip_feedback(job_name),
                "rerun_job_stage": lambda: self._rerun_job_stage(job_name),
                "generate_job_covers": lambda: self._generate_job_covers(job_name),
                "select_job_cover": lambda: self._select_job_cover(job_name),
                "generate_job_segments": lambda: self._generate_job_segments(job_name),
                "generate_job_metadata": lambda: self._generate_job_metadata(job_name),
                "save_job_metadata": lambda: self._save_job_metadata(job_name),
                "generate_job_highlights": lambda: self._generate_job_highlights(job_name),
                "generate_job_highlight_cut": lambda: self._generate_job_highlight_cut(job_name),
                "render_job_highlight_cut": lambda: self._render_job_highlight_cut(job_name),
                "generate_job_publish_package": lambda: self._generate_job_publish_package(job_name),
                "generate_job_project_export": lambda: self._generate_job_project_export(job_name),
                "translate_job_subtitles": lambda: self._translate_job_subtitles(job_name),
                "render_translated_subtitles": lambda: self._render_translated_subtitles(job_name),
                "process_batch": self._process_batch,
                "process_one": self._process_one,
                "delete_job": lambda: self._delete_job(job_name),
            }
            handler = handlers.get(matched.endpoint)
            if handler is None:
                return False
            handler()
            return True

        def _send_health(self) -> None:
            payload = _health_response(Settings.load())
            queue_worker = getattr(self.server, "queue_worker", None)
            payload["queue_worker"] = (
                queue_worker.status()
                if queue_worker is not None
                else {"mode": "disabled", "running": False, "pid": None, "workers": 0, "restart_count": 0}
            )
            self._json(payload)

        def _send_jobs(self) -> None:
            jobs = list_jobs(settings)
            library_fields = job_library_fields_map(settings, [job.job_dir.name for job in jobs])
            self._json([
                self._job_payload(job, library_fields=library_fields.get(job.job_dir.name))
                for job in jobs
            ])

        def _send_job(self, job_name: str) -> None:
            job = load_job(settings.jobs_dir / job_name / "job.json")
            if job is None:
                self._json({"error": "job not found"}, status=404)
                return
            payload = self._job_payload(job, include_runtime=True)
            payload["pipeline_runs"] = StageRunRepository(library_database_path(settings)).list_for_job(job_name)
            latest_run = payload["pipeline_runs"][0] if payload["pipeline_runs"] else None
            payload["active_stages"] = [
                stage for stage in (latest_run or {}).get("stages", []) if stage.get("status") == "running"
            ]
            self._json(payload)

        def _approve_job(self, job_name: str) -> None:
            job_dir = (settings.jobs_dir / job_name).resolve()
            try:
                job_dir.relative_to(settings.jobs_dir.resolve())
            except ValueError:
                self._json({"error": "invalid job"}, status=400)
                return
            job = load_job(job_dir / "job.json")
            if job is None:
                self._json({"error": "job not found"}, status=404)
                return
            if job.status != "needs_review":
                self._json({"error": f"job is not waiting for review: {job.status}"}, status=409)
                return
            quality = evaluate_job_quality(settings, job_name)
            if quality["blocking"]:
                self._json(
                    structured_error(
                        "quality_gate_failed",
                        "Quality checks must be resolved before approval",
                        retryable=False,
                        action="open_review_quality",
                        details=quality,
                    ),
                    status=409,
                )
                return
            job.set_status("done")
            payload = job.to_dict()
            payload["files"] = _job_files(job.job_dir)
            self._json(payload)

        def _update_job_cuts(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before editing cuts"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                cuts = update_cuts_from_editor(job.job_dir, payload.get("clips", []))
                generate_clipped_ass_subtitles(settings, job.job_dir, force=True)
                _remove_render_outputs(job.job_dir)
                generate_render_preview(settings, job.job_dir, job.source_path, force=True)
                job.set_status("needs_review")
                revision = record_job_revision(
                    settings,
                    job.job_dir.name,
                    "cuts",
                    cuts,
                    summary="Saved clip decisions",
                )
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "cuts": cuts, "revision": revision})

        def _update_job_transcript(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before editing transcript"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            previous_transcript = read_json_file(job.job_dir / "transcript.json") or {}
            try:
                transcript = _update_transcript_from_editor(job.job_dir, payload.get("segments", []))
                cuts_path = job.job_dir / "cuts.json"
                if cuts_path.exists():
                    cuts = read_json_file(cuts_path) or {}
                    cuts["transcript_segments"] = _transcript_summary(transcript)
                    write_json_atomic(cuts_path, cuts)
                    update_cuts_from_editor(job.job_dir, cuts.get("clips", []))
                generate_ass_subtitles(settings, job.job_dir, force=True)
                generate_clipped_ass_subtitles(settings, job.job_dir, force=True)
                _remove_render_outputs(job.job_dir)
                generate_render_preview(settings, job.job_dir, job.source_path, force=True)
                job.set_status("needs_review")
                revision = record_job_revision(
                    settings,
                    job.job_dir.name,
                    "transcript",
                    transcript,
                    summary="Saved transcript edits",
                )
                _record_transcript_preferences(
                    preference_repository_for(settings),
                    job.job_dir.name,
                    previous_transcript,
                    transcript,
                )
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "transcript": transcript, "revision": revision})

        def _save_clip_feedback(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                feedback = _save_clip_feedback(job.job_dir, payload)
                preference_repository_for(settings).record(
                    "clip_feedback",
                    {
                        "action": str(payload.get("action") or ""),
                        "clip_key": str(payload.get("clip_key") or "")[:120],
                        "reason": str(payload.get("reason") or "")[:200],
                    },
                    job_name=job.job_dir.name,
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "feedback": feedback})

        def _rerun_job_stage(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish"}, status=409)
                return
            if self._job_runtime(job).get("active"):
                self._json({"error": "another managed task is already active for this job"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            stage = str(payload.get("stage") or "").strip()
            if stage not in RERUN_STATUS:
                self._json({"error": f"unsupported stage: {stage}"}, status=400)
                return
            existing = queue_repository.get_by_job(job.job_dir.name)
            queued_payload = dict((existing or {}).get("payload") or {})
            queued_payload.update(payload)
            queued_payload["path"] = str(job.source_path)
            queued_payload["_runtime_settings_snapshot"] = snapshot_runtime_settings(settings)
            if existing is None:
                existing = queue_repository.enqueue(job.job_dir.name, queued_payload)
            queue_item = queue_repository.retry_stage(
                str(existing["id"]),
                stage,
                payload=queued_payload,
            )
            job.set_status("queued")
            response = self._job_payload(job, include_runtime=True)
            response["queue"] = queue_item
            self._json(response, status=202)

        def _generate_job_covers(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before generating covers"}, status=409)
                return
            if settings.cover_provider.strip().lower() in {"openai", "openai-compatible", "openrouter", "google"} and not settings.cover_api_key_for_provider():
                self._json({"error": "COVER_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY is not configured"}, status=400)
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                options = normalize_cover_options(settings, payload)
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            manifest = mark_cover_generation_started(settings, job.job_dir, options)
            _publish_job_dir_event(job.job_dir)
            queue_item = self._enqueue_job_command(job, "generate_covers", options)
            if queue_item is None:
                return
            self._json({"job": self._job_payload(job), "cover": manifest, "queue": queue_item}, status=202)

        def _select_job_cover(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before selecting covers"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                manifest = select_cover(
                    job.job_dir,
                    aspect=str(payload.get("aspect") or "").strip(),
                    candidate=str(payload.get("candidate") or "").strip(),
                )
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            _publish_job_dir_event(job.job_dir)
            self._json({"job": self._job_payload(job), "cover": manifest})

        def _generate_job_segments(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "generate_segments")

        def _generate_job_metadata(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "generate_metadata")

        def _save_job_metadata(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None or not self._allow_quick_mutation(job):
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                metadata = save_metadata(job.job_dir, payload)
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "metadata": metadata})

        def _generate_job_highlights(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "generate_highlights")

        def _generate_job_highlight_cut(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None or not self._allow_quick_mutation(job):
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                highlight_cut = generate_highlight_cut(
                    job.job_dir,
                    target_seconds=float(payload.get("target_seconds") or 60),
                    force=bool(payload.get("force", False)),
                )
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "highlight_cut": highlight_cut})

        def _render_job_highlight_cut(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            payload = self._read_json()
            if payload is None:
                return
            target_seconds = float(payload.get("target_seconds") or 60)
            try:
                highlight_cut = generate_highlight_cut(
                    job.job_dir,
                    target_seconds=target_seconds,
                    force=bool(payload.get("force_cut", False)),
                )
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            queue_item = self._enqueue_job_command(job, "render_highlight", {"highlight_cut": highlight_cut})
            if queue_item is None:
                return
            self._json({
                "job": self._job_payload(job),
                "status": "queued",
                "highlight_cut": highlight_cut,
                "output": "highlight.mp4",
                "queue": queue_item,
            }, status=202)

        def _generate_job_publish_package(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "generate_publish_package")
        def _generate_job_project_export(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "generate_project_export")
        def _translate_job_subtitles(self, job_name: str) -> None:
            self._queue_job_enhancement(job_name, "translate_subtitles")
        def _render_translated_subtitles(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            payload = self._read_json()
            if payload is None:
                return
            target_language = str(payload.get("target_language") or "zh").strip() or "zh"
            try:
                subtitle_name = translated_clipped_ass_name(target_language)
                output_filename = translated_final_video_name(target_language)
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            subtitle_file = job.job_dir / subtitle_name
            if not subtitle_file.exists() or subtitle_file.stat().st_size < 1:
                self._json({"error": f"translated subtitles are not ready for {target_language}"}, status=400)
                return
            queue_item = self._enqueue_job_command(job, "render_translated_subtitles", {
                "target_language": target_language,
                "output_filename": output_filename,
            })
            if queue_item is None:
                return
            self._json({
                "job": self._job_payload(job),
                "status": "queued",
                "target_language": target_language,
                "output": output_filename,
                "queue": queue_item,
            }, status=202)

        def _cancel_job(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}"}, status=409)
                return
            runtime = self._job_runtime(job)
            queue_item = runtime.get("queue")
            if isinstance(queue_item, dict) and queue_item.get("status") in {"pending", "paused", "running"}:
                updated = queue_repository.cancel(str(queue_item.get("id") or ""))
                # A running worker owns job.json until it acknowledges the
                # cooperative stop. Pending and paused work has no owner and
                # can become terminal immediately.
                if updated and updated.get("status") == "canceled":
                    job.cancel()
            elif runtime.get("stale"):
                job.cancel("Stopped stale job state because no active worker was found")
            else:
                self._json({"error": "job is active outside the managed queue and cannot be canceled safely"}, status=409)
                return
            self._json(self._job_payload(job, include_runtime=True))

        def _delete_job(self, job_name: str) -> None:
            job_dir = (settings.jobs_dir / job_name).resolve()
            try:
                job_dir.relative_to(settings.jobs_dir.resolve())
            except ValueError:
                self._json({"error": "invalid job"}, status=400)
                return
            if not job_dir.exists():
                self._json({"error": "job not found"}, status=404)
                return
            job = load_job(job_dir / "job.json")
            if job is None:
                self._json({"error": "job not found"}, status=404)
                return
            runtime = self._job_runtime(job)
            if runtime.get("active"):
                self._json({"error": "job still has an active worker; wait for cancellation to finish before deleting"}, status=409)
                return
            if not _job_is_terminal(job) and not runtime.get("stale"):
                self._json({"error": f"job is already {job.status}; wait for it to finish before deleting"}, status=409)
                return
            tombstone = job_dir.with_name(f".{job_dir.name}.deleting-{uuid.uuid4().hex}")
            try:
                job_dir.replace(tombstone)
            except OSError:
                self._json({
                    "error": "job files are still in use",
                    "code": "job_files_in_use",
                }, status=409)
                return
            try:
                delete_job_records(settings, job_name)
            except Exception:
                try:
                    tombstone.replace(job_dir)
                except OSError:
                    _schedule_tombstone_cleanup(tombstone)
                self._json({
                    "error": "job metadata could not be deleted",
                    "code": "job_delete_failed",
                }, status=500)
                return
            try:
                shutil.rmtree(tombstone)
            except OSError:
                _schedule_tombstone_cleanup(tombstone)
                self._json({"deleted": job_name, "cleanup_pending": True}, status=202)
                return
            self._json({"deleted": job_name, "cleanup_pending": False})

        def _upload_recording(self, query: str) -> None:
            params = parse_qs(query)
            filename = (params.get("filename") or [""])[0]
            if not filename:
                self._json({"error": "missing filename"}, status=400)
                return
            try:
                target = _recording_upload_path(settings, filename)
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                self._json({"error": "empty upload"}, status=400)
                return
            if settings.recording_upload_max_bytes > 0 and length > settings.recording_upload_max_bytes:
                self._json({
                    "error": f"upload exceeds RECORDING_UPLOAD_MAX_BYTES ({settings.recording_upload_max_bytes})",
                    "max_bytes": settings.recording_upload_max_bytes,
                }, status=413)
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
            self._json({
                "name": target.name,
                "path": str(target.resolve()),
                "relative_path": str(target.relative_to(settings.input_recordings_dir.resolve())),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }, status=201)

        def _load_job_for_mutation(self, job_name: str) -> Job | None:
            job_dir = (settings.jobs_dir / job_name).resolve()
            try:
                job_dir.relative_to(settings.jobs_dir.resolve())
            except ValueError:
                self._json({"error": "invalid job"}, status=400)
                return None
            job = load_job(job_dir / "job.json")
            if job is None:
                self._json({"error": "job not found"}, status=404)
                return None
            return job

        def _job_payload(
            self,
            job: Job,
            *,
            library_fields: dict[str, Any] | None = None,
            include_runtime: bool = False,
        ) -> dict[str, Any]:
            payload = job.to_dict()
            payload["files"] = _job_files(job.job_dir)
            payload["feedback"] = _job_feedback(job.job_dir)
            payload.update(library_fields or job_library_fields(settings, job.job_dir.name))
            if include_runtime:
                payload["runtime"] = self._job_runtime(job)
            return payload

        def _job_runtime(self, job: Job) -> dict[str, Any]:
            queue_item = queue_repository.get_by_job(job.job_dir.name)
            pipeline_runs = StageRunRepository(library_database_path(settings)).list_for_job(job.job_dir.name, limit=1)
            return _job_runtime_state(job.status, queue_item, pipeline_runs)

        def _allow_quick_mutation(self, job: Job) -> bool:
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before running enhancements"}, status=409)
                return False
            if self._job_runtime(job).get("active"):
                self._json({"error": "another managed task is already active for this job"}, status=409)
                return False
            return True

        def _enqueue_job_command(
            self,
            job: Job,
            command: str,
            command_payload: dict[str, Any],
        ) -> dict[str, Any] | None:
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish"}, status=409)
                return None
            current = queue_repository.get_by_job(job.job_dir.name)
            if current and current.get("status") in {"pending", "running", "paused"}:
                self._json({"error": "another managed task is already active for this job"}, status=409)
                return None
            payload = {
                "path": str(job.source_path),
                "_command": command,
                "_command_payload": command_payload,
                "_runtime_settings_snapshot": snapshot_runtime_settings(settings),
            }
            return queue_repository.enqueue(job.job_dir.name, payload)

        def _queue_job_enhancement(self, job_name: str, command: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            payload = self._read_json()
            if payload is None:
                return
            queue_item = self._enqueue_job_command(job, command, payload)
            if queue_item is None:
                return
            self._json({
                "job": self._job_payload(job),
                "status": "queued",
                "command": command,
                "queue": queue_item,
            }, status=202)

        def _send_events(self, query: str = "") -> None:
            requested_last_id = _event_last_id(self.headers.get("Last-Event-ID"), query)
            last_id = requested_last_id
            snapshot_id = current_event_id()
            try:
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(_format_sse("hello", {
                    "jobs": [self._job_payload(job) for job in list_jobs(settings)],
                    "tools_install": _tools_install_snapshot(),
                    "server_time": datetime.now().isoformat(timespec="seconds"),
                }, event_id=snapshot_id).encode("utf-8"))
                self.wfile.flush()
                if requested_last_id <= 0 or requested_last_id > snapshot_id:
                    last_id = snapshot_id
                while True:
                    events = wait_for_events(last_id, timeout_seconds=15.0)
                    if not events:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        continue
                    for event in events:
                        last_id = event.id
                        self.wfile.write(_format_sse(event.type, event.payload, event_id=event.id).encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _send_job_file(self, job_name: str, filename: str, query: str = "") -> None:
            job_dir = (settings.jobs_dir / job_name).resolve()
            try:
                job_dir.relative_to(settings.jobs_dir.resolve())
            except ValueError:
                self._json({"error": "invalid job"}, status=400)
                return
            path = (job_dir / filename).resolve()
            try:
                path.relative_to(job_dir)
            except ValueError:
                self._json({"error": "invalid file"}, status=400)
                return
            if not path.is_file():
                self._json({"error": "file not found"}, status=404)
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self._send_file(path, content_type, attachment=("download=1" in query))

        def _send_static_file(self, raw_path: str) -> None:
            web_root = (settings.root / "web").resolve()
            path = (web_root / raw_path).resolve()
            try:
                path.relative_to(web_root)
            except ValueError:
                self._json({"error": "invalid static path"}, status=400)
                return
            if not path.is_file():
                self._json({"error": "not found"}, status=404)
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self._send_file(path, content_type, attachment=False, cache_control="no-store, max-age=0")

        def _send_file(self, path: Path, content_type: str, *, attachment: bool, cache_control: str | None = None) -> None:
            size = path.stat().st_size
            range_header = self.headers.get("Range")
            byte_range = _parse_range(range_header, size) if range_header else None
            if range_header and byte_range is None:
                self.send_response(416)
                self._cors_headers()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            start, end = byte_range if byte_range else (0, max(0, size - 1))
            content_length = max(0, end - start + 1)
            self.send_response(206 if byte_range else 200)
            self._cors_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(content_length))
            self.send_header("Accept-Ranges", "bytes")
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = content_length
                try:
                    while remaining > 0:
                        chunk = handle.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self._json({"error": "invalid Content-Length"}, status=400)
                return None
            if length <= 0:
                return {}
            if length > MAX_JSON_BODY_SIZE:
                self._json({"error": "request body too large"}, status=413)
                return None
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._json({"error": "invalid JSON body"}, status=400)
                return None
            if not isinstance(payload, dict):
                self._json({"error": "JSON body must be an object"}, status=400)
                return None
            return payload

        def _json(self, payload: Any, *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self._cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _cors_headers(self) -> None:
            origin = _normalize_origin(self.headers.get("Origin"))
            if origin and origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
            self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Range, Content-Length, Content-Disposition")

        def _require_allowed_origin(self) -> bool:
            origin = _normalize_origin(self.headers.get("Origin"))
            if origin is None or origin in allowed_origins:
                return True
            self._json({"error": "origin not allowed"}, status=403)
            return False

    return Handler


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    if not range_header or size <= 0:
        return None
    match = RANGE_RE.match(range_header.strip())
    if not match:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        start = max(0, size - suffix_length)
        end = size - 1
    if start < 0 or end < start or start >= size:
        return None
    return start, min(end, size - 1)


def _event_last_id(header_value: str | None, query: str) -> int:
    candidates = [header_value]
    candidates.extend(parse_qs(query).get("last_id", []))
    for value in candidates:
        if value is None:
            continue
        try:
            return max(0, int(str(value).strip()))
        except ValueError:
            continue
    return 0


def _health_response(settings: Settings) -> dict[str, Any]:
    payload = health_payload(settings)
    payload["tools_install"] = _tools_install_snapshot()
    return payload


def _publish_package_queue(settings: Settings) -> dict[str, Any]:
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


def _tools_install_snapshot() -> dict[str, Any]:
    with TOOLS_INSTALL_LOCK:
        snapshot = dict(TOOLS_INSTALL_STATE)
        snapshot["log_tail"] = list(TOOLS_INSTALL_STATE.get("log_tail") or [])
        return snapshot


def _schedule_tombstone_cleanup(path: Path, *, attempts: int = 60, delay_seconds: float = 1.0) -> None:
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


def _resume_tombstone_cleanup(jobs_dir: Path) -> None:
    if not jobs_dir.exists():
        return
    for path in jobs_dir.glob(".*.deleting-*"):
        if path.is_dir():
            _schedule_tombstone_cleanup(path)


def _set_tools_install_state(**updates: Any) -> dict[str, Any]:
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


def _run_tools_install(settings: Settings, command: list[str]) -> None:
    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        _set_tools_install_state(
            status="failed",
            failed_at=datetime.now().isoformat(timespec="seconds"),
            message=str(exc),
        )
        return

    if process.stdout is not None:
        for line in process.stdout:
            _set_tools_install_state(log_append=line)
    returncode = process.wait()
    if returncode == 0:
        clear_health_cache()
        _set_tools_install_state(
            status="done",
            completed_at=datetime.now().isoformat(timespec="seconds"),
            returncode=returncode,
            message="Tool installation finished",
        )
        publish_event("health", _health_response(Settings.load()))
        return
    _set_tools_install_state(
        status="failed",
        failed_at=datetime.now().isoformat(timespec="seconds"),
        returncode=returncode,
        message=f"Tool installation failed with exit code {returncode}",
    )


def _format_sse(event_type: str, payload: dict[str, Any], *, event_id: int) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"id: {event_id}", f"event: {event_type}"]
    lines.extend(f"data: {line}" for line in data.splitlines() or ["{}"])
    return "\n".join(lines) + "\n\n"


def _normalize_origin(origin: str | None) -> str | None:
    if origin is None:
        return None
    value = origin.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_api_origins(settings: Settings) -> set[str]:
    origins = _default_api_origins(settings)
    for raw_origin in settings.api_allowed_origins:
        origin = _normalize_origin(raw_origin)
        if origin:
            origins.add(origin)
    return origins


def _default_api_origins(settings: Settings) -> set[str]:
    port = settings.api_port
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    host = settings.api_host.strip()
    if host and host not in {"0.0.0.0", "::", "[::]"}:
        origin_host = host
        if ":" in origin_host and not origin_host.startswith("["):
            origin_host = f"[{origin_host}]"
        origins.add(f"http://{origin_host.lower()}:{port}")
    return origins


def _job_is_terminal(job: Job) -> bool:
    return job.status in TERMINAL_STATUSES


def _job_runtime_state(
    job_status: str,
    queue_item: dict[str, Any] | None,
    pipeline_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    queue_status = str((queue_item or {}).get("status") or "")
    queue_pid = _safe_int((queue_item or {}).get("worker_pid"))
    queue_active = queue_status in {"pending", "paused"} or queue_status == "running"
    latest_run = pipeline_runs[0] if pipeline_runs else None
    pipeline_pid = _safe_int((latest_run or {}).get("worker_pid"))
    pipeline_active = bool(
        latest_run
        and latest_run.get("status") == "running"
        and _pid_is_alive(pipeline_pid)
    )
    terminal = job_status in TERMINAL_STATUSES
    # Queue ownership is authoritative even if job.json temporarily contains
    # a terminal projection while the worker is finishing its acknowledgement.
    active = queue_active or (not terminal and pipeline_active)
    stale = not terminal and not active
    queue_summary = None
    if queue_item is not None:
        queue_summary = {
            "id": queue_item.get("id"),
            "status": queue_status,
            "worker_pid": queue_item.get("worker_pid"),
            "heartbeat_at": queue_item.get("heartbeat_at"),
            "cancel_requested": bool(queue_item.get("cancel_requested")),
        }
    return {
        "active": active,
        "stale": stale,
        "can_cancel": not terminal and not bool((queue_item or {}).get("cancel_requested")) and (active or stale),
        "can_delete": not active and (terminal or stale),
        "queue": queue_summary,
        "pipeline": {
            "id": latest_run.get("id"),
            "status": latest_run.get("status"),
            "worker_pid": latest_run.get("worker_pid"),
        } if latest_run else None,
    }


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    return process_is_alive(pid)


def _job_feedback(job_dir: Path) -> dict[str, Any]:
    return read_json_file(job_dir / "feedback.json") or {"items": []}


def _save_clip_feedback(job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    action = _bounded_text(payload.get("action"), 20)
    if action not in {"accepted", "rejected", "clear"}:
        raise ValueError("action must be accepted, rejected, or clear")
    clip_key = _bounded_text(payload.get("clip_key"), 120)
    if not clip_key:
        raise ValueError("clip_key is required")
    current = _job_feedback(job_dir)
    items = current.get("items") if isinstance(current.get("items"), list) else []
    items = [item for item in items if isinstance(item, dict) and item.get("clip_key") != clip_key]
    if action != "clear":
        items.append({
            "clip_key": clip_key,
            "action": action,
            "index": _safe_int(payload.get("index")),
            "start": _safe_float(payload.get("start")),
            "end": _safe_float(payload.get("end")),
            "reason": _bounded_text(payload.get("reason"), 200),
            "text": _bounded_text(payload.get("text"), 500),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    result = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items[-1000:],
    }
    write_json_atomic(job_dir / "feedback.json", result)
    return result


def _record_transcript_preferences(
    repository: Any,
    job_name: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> int:
    """Record explicit text edits only; timing changes are not preference signals."""
    before_segments = before.get("segments") if isinstance(before.get("segments"), list) else []
    after_segments = after.get("segments") if isinstance(after.get("segments"), list) else []
    recorded = 0
    for previous, current in zip(before_segments, after_segments):
        if not isinstance(previous, dict) or not isinstance(current, dict):
            continue
        previous_text = str(previous.get("text") or "").strip()
        current_text = str(current.get("text") or "").strip()
        if not previous_text or not current_text or previous_text == current_text:
            continue
        repository.record(
            "subtitle_correction",
            {"before": previous_text[:500], "after": current_text[:500]},
            job_name=job_name,
        )
        recorded += 1
    return recorded


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _job_files(job_dir: Path) -> list[dict[str, Any]]:
    if not job_dir.exists():
        return []
    files = []
    for path in sorted(job_dir.rglob("*")):
        if path.is_file():
            stat = path.stat()
            relative_name = str(path.relative_to(job_dir)).replace("\\", "/")
            files.append({
                "name": relative_name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            })
    return files


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _recording_files(settings: Settings) -> list[dict[str, Any]]:
    root = settings.input_recordings_dir.resolve()
    if not root.exists():
        return []
    files: list[dict[str, Any]] = []
    for path in root.rglob("*"):
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


def _recording_upload_path(settings: Settings, filename: str) -> Path:
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


def _execute_queue_item(settings: Settings, item: dict[str, Any]) -> None:
    job_name = str(item.get("job_name") or "")
    job = load_job(Path(settings.jobs_dir) / job_name / "job.json")
    if job is None:
        raise RuntimeError(f"queued job not found: {job_name}")
    ensure_job_capacity(settings, job.source_path)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    write_json_atomic(job.job_dir / "run_config.json", {
        "queue_id": item.get("id"),
        "attempt": item.get("attempt"),
        "retry_stage": item.get("retry_stage"),
        "runtime_settings": payload.get("_runtime_settings_snapshot"),
        "recipe": payload.get("_recipe_snapshot"),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    })
    job_settings, options = _queued_process_config(settings, payload)
    retry_stage = str(item.get("retry_stage") or "").strip()
    def control_callback() -> str | None:
        return _queue_control_action(settings, str(item.get("id") or ""))
    command = str(payload.get("_command") or "").strip()
    if command:
        _execute_managed_job_command(
            job_settings,
            job,
            command,
            payload.get("_command_payload") if isinstance(payload.get("_command_payload"), dict) else {},
            control_callback=control_callback,
        )
        return
    if retry_stage:
        if retry_stage not in RERUN_STATUS:
            raise RuntimeError(f"unsupported retry stage: {retry_stage}")
        options.update({
            "force": True,
            "selected_stages": [retry_stage],
            "expand_selected_dependencies": False,
            "completion_status": "needs_review",
            "control_callback": control_callback,
        })
        process_job(job_settings, job, **options)
    else:
        options["control_callback"] = control_callback
        process_job(job_settings, job, **options)
    if job.status == "failed":
        raise RuntimeError(job.error or "job failed")


def _execute_managed_job_command(
    settings: Settings,
    job: Job,
    command: str,
    payload: dict[str, Any],
    *,
    control_callback: Callable[[], str | None],
) -> None:
    def check_control() -> None:
        action = control_callback()
        if action in {"paused", "canceled"}:
            raise QueueControlRequested(action)

    check_control()
    if command == "generate_covers":
        try:
            generate_cover_candidates(
                settings,
                job.job_dir,
                title=str(payload.get("title") or "").strip(),
                style=str(payload.get("style") or "short_video").strip(),
                count=int(payload.get("count") or settings.cover_count),
                aspects=[str(value) for value in payload.get("aspects", [])]
                if isinstance(payload.get("aspects"), list)
                else None,
            )
        finally:
            _publish_job_dir_event(job.job_dir)
        check_control()
        return
    if command == "generate_segments":
        generate_platform_segments(
            settings,
            job.job_dir,
            platforms=_string_list(payload.get("platforms")),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_metadata":
        generate_metadata(
            settings,
            job.job_dir,
            platform=str(payload.get("platform") or "douyin"),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_highlights":
        generate_highlights(settings, job.job_dir, force=bool(payload.get("force", False)))
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_publish_package":
        generate_publish_package(
            settings,
            job.job_dir,
            platforms=_string_list(payload.get("platforms")),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "generate_project_export":
        generate_project_exports(
            settings,
            job.job_dir,
            targets=_string_list(payload.get("targets")),
            include_clips=bool(payload.get("include_clips", False)),
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "translate_subtitles":
        target_language = str(payload.get("target_language") or "zh").strip() or "zh"
        translate_subtitles(
            settings,
            job.job_dir,
            target_language=target_language,
            force=bool(payload.get("force", False)),
        )
        check_control()
        _publish_job_dir_event(job.job_dir)
        return
    if command == "render_highlight":
        highlight_cut = payload.get("highlight_cut") if isinstance(payload.get("highlight_cut"), dict) else {}
        status_path = job.job_dir / "highlight_render_status.json"
        status_base = {
            "output": "highlight.mp4",
            "duration_seconds": highlight_cut.get("duration_seconds", 0),
            "selected_clip_count": highlight_cut.get("selected_clip_count", 0),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            write_json_atomic(status_path, {**status_base, "status": "rendering", "message": "Rendering highlight video."})
            render_highlight_video(
                settings,
                job.job_dir,
                job.source_path,
                force=True,
                resource_wait_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "waiting_for_gpu", "message": "Waiting for GPU to render highlight video."}
                ),
                resource_acquired_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "rendering", "message": "GPU available. Rendering highlight video."}
                ),
                control_callback=control_callback,
            )
            write_json_atomic(status_path, {
                **status_base,
                "status": "done",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
        except QueueControlRequested:
            write_json_atomic(status_path, {**status_base, "status": "canceled"})
            raise
        except Exception as exc:
            write_json_atomic(status_path, {**status_base, "status": "failed", "error": str(exc)})
            raise
        finally:
            _publish_job_dir_event(job.job_dir)
        return
    if command == "render_translated_subtitles":
        target_language = str(payload.get("target_language") or "zh").strip() or "zh"
        output_filename = str(payload.get("output_filename") or translated_final_video_name(target_language))
        status_path = job.job_dir / f"subtitle_translation_render_{target_language}.json"
        status_base = {
            "target_language": target_language,
            "output": output_filename,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        preview = read_json_file(job.job_dir / "final_render_preview.json") or {}
        try:
            write_json_atomic(status_path, {**status_base, "status": "rendering", "message": "Rendering translated subtitles."})
            render_final_video(
                settings,
                job.job_dir,
                job.source_path,
                force=True,
                vertical=bool(preview.get("vertical", False)),
                burn_subtitles=True,
                subtitle_filename=translated_clipped_ass_name(target_language),
                output_filename=output_filename,
                resource_wait_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "waiting_for_gpu", "message": "Waiting for GPU to render translated subtitles."}
                ),
                resource_acquired_callback=lambda: write_json_atomic(
                    status_path, {**status_base, "status": "rendering", "message": "GPU available. Rendering translated subtitles."}
                ),
                control_callback=control_callback,
            )
            write_json_atomic(status_path, {
                **status_base,
                "status": "done",
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
        except QueueControlRequested:
            write_json_atomic(status_path, {**status_base, "status": "canceled"})
            raise
        except Exception as exc:
            write_json_atomic(status_path, {**status_base, "status": "failed", "error": str(exc)})
            raise
        finally:
            _publish_job_dir_event(job.job_dir)
        return
    raise RuntimeError(f"unsupported managed job command: {command}")


def _queue_control_action(settings: Settings, queue_id: str) -> str | None:
    current = queue_repository_for(settings).get(queue_id)
    if not current:
        return "canceled"
    if current.get("cancel_requested"):
        return "canceled"
    if current.get("pause_requested"):
        return "paused"
    return None


def _queued_process_config(settings: Settings, payload: dict[str, Any]) -> tuple[Settings, dict[str, Any]]:
    settings = apply_runtime_settings_snapshot(settings, payload.get("_runtime_settings_snapshot"))
    effective = dict(payload)
    recipe_id = str(payload.get("recipe_id") or "").strip()
    if recipe_id:
        recipe = payload.get("_recipe_snapshot")
        if not isinstance(recipe, dict):
            recipe = automation_repository_for(settings).get_recipe(recipe_id)
        if recipe is None:
            raise RuntimeError(f"recipe not found: {recipe_id}")
        effective = {**recipe.get("options", {}), **effective}
        effective["recipe_stages"] = recipe.get("stages", [])
    profile = str(effective.get("profile") or "").strip()
    job_settings = apply_profile_settings(settings, profile)
    if "source_integrity_scan" in effective:
        job_settings = replace(
            job_settings,
            source_integrity_scan_enabled=bool(effective.get("source_integrity_scan", False)),
        )
    options = apply_profile_flags({
        "force": bool(effective.get("force", False)),
        "detect_silence_enabled": bool(effective.get("detect_silence", False)),
        "detect_freeze_enabled": bool(effective.get("detect_freeze", False)),
        "detect_scenes_enabled": bool(effective.get("detect_scenes", False)),
        "render_review_enabled": bool(effective.get("render_review", False)),
        "render_final_enabled": bool(effective.get("render_final", False)),
        "vertical_enabled": bool(effective.get("vertical", False)),
        "burn_subtitles_enabled": bool(effective.get("burn_subtitles", False)),
        "plan_crop_enabled": bool(effective.get("plan_crop", False)),
        "plan_uvr_enabled": bool(effective.get("plan_uvr", False)),
        "skip_transcribe": bool(effective.get("skip_transcribe", False)),
        "progress_enabled": False,
        "whisper_language": str(effective.get("whisper_language") or "").strip() or None,
        "selected_stages": effective.get("recipe_stages") if isinstance(effective.get("recipe_stages"), list) else None,
    }, profile)
    return job_settings, options


def _publish_job_dir_event(job_dir: Path) -> None:
    job = load_job(job_dir / "job.json")
    if job is not None:
        publish_event("job", job.to_dict())


def _run_single_stage(
    settings: Settings,
    job: Job,
    stage: str,
    options: dict[str, Any],
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    """Compatibility entry point backed by the canonical pipeline implementation."""
    if stage not in RERUN_STATUS:
        raise ValueError(f"unsupported stage: {stage}")
    job_settings, process_options = _queued_process_config(
        settings,
        {"path": str(job.source_path), **dict(options)},
    )
    process_options.update({
        "force": True,
        "selected_stages": [stage],
        "expand_selected_dependencies": False,
        "completion_status": "needs_review",
        "control_callback": control_callback,
    })
    process_job(job_settings, job, **process_options)
def _update_transcript_from_editor(job_dir: Path, segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(segments, list):
        raise RuntimeError("segments must be a list")
    current = read_json_file(job_dir / "transcript.json") or {}
    normalized = _validate_transcript_segments(segments)
    payload = dict(current) if isinstance(current, dict) else {}
    payload["segments"] = normalized
    payload["edited_in_web"] = True
    write_json_atomic(job_dir / "transcript.json", payload)
    write_text_atomic(job_dir / "transcript.txt", "\n".join(segment["text"] for segment in normalized if segment["text"]).strip() + "\n")
    write_text_atomic(job_dir / "transcript.srt", _segments_to_srt(normalized))
    return payload


def _validate_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise RuntimeError(f"transcript segment {index} is invalid")
        try:
            start = round(max(0.0, float(segment["start"])), 3)
            end = round(max(start, float(segment["end"])), 3)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"transcript segment {index} start/end is invalid") from exc
        text = str(segment.get("text") or "").strip()
        value = dict(segment)
        value["start"] = start
        value["end"] = end
        value["text"] = text
        normalized.append(value)
    return sorted(normalized, key=lambda item: (float(item["start"]), float(item["end"])))


def _transcript_summary(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    summary = []
    segments = transcript.get("segments") if isinstance(transcript, dict) else []
    if not isinstance(segments, list):
        return summary
    for segment in segments[:200]:
        if not isinstance(segment, dict):
            continue
        summary.append({
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": str(segment.get("text", "")).strip(),
        })
    return summary


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        blocks.append(
            f"{index}\n"
            f"{_srt_time(float(segment['start']))} --> {_srt_time(float(segment['end']))}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _remove_render_outputs(job_dir: Path) -> None:
    for name in ["review.mp4", "final.mp4", "render_preview.json", "render_review.ps1", "final_render_preview.json"]:
        path = job_dir / name
        if path.exists():
            path.unlink()
