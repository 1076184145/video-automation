from __future__ import annotations

import json
import mimetypes
import re
import shutil
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import Settings
from .covers import cover_manifest, generate_cover_candidates, mark_cover_generation_started, normalize_cover_options, select_cover
from .crop import generate_vertical_crop_plan
from .cuts import generate_cuts, update_cuts_from_editor
from .downloads import get_download, list_downloads, start_download
from .hooks import generate_uvr_plan
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .jobs import Job, create_job, list_jobs, load_job, normalize_source_path
from .llm_tools import generate_highlights, generate_metadata, save_metadata
from .media import MEDIA_EXTENSIONS, detect_decode_errors, detect_freeze, detect_scenes, detect_silence, extract_audio, extract_high_quality_audio, generate_thumbnail, generate_waveform, probe_media
from .plans import generate_bgm_mix_plan, generate_platform_export_plan, generate_webhook_plan
from .publish import generate_publish_package
from .profiles import apply_profile_flags, apply_profile_settings
from .project_exports import generate_project_exports
from .render import generate_render_preview, render_final_video, render_review_video
from .segments import generate_platform_segments
from .subtitles import generate_ass_subtitles, generate_clipped_ass_subtitles
from .transcribe import transcribe_audio
from .worker import health_payload, process_job

CHUNK_SIZE = 1024 * 1024
MAX_JSON_BODY_SIZE = 2 * 1024 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
TERMINAL_STATUSES = {"needs_review", "done", "failed"}
RERUN_STATUS = {
    "probe": "probing",
    "detect_corruption": "detecting_corruption",
    "extract_audio": "extracting_audio",
    "transcribe": "transcribing",
    "detect_silence": "detecting_silence",
    "detect_freeze": "detecting_freeze",
    "detect_scenes": "detecting_scenes",
    "plan_cuts": "planning_cuts",
    "style_subtitles": "styling_subtitles",
    "plan_crop": "planning_crop",
    "plan_uvr": "planning_uvr",
    "plan_render": "planning_render",
    "render_review": "rendering_review",
    "render_final": "rendering_final",
}
COVER_GENERATIONS: set[str] = set()
COVER_GENERATIONS_LOCK = threading.Lock()
ENHANCEMENT_RUNS: set[str] = set()
ENHANCEMENT_RUNS_LOCK = threading.Lock()


def serve(settings: Settings) -> None:
    server = ThreadingHTTPServer((settings.api_host, settings.api_port), _handler_class(settings))
    print(f"Video Automation API listening on http://{settings.api_host}:{settings.api_port}", flush=True)
    server.serve_forever()


