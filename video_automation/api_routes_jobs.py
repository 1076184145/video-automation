from __future__ import annotations

import shutil
import uuid
from typing import Any

from .api_job_utils import (
    job_feedback,
    job_files,
    job_is_terminal,
    job_runtime_state,
    record_transcript_preferences,
    remove_render_outputs,
    save_clip_feedback,
    transcript_summary,
    update_transcript_from_editor,
)
from .api_system import schedule_tombstone_cleanup
from .cuts import update_cuts_from_editor
from .io_utils import read_json_file, write_json_atomic
from .jobs import Job, list_jobs, load_job
from .library_api import (
    delete_job_records,
    evaluate_job_quality,
    job_library_fields,
    job_library_fields_map,
    library_database_path,
    preference_repository_for,
    record_job_revision,
    structured_error,
)
from .pipeline_spec import PIPELINE_STAGE_SPECS
from .render import generate_render_preview
from .routing import RouteMatch
from .runtime_config import snapshot_runtime_settings
from .stage_runs import StageRunRepository
from .subtitles import generate_ass_subtitles, generate_clipped_ass_subtitles


RERUN_STATUS = frozenset(PIPELINE_STAGE_SPECS)


class JobRoutes:
    """Job lifecycle, review editing, queue control, and shared job helpers."""

    def _route_jobs(self, _matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        jobs = list_jobs(settings)
        library_fields = job_library_fields_map(
            settings,
            [job.job_dir.name for job in jobs],
        )
        self._json(
            [
                self._job_payload(
                    job,
                    library_fields=library_fields.get(job.job_dir.name),
                )
                for job in jobs
            ]
        )

    def _route_job(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job_name = matched.params.get("job_name", "")
        job = load_job(settings.jobs_dir / job_name / "job.json")
        if job is None:
            self._json({"error": "job not found"}, status=404)
            return
        payload = self._job_payload(job, include_runtime=True)
        payload["pipeline_runs"] = StageRunRepository(
            library_database_path(settings)
        ).list_for_job(job_name)
        latest_run = payload["pipeline_runs"][0] if payload["pipeline_runs"] else None
        payload["active_stages"] = [
            stage
            for stage in (latest_run or {}).get("stages", [])
            if stage.get("status") == "running"
        ]
        self._json(payload)

    def _route_approve_job(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job_name = matched.params.get("job_name", "")
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
            self._json(
                {"error": f"job is not waiting for review: {job.status}"},
                status=409,
            )
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
        payload["files"] = job_files(job.job_dir)
        self._json(payload)

    def _route_update_job_cuts(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if not job_is_terminal(job):
            self._json(
                {"error": f"job is already {job.status}; wait for it to finish before editing cuts"},
                status=409,
            )
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            cuts = update_cuts_from_editor(job.job_dir, payload.get("clips", []))
            generate_clipped_ass_subtitles(settings, job.job_dir, force=True)
            remove_render_outputs(job.job_dir)
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

    def _route_update_job_transcript(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if not job_is_terminal(job):
            self._json(
                {
                    "error": (
                        f"job is already {job.status}; wait for it to finish "
                        "before editing transcript"
                    )
                },
                status=409,
            )
            return
        payload = self._read_json()
        if payload is None:
            return
        previous_transcript = read_json_file(job.job_dir / "transcript.json") or {}
        try:
            transcript = update_transcript_from_editor(
                job.job_dir,
                payload.get("segments", []),
            )
            cuts_path = job.job_dir / "cuts.json"
            if cuts_path.exists():
                cuts = read_json_file(cuts_path) or {}
                cuts["transcript_segments"] = transcript_summary(transcript)
                write_json_atomic(cuts_path, cuts)
                update_cuts_from_editor(job.job_dir, cuts.get("clips", []))
            generate_ass_subtitles(settings, job.job_dir, force=True)
            generate_clipped_ass_subtitles(settings, job.job_dir, force=True)
            remove_render_outputs(job.job_dir)
            generate_render_preview(settings, job.job_dir, job.source_path, force=True)
            job.set_status("needs_review")
            revision = record_job_revision(
                settings,
                job.job_dir.name,
                "transcript",
                transcript,
                summary="Saved transcript edits",
            )
            record_transcript_preferences(
                preference_repository_for(settings),
                job.job_dir.name,
                previous_transcript,
                transcript,
            )
        except Exception as exc:
            self._json({"error": str(exc)}, status=400)
            return
        self._json(
            {"job": self._job_payload(job), "transcript": transcript, "revision": revision}
        )

    def _route_save_clip_feedback(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            feedback = save_clip_feedback(job.job_dir, payload)
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

    def _route_rerun_job_stage(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        queue_repository = self.api_context.queue_repository
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if not job_is_terminal(job):
            self._json(
                {"error": f"job is already {job.status}; wait for it to finish"},
                status=409,
            )
            return
        if self._job_runtime(job).get("active"):
            self._json(
                {"error": "another managed task is already active for this job"},
                status=409,
            )
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

    def _route_cancel_job(self, matched: RouteMatch, _query: str) -> None:
        queue_repository = self.api_context.queue_repository
        job = self._load_job_for_mutation(matched.params.get("job_name", ""))
        if job is None:
            return
        if job_is_terminal(job):
            self._json({"error": f"job is already {job.status}"}, status=409)
            return
        runtime = self._job_runtime(job)
        queue_item = runtime.get("queue")
        if isinstance(queue_item, dict) and queue_item.get("status") in {
            "pending",
            "paused",
            "running",
        }:
            updated = queue_repository.cancel(str(queue_item.get("id") or ""))
            if updated and updated.get("status") == "canceled":
                job.cancel()
        elif runtime.get("stale"):
            job.cancel("Stopped stale job state because no active worker was found")
        else:
            self._json(
                {
                    "error": (
                        "job is active outside the managed queue and cannot be canceled safely"
                    )
                },
                status=409,
            )
            return
        self._json(self._job_payload(job, include_runtime=True))

    def _route_delete_job(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job_name = matched.params.get("job_name", "")
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
            self._json(
                {
                    "error": (
                        "job still has an active worker; wait for cancellation to finish "
                        "before deleting"
                    )
                },
                status=409,
            )
            return
        if not job_is_terminal(job) and not runtime.get("stale"):
            self._json(
                {"error": f"job is already {job.status}; wait for it to finish before deleting"},
                status=409,
            )
            return
        tombstone = job_dir.with_name(f".{job_dir.name}.deleting-{uuid.uuid4().hex}")
        try:
            job_dir.replace(tombstone)
        except OSError:
            self._json(
                {"error": "job files are still in use", "code": "job_files_in_use"},
                status=409,
            )
            return
        try:
            delete_job_records(settings, job_name)
        except Exception:
            try:
                tombstone.replace(job_dir)
            except OSError:
                schedule_tombstone_cleanup(tombstone)
            self._json(
                {
                    "error": "job metadata could not be deleted",
                    "code": "job_delete_failed",
                },
                status=500,
            )
            return
        try:
            shutil.rmtree(tombstone)
        except OSError:
            schedule_tombstone_cleanup(tombstone)
            self._json({"deleted": job_name, "cleanup_pending": True}, status=202)
            return
        self._json({"deleted": job_name, "cleanup_pending": False})

    def _load_job_for_mutation(self, job_name: str) -> Job | None:
        settings = self.api_context.settings
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
        settings = self.api_context.settings
        payload = job.to_dict()
        payload["files"] = job_files(job.job_dir)
        payload["feedback"] = job_feedback(job.job_dir)
        payload.update(library_fields or job_library_fields(settings, job.job_dir.name))
        if include_runtime:
            payload["runtime"] = self._job_runtime(job)
        return payload

    def _job_runtime(self, job: Job) -> dict[str, Any]:
        settings = self.api_context.settings
        queue_item = self.api_context.queue_repository.get_by_job(job.job_dir.name)
        pipeline_runs = StageRunRepository(
            library_database_path(settings)
        ).list_for_job(job.job_dir.name, limit=1)
        return job_runtime_state(job.status, queue_item, pipeline_runs)

    def _allow_quick_mutation(self, job: Job) -> bool:
        if not job_is_terminal(job):
            self._json(
                {
                    "error": (
                        f"job is already {job.status}; wait for it to finish "
                        "before running enhancements"
                    )
                },
                status=409,
            )
            return False
        if self._job_runtime(job).get("active"):
            self._json(
                {"error": "another managed task is already active for this job"},
                status=409,
            )
            return False
        return True

    def _enqueue_job_command(
        self,
        job: Job,
        command: str,
        command_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        settings = self.api_context.settings
        queue_repository = self.api_context.queue_repository
        if not job_is_terminal(job):
            self._json(
                {"error": f"job is already {job.status}; wait for it to finish"},
                status=409,
            )
            return None
        current = queue_repository.get_by_job(job.job_dir.name)
        if current and current.get("status") in {"pending", "running", "paused"}:
            self._json(
                {"error": "another managed task is already active for this job"},
                status=409,
            )
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
        self._json(
            {
                "job": self._job_payload(job),
                "status": "queued",
                "command": command,
                "queue": queue_item,
            },
            status=202,
        )
