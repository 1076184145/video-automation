from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
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
    validate_transcript_segments,
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
        job_dir = self._safe_job_dir(job_name)
        if job_dir is None:
            return
        job = load_job(job_dir / "job.json")
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
        save_state = transcript_save_state(job)
        if save_state is not None:
            payload["transcript_save"] = save_state
        self._json(payload)

    def _route_approve_job(self, matched: RouteMatch, _query: str) -> None:
        settings = self.api_context.settings
        job_name = matched.params.get("job_name", "")
        job_dir = self._safe_job_dir(job_name)
        if job_dir is None:
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
        key = _held_transcript_key(job)
        lock = _transcript_apply_lock(key)
        generation = _transcript_save_generation(key)
        if transcript_save_is_held(job) or lock.locked():
            self._json({"error": TRANSCRIPT_SAVE_BUSY}, status=409)
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
        # Commit only if no transcript save was accepted or applied while the gate ran: such a save
        # replaces the files the gate just checked. Saves take this lock while they decide and apply.
        if not lock.acquire(blocking=False):
            self._json({"error": TRANSCRIPT_SAVE_BUSY}, status=409)
            return
        try:
            if transcript_save_is_held(job):
                self._json({"error": TRANSCRIPT_SAVE_BUSY}, status=409)
                return
            if _transcript_save_generation(key) != generation:
                self._json(
                    {"error": "the transcript was saved during approval; review the job and approve again"},
                    status=409,
                )
                return
            job = load_job(job_dir / "job.json") or job
            if job.status != "needs_review":
                self._json(
                    {"error": f"job is not waiting for review: {job.status}"},
                    status=409,
                )
                return
            job.set_status("done")
        finally:
            lock.release()
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
        if self._transcript_save_mode(job) == "busy":
            self._transcript_busy(job)
            return
        payload = self._read_json()
        if payload is None:
            return
        segments = payload.get("segments", [])
        key = _held_transcript_key(job)
        with _transcript_apply_lock(key):
            # Decide again under the lock. Queue ownership is authoritative: a worker writes
            # needs_review to job.json before it releases its queue item.
            job = load_job(job.job_dir / "job.json") or job
            mode = self._transcript_save_mode(job)
            if mode == "busy":
                self._transcript_busy(job)
                return
            # A rejected edit changes nothing: an edit held earlier stays held, a failure stays reported.
            try:
                if not isinstance(segments, list):
                    raise RuntimeError("segments must be a list")
                validate_transcript_segments(segments)
            except Exception as exc:
                self._json({"error": str(exc)}, status=400)
                return
            if mode == "hold":
                # A managed task (e.g. the review re-render after "Save cuts") owns the job files.
                # Hold the edit and apply it once when that task ends.
                hold_transcript_save(self.api_context, job, segments)
                self._json({"job": self._job_payload(job), "pending": True}, status=202)
                return
            # This newer full transcript supersedes a held edit, whether or not it succeeds.
            _bump_transcript_save_generation(key)
            try:
                transcript, revision = apply_transcript_save(settings, job, segments)
            except Exception as exc:
                _record_transcript_save_outcome(key, {"state": "failed", "error": str(exc)})
                self._json({"error": str(exc)}, status=400)
                return
            _clear_transcript_save_state(key)
        self._json(
            {"job": self._job_payload(job), "transcript": transcript, "revision": revision}
        )

    def _transcript_save_mode(self, job: Job) -> str:
        queue_item = self.api_context.queue_repository.get_by_job(job.job_dir.name)
        if str((queue_item or {}).get("status") or "") in {"pending", "paused", "running"}:
            return "hold"
        return "direct" if job_is_terminal(job) else "busy"

    def _transcript_busy(self, job: Job) -> None:
        self._json(
            {
                "error": (
                    f"job is already {job.status}; wait for it to finish "
                    "before editing transcript"
                )
            },
            status=409,
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
        job_dir = self._safe_job_dir(job_name)
        if job_dir is None:
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
        job_dir = self._safe_job_dir(job_name)
        if job_dir is None:
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
        return _job_runtime_for(self.api_context, job)

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


def _job_runtime_for(api_context: Any, job: Job) -> dict[str, Any]:
    settings = api_context.settings
    queue_item = api_context.queue_repository.get_by_job(job.job_dir.name)
    pipeline_runs = StageRunRepository(
        library_database_path(settings)
    ).list_for_job(job.job_dir.name, limit=1)
    return job_runtime_state(job.status, queue_item, pipeline_runs)


def apply_transcript_save(settings: Any, job: Job, segments: Any) -> tuple[dict[str, Any], Any]:
    """Write a transcript edit and everything derived from it (the 200 path of the transcript route).

    The file steps are all or nothing: if one fails, every file they touched is put back as it was,
    so the gate never sees an edited transcript next to subtitles and a preview from before it."""
    previous_transcript = read_json_file(job.job_dir / "transcript.json") or {}
    backup = _TranscriptFilesBackup(job.job_dir)
    try:
        transcript = update_transcript_from_editor(job.job_dir, segments)
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
    except BaseException:
        backup.restore()
        raise
    backup.discard()
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
    return transcript, revision


# Every file a transcript save rewrites or removes. Small files are kept in memory; render videos are
# moved to a hidden sibling folder (same volume, so a rename) and deleted only once the save succeeded.
_TRANSCRIPT_SAVE_FILES = (
    "transcript.json", "transcript.txt", "transcript.srt", "cuts.json", "cuts.md",
    "clip_refinement.json", "subtitles.ass", "subtitles_clipped.ass",
    "render_preview.json", "render_review.ps1", "final_render_preview.json",
)
_TRANSCRIPT_SAVE_VIDEOS = ("review.mp4", "final.mp4")


class _TranscriptFilesBackup:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir
        self.files = {
            name: (job_dir / name).read_bytes() if (job_dir / name).is_file() else None
            for name in _TRANSCRIPT_SAVE_FILES
        }
        self.video_dir = job_dir.parent / f".{job_dir.name}.transcript-save-{uuid.uuid4().hex}"
        self.videos: list[str] = []
        try:
            for name in _TRANSCRIPT_SAVE_VIDEOS:
                if (job_dir / name).is_file():
                    self.video_dir.mkdir(exist_ok=True)
                    (job_dir / name).replace(self.video_dir / name)
                    self.videos.append(name)
        except BaseException:
            self.restore()
            raise

    def restore(self) -> None:
        for name, data in self.files.items():
            path = self.job_dir / name
            if data is None:
                path.unlink(missing_ok=True)
            elif not path.is_file() or path.read_bytes() != data:
                temporary = path.with_name(f".{name}.restore-{uuid.uuid4().hex}.tmp")
                temporary.write_bytes(data)
                os.replace(temporary, path)
        for name in self.videos:
            (self.video_dir / name).replace(self.job_dir / name)
        self.videos = []
        self._remove_video_dir()

    def discard(self) -> None:
        try:
            for name in self.videos:
                (self.video_dir / name).unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("Could not remove %s", self.video_dir, exc_info=True)
        self.videos = []
        self._remove_video_dir()

    def _remove_video_dir(self) -> None:
        try:
            self.video_dir.rmdir()
        except OSError:
            pass


# Transcript edits sent while a managed task owns the job are held here (in memory, so nothing new
# appears in the job directory) and applied exactly once, by one applier thread per job, after the
# task ends. A held entry stays until its apply has finished ("pending", then "applying"), so Approve
# is refused for that whole time; the result ("applied" or "failed") is kept until the next save so
# the page can tell a confirmed apply from a failed one. Saves and applies for a job share its lock.
# Each accepted or applied save bumps the job's generation; Approve commits only if it did not change.
HELD_TRANSCRIPT_POLL_SECONDS = 0.25
TRANSCRIPT_SAVE_BUSY = "a transcript save is still being applied; try again in a moment"
_held_transcripts: dict[str, dict[str, Any]] = {}
_transcript_save_outcomes: dict[str, dict[str, Any]] = {}
_transcript_save_generations: dict[str, int] = {}
_held_transcripts_lock = threading.Lock()
_transcript_apply_locks: dict[str, threading.Lock] = {}


def _held_transcript_key(job: Job) -> str:
    return str(job.job_dir.resolve())


def _transcript_apply_lock(key: str) -> threading.Lock:
    with _held_transcripts_lock:
        return _transcript_apply_locks.setdefault(key, threading.Lock())


def _clear_transcript_save_state(key: str) -> None:
    with _held_transcripts_lock:
        _held_transcripts.pop(key, None)
        _transcript_save_outcomes.pop(key, None)


def _record_transcript_save_outcome(key: str, outcome: dict[str, Any]) -> None:
    with _held_transcripts_lock:
        _held_transcripts.pop(key, None)
        _transcript_save_outcomes[key] = outcome


def _transcript_save_generation(key: str) -> int:
    with _held_transcripts_lock:
        return _transcript_save_generations.get(key, 0)


def _bump_transcript_save_generation(key: str) -> None:
    with _held_transcripts_lock:
        _transcript_save_generations[key] = _transcript_save_generations.get(key, 0) + 1


def transcript_save_is_held(job: Job) -> bool:
    with _held_transcripts_lock:
        return _held_transcript_key(job) in _held_transcripts


def transcript_save_state(job: Job) -> dict[str, Any] | None:
    key = _held_transcript_key(job)
    with _held_transcripts_lock:
        held = _held_transcripts.get(key)
        if held is not None:
            return {"state": held["state"], "error": None}
        outcome = _transcript_save_outcomes.get(key)
        return dict(outcome) if outcome is not None else None


def hold_transcript_save(api_context: Any, job: Job, segments: list[Any]) -> None:
    key = _held_transcript_key(job)
    with _held_transcripts_lock:
        start_applier = key not in _held_transcripts
        # The client sends the full transcript, so the latest edit wins.
        _held_transcripts[key] = {"segments": segments, "state": "pending"}
        _transcript_save_outcomes.pop(key, None)
        _transcript_save_generations[key] = _transcript_save_generations.get(key, 0) + 1
    if start_applier:
        threading.Thread(
            target=_apply_held_transcript_when_idle,
            args=(api_context, job.job_dir, key),
            name=f"held-transcript-{job.job_dir.name}",
            daemon=True,
        ).start()


def _job_is_idle(api_context: Any, job: Job) -> bool:
    try:
        return job_is_terminal(job) and not _job_runtime_for(api_context, job)["active"]
    except Exception:
        return False


def _apply_held_transcript_when_idle(api_context: Any, job_dir: Any, key: str) -> None:
    while True:
        time.sleep(HELD_TRANSCRIPT_POLL_SECONDS)
        with _held_transcripts_lock:
            if key not in _held_transcripts:
                return
        job = load_job(job_dir / "job.json")
        if job is None:
            _clear_transcript_save_state(key)
            return
        if not _job_is_idle(api_context, job):
            continue
        with _transcript_apply_lock(key):
            job = load_job(job_dir / "job.json")
            if job is None or not _job_is_idle(api_context, job):
                continue
            with _held_transcripts_lock:
                held = _held_transcripts.get(key)
                if held is None:
                    return
                held["state"] = "applying"
                _transcript_save_generations[key] = _transcript_save_generations.get(key, 0) + 1
            try:
                apply_transcript_save(api_context.settings, job, held["segments"])
                outcome = {"state": "applied", "error": None}
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "Could not apply the held transcript save for %s", job_dir.name
                )
                outcome = {"state": "failed", "error": str(exc)}
            _record_transcript_save_outcome(key, outcome)
            return