def _handler_class(settings: Settings) -> type[BaseHTTPRequestHandler]:
    process_semaphore = threading.Semaphore(max(1, settings.api_parallel_jobs))
    allowed_origins = _allowed_api_origins(settings)

    class Handler(BaseHTTPRequestHandler):
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
            if parsed.path == "/":
                self._send_static_file("index.html")
                return
            if parsed.path.startswith("/static/"):
                self._send_static_file(unquote(parsed.path.removeprefix("/static/")))
                return
            if parsed.path == "/health":
                self._json(health_payload(settings))
                return
            if parsed.path == "/recordings":
                self._json(_recording_files(settings))
                return
            if parsed.path == "/downloads":
                self._json(list_downloads(settings))
                return
            if parsed.path == "/jobs":
                self._json([self._job_payload(job) for job in list_jobs(settings)])
                return
            if parsed.path.startswith("/jobs/"):
                parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "files":
                    self._send_job_file(parts[1], parts[3], parsed.query)
                    return
                name = parts[1] if len(parts) >= 2 else ""
                job = load_job(settings.jobs_dir / name / "job.json")
                if job is None:
                    self._json({"error": "job not found"}, status=404)
                    return
                payload = job.to_dict()
                payload["files"] = _job_files(job.job_dir)
                self._json(payload)
                return
            self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_allowed_origin():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/recordings/upload":
                self._upload_recording(parsed.query)
                return
            if parsed.path == "/downloads":
                self._start_download()
                return
            if parsed.path.startswith("/downloads/"):
                parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
                if len(parts) == 3 and parts[0] == "downloads" and parts[2] == "import":
                    self._import_download(parts[1], process_semaphore)
                    return
            if parsed.path.startswith("/jobs/"):
                parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
                if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "approve":
                    self._approve_job(parts[1])
                    return
                if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cuts":
                    self._update_job_cuts(parts[1])
                    return
                if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "transcript":
                    self._update_job_transcript(parts[1])
                    return
                if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "rerun":
                    self._rerun_job_stage(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "covers" and parts[3] == "generate":
                    self._generate_job_covers(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "covers" and parts[3] == "select":
                    self._select_job_cover(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "segments" and parts[3] == "generate":
                    self._generate_job_segments(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "metadata" and parts[3] == "generate":
                    self._generate_job_metadata(parts[1])
                    return
                if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "metadata":
                    self._save_job_metadata(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "highlights" and parts[3] == "generate":
                    self._generate_job_highlights(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "publish" and parts[3] == "package":
                    self._generate_job_publish_package(parts[1])
                    return
                if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "project-export" and parts[3] == "generate":
                    self._generate_job_project_export(parts[1])
                    return
            if parsed.path == "/process/batch":
                self._process_batch(process_semaphore)
                return
            if parsed.path != "/process":
                self._json({"error": "not found"}, status=404)
                return
            self._process_one(process_semaphore)

        def _process_one(self, process_semaphore: threading.Semaphore) -> None:
            payload = self._read_json()
            if payload is None:
                return
            try:
                job, status = self._submit_process_payload(payload, process_semaphore)
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json(job.to_dict(), status=status)

        def _process_batch(self, process_semaphore: threading.Semaphore) -> None:
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
            if len(raw_items) > 50:
                self._json({"error": "batch is limited to 50 items"}, status=400)
                return
            jobs = []
            for raw_item in raw_items:
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
                try:
                    job, status = self._submit_process_payload(item_payload, process_semaphore)
                except ValueError as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                jobs.append({**job.to_dict(), "http_status": status})
            self._json({"jobs": jobs, "count": len(jobs), "parallel_jobs": settings.api_parallel_jobs}, status=202)

        def _submit_process_payload(self, payload: dict[str, Any], process_semaphore: threading.Semaphore) -> tuple[Job, int]:
            source = payload.get("path") or payload.get("source_path")
            profile = str(payload.get("profile") or "").strip()
            if not source:
                raise ValueError("missing path")
            try:
                job = create_job(settings, normalize_source_path(str(source)), force=bool(payload.get("force", False)))
            except OSError as exc:
                raise ValueError(str(exc)) from exc
            if job.status in {"needs_review", "done", "failed"} and not bool(payload.get("force", False)):
                return job, 200
            if job.status != "pending" and not bool(payload.get("force", False)):
                return job, 202
            job_settings = apply_profile_settings(settings, profile)
            options = apply_profile_flags({
                "force": bool(payload.get("force", False)),
                "detect_silence_enabled": bool(payload.get("detect_silence", False)),
                "detect_freeze_enabled": bool(payload.get("detect_freeze", False)),
                "detect_scenes_enabled": bool(payload.get("detect_scenes", False)),
                "render_review_enabled": bool(payload.get("render_review", False)),
                "render_final_enabled": bool(payload.get("render_final", False)),
                "vertical_enabled": bool(payload.get("vertical", False)),
                "burn_subtitles_enabled": bool(payload.get("burn_subtitles", False)),
                "plan_crop_enabled": bool(payload.get("plan_crop", False)),
                "plan_uvr_enabled": bool(payload.get("plan_uvr", False)),
                "skip_transcribe": bool(payload.get("skip_transcribe", False)),
                "progress_enabled": False,
                "whisper_language": str(payload.get("whisper_language") or "").strip() or None,
            }, profile)
            job.set_status("queued")
            thread = threading.Thread(
                target=_run_process_job,
                args=(process_semaphore, job_settings, job, options),
                daemon=True,
            )
            thread.start()
            return job, 202

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._require_allowed_origin():
                return
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
            if len(parts) == 2 and parts[0] == "jobs":
                self._delete_job(parts[1])
                return
            self._json({"error": "not found"}, status=404)

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
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "cuts": cuts})

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
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"job": self._job_payload(job), "transcript": transcript})

        def _rerun_job_stage(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            stage = str(payload.get("stage") or "").strip()
            if stage not in RERUN_STATUS:
                self._json({"error": f"unsupported stage: {stage}"}, status=400)
                return
            thread = threading.Thread(
                target=_run_single_stage,
                args=(settings, job, stage, payload),
                daemon=True,
            )
            thread.start()
            self._json(self._job_payload(job), status=202)

        def _generate_job_covers(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before generating covers"}, status=409)
                return
            if settings.cover_provider.strip().lower() == "openai" and not settings.openai_api_key.strip():
                self._json({"error": "OPENAI_API_KEY is not configured"}, status=400)
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                options = normalize_cover_options(settings, payload)
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            key = str(job.job_dir.resolve())
            with COVER_GENERATIONS_LOCK:
                if key in COVER_GENERATIONS:
                    self._json({"error": "cover generation is already running for this job"}, status=409)
                    return
                COVER_GENERATIONS.add(key)
            manifest = mark_cover_generation_started(settings, job.job_dir, options)
            thread = threading.Thread(
                target=_run_cover_generation,
                args=(settings, job.job_dir, key, options),
                daemon=True,
            )
            thread.start()
            self._json({"job": self._job_payload(job), "cover": manifest}, status=202)

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
            self._json({"job": self._job_payload(job), "cover": manifest})

        def _generate_job_segments(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    manifest = generate_platform_segments(
                        settings,
                        job.job_dir,
                        platforms=_string_list(payload.get("platforms")),
                        force=bool(payload.get("force", False)),
                    )
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "segments": manifest})
            finally:
                _end_enhancement(run_key)

        def _generate_job_metadata(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    metadata = generate_metadata(
                        settings,
                        job.job_dir,
                        platform=str(payload.get("platform") or "douyin"),
                        force=bool(payload.get("force", False)),
                    )
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "metadata": metadata})
            finally:
                _end_enhancement(run_key)

        def _save_job_metadata(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    metadata = save_metadata(job.job_dir, payload)
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "metadata": metadata})
            finally:
                _end_enhancement(run_key)

        def _generate_job_highlights(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    highlights = generate_highlights(settings, job.job_dir, force=bool(payload.get("force", False)))
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "highlights": highlights})
            finally:
                _end_enhancement(run_key)

        def _generate_job_publish_package(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    package = generate_publish_package(
                        settings,
                        job.job_dir,
                        platforms=_string_list(payload.get("platforms")),
                        force=bool(payload.get("force", False)),
                    )
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "package": package})
            finally:
                _end_enhancement(run_key)

        def _generate_job_project_export(self, job_name: str) -> None:
            job = self._load_job_for_mutation(job_name)
            if job is None:
                return
            run_key = self._begin_enhancement(job)
            if run_key is None:
                return
            try:
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    export_manifest = generate_project_exports(
                        settings,
                        job.job_dir,
                        targets=_string_list(payload.get("targets")),
                        include_clips=bool(payload.get("include_clips", False)),
                        force=bool(payload.get("force", False)),
                    )
                except Exception as exc:
                    self._json({"error": str(exc)}, status=400)
                    return
                self._json({"job": self._job_payload(job), "project_export": export_manifest})
            finally:
                _end_enhancement(run_key)

        def _start_download(self) -> None:
            payload = self._read_json()
            if payload is None:
                return
            try:
                record = start_download(settings, str(payload.get("url") or ""))
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"download": record}, status=202)

        def _import_download(self, download_id: str, process_semaphore: threading.Semaphore) -> None:
            record = get_download(settings, download_id)
            if record is None:
                self._json({"error": "download not found"}, status=404)
                return
            if record.get("status") != "done" or not record.get("output_path"):
                self._json({"error": f"download is not ready: {record.get('status')}"}, status=409)
                return
            payload = self._read_json()
            if payload is None:
                return
            payload = dict(payload)
            payload["path"] = record["output_path"]
            try:
                job, status = self._submit_process_payload(payload, process_semaphore)
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            self._json({"download": record, "job": job.to_dict()}, status=status)

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
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before deleting"}, status=409)
                return
            shutil.rmtree(job_dir)
            self._json({"deleted": job_name})

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

        def _job_payload(self, job: Job) -> dict[str, Any]:
            payload = job.to_dict()
            payload["files"] = _job_files(job.job_dir)
            return payload

        def _begin_enhancement(self, job: Job) -> str | None:
            if not _job_is_terminal(job):
                self._json({"error": f"job is already {job.status}; wait for it to finish before running enhancements"}, status=409)
                return None
            key = str(job.job_dir.resolve())
            with ENHANCEMENT_RUNS_LOCK:
                if key in ENHANCEMENT_RUNS:
                    self._json({"error": "an enhancement is already running for this job"}, status=409)
                    return None
                ENHANCEMENT_RUNS.add(key)
            return key

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
            self._send_file(path, content_type, attachment=False)

        def _send_file(self, path: Path, content_type: str, *, attachment: bool) -> None:
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
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = handle.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

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


def _end_enhancement(key: str) -> None:
    with ENHANCEMENT_RUNS_LOCK:
        ENHANCEMENT_RUNS.discard(key)


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


def _run_cover_generation(settings: Settings, job_dir: Path, key: str, options: dict[str, Any]) -> None:
    try:
        try:
            generate_cover_candidates(
                settings,
                job_dir,
                title=str(options.get("title") or "").strip(),
                style=str(options.get("style") or "short_video").strip(),
                count=int(options.get("count") or settings.cover_count),
                aspects=[str(value) for value in options.get("aspects", [])] if isinstance(options.get("aspects"), list) else None,
            )
        except Exception as exc:
            manifest = cover_manifest(job_dir)
            manifest["status"] = "failed"
            manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
            manifest["error"] = str(exc)
            write_json_atomic(job_dir / "cover_manifest.json", manifest)
    finally:
        with COVER_GENERATIONS_LOCK:
            COVER_GENERATIONS.discard(key)


def _run_process_job(process_semaphore: threading.Semaphore, settings: Settings, job: Job, options: dict[str, Any]) -> None:
    with process_semaphore:
        process_job(settings, job, **options)


def _run_single_stage(settings: Settings, job: Job, stage: str, options: dict[str, Any]) -> None:
    job.start_stage(RERUN_STATUS[stage], stage, message=f"Rerunning {stage}.")
    try:
        if stage == "probe":
            manifest = probe_media(settings, job.source_path, job.job_dir / "manifest.json", force=True)
            if manifest["audio_stream_count"] < 1:
                raise RuntimeError("source has no audio stream")
            if manifest.get("video_stream_count", 0) > 0:
                generate_thumbnail(settings, job.source_path, job.job_dir / "thumbnail.jpg", manifest["duration_seconds"], force=True)
        elif stage == "detect_corruption":
            manifest = probe_media(settings, job.source_path, job.job_dir / "manifest.json", force=False)
            detect_decode_errors(settings, job.source_path, manifest["duration_seconds"], job.job_dir / "corrupt.json", force=True)
        elif stage == "extract_audio":
            extract_audio(settings, job.source_path, job.job_dir / "audio.wav", force=True)
            extract_high_quality_audio(settings, job.source_path, job.job_dir / "audio_hq.flac", force=True)
            generate_waveform(settings, job.job_dir / "audio.wav", job.job_dir / "waveform.json", force=True)
        elif stage == "transcribe":
            transcribe_audio(settings, job.job_dir / "audio.wav", job.job_dir, force=True)
            if (job.job_dir / "cuts.json").exists():
                generate_cuts(
                    job.job_dir,
                    _manifest_duration(job.job_dir),
                    force=True,
                    min_clip_seconds=settings.cut_min_clip_seconds,
                    merge_gap_seconds=settings.cut_merge_gap_seconds,
                )
                _remove_render_outputs(job.job_dir)
                generate_render_preview(settings, job.job_dir, job.source_path, force=True)
        elif stage == "detect_silence":
            detect_silence(settings, job.job_dir / "audio.wav", _manifest_duration(job.job_dir), job.job_dir / "silence.json", force=True)
        elif stage == "detect_freeze":
            detect_freeze(settings, job.source_path, _manifest_duration(job.job_dir), job.job_dir / "freeze.json", force=True)
        elif stage == "detect_scenes":
            detect_scenes(settings, job.source_path, _manifest_duration(job.job_dir), job.job_dir / "scene.json", force=True)
        elif stage == "plan_cuts":
            generate_cuts(
                job.job_dir,
                _manifest_duration(job.job_dir),
                force=True,
                min_clip_seconds=settings.cut_min_clip_seconds,
                merge_gap_seconds=settings.cut_merge_gap_seconds,
            )
            _remove_render_outputs(job.job_dir)
            generate_render_preview(settings, job.job_dir, job.source_path, force=True)
        elif stage == "style_subtitles":
            generate_ass_subtitles(settings, job.job_dir, force=True)
            generate_clipped_ass_subtitles(settings, job.job_dir, force=True)
        elif stage == "plan_crop":
            generate_vertical_crop_plan(settings, job.job_dir, force=True)
        elif stage == "plan_uvr":
            generate_uvr_plan(settings, job.job_dir, force=True)
        elif stage == "plan_render":
            generate_render_preview(settings, job.job_dir, job.source_path, force=True)
            generate_platform_export_plan(settings, job.job_dir, force=True)
            generate_bgm_mix_plan(settings, job.job_dir, force=True)
            generate_webhook_plan(settings, job.job_dir, force=True)
        elif stage == "render_review":
            render_review_video(settings, job.job_dir, job.source_path, force=True, progress_callback=_progress_callback(job, stage))
        elif stage == "render_final":
            preview = read_json_file(job.job_dir / "final_render_preview.json") or {}
            vertical, burn_subtitles = _infer_final_render_options(job.job_dir, preview, options)
            render_final_video(
                settings,
                job.job_dir,
                job.source_path,
                force=True,
                vertical=vertical,
                burn_subtitles=burn_subtitles,
                progress_callback=_progress_callback(job, stage),
            )
        job.complete_stage()
        job.set_status("needs_review")
    except Exception as exc:
        job.fail(str(exc))


def _progress_callback(job: Job, stage: str):
    def callback(percent: float) -> None:
        value = round(max(0.0, min(100.0, percent)), 2)
        job.update_stage_progress(value, message=f"{stage} progress {value:.1f}%.")

    return callback


def _manifest_duration(job_dir: Path) -> float:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    try:
        return float(manifest.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _infer_final_render_options(job_dir: Path, preview: dict[str, Any], options: dict[str, Any]) -> tuple[bool, bool]:
    vertical = bool(options["vertical"]) if "vertical" in options else bool(preview.get("vertical", False))
    burn_subtitles = (
        bool(options["burn_subtitles"])
        if "burn_subtitles" in options
        else bool(preview.get("burn_subtitles", False))
    )
    if "vertical" not in options and not vertical:
        vertical = _has_vertical_crop_plan(job_dir)
    if "burn_subtitles" not in options and not burn_subtitles:
        burn_subtitles = (job_dir / "subtitles_clipped.ass").exists()
    return vertical, burn_subtitles


def _has_vertical_crop_plan(job_dir: Path) -> bool:
    plan = read_json_file(job_dir / "crop_plan.json") or {}
    target = plan.get("target") if isinstance(plan, dict) else {}
    if isinstance(target, dict):
        try:
            width = int(target.get("width") or 0)
            height = int(target.get("height") or 0)
        except (TypeError, ValueError):
            width = 0
            height = 0
        if width > 0 and height > width:
            return True
    filter_text = str(plan.get("ffmpeg_filter") or "") if isinstance(plan, dict) else ""
    return "1080:1920" in filter_text or "crop=1080:1920" in filter_text


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
