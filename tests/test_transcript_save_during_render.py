from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation import api_routes_jobs
from video_automation.api import create_server
from video_automation.jobs import load_job
from video_automation.library_api import repository_for

ORIGINAL = [{"start": 0.0, "end": 1.0, "text": "original line"}, {"start": 1.0, "end": 2.0, "text": "second line"}]
BUSY_ERROR = "job is already rendering_review; wait for it to finish before editing transcript"


class TranscriptSaveDuringRenderTests(unittest.TestCase):
    """The transcript route while a managed task (the "Save cuts" review re-render) owns the job."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(
            root=root,
            jobs_dir=root / "processing" / "jobs",
            api_host="127.0.0.1",
            api_port=0,
            api_parallel_jobs=1,
            api_allowed_origins=(),
            api_batch_limit=10,
        )
        self.job_dir = self.settings.jobs_dir / "job-one"
        self.job_dir.mkdir(parents=True)
        self._write_job("rendering_review")
        (self.job_dir / "transcript.json").write_text(json.dumps({"segments": ORIGINAL}), encoding="utf-8")
        repository_for(self.settings).index_existing_jobs(self.settings.jobs_dir)
        # Subtitle and preview generation need FFmpeg-era settings; the route behavior is what is under test.
        self.generated = []
        self.hooks = {}  # name -> callable run inside the patched step (test barrier or forced failure)
        for name in ("generate_ass_subtitles", "generate_clipped_ass_subtitles", "generate_render_preview"):
            patcher = patch.object(api_routes_jobs, name, side_effect=lambda *a, _n=name, **k: self._generate(_n, a[1]))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = create_server(self.settings, start_queue_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.queue = self.server.RequestHandlerClass.api_context.queue_repository

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp_dir.cleanup()

    def _write_job(self, status: str) -> None:
        (self.job_dir / "job.json").write_text(json.dumps({
            "source_path": str(Path(self.temp_dir.name) / "source.mp4"),
            "job_dir": str(self.job_dir),
            "status": status,
            "created_at": "2026-11-25T10:00:00",
            "updated_at": "2026-11-25T10:00:00",
        }), encoding="utf-8")

    def _generate(self, name: str, job_dir: Path) -> None:
        """Stand-in for subtitle/preview generation: writes the same files, derived from transcript.json."""
        self.generated.append(name)
        hook = self.hooks.get(name)
        if hook is not None:
            hook()
        texts = " | ".join(self._texts())
        if name == "generate_ass_subtitles":
            (job_dir / "subtitles.ass").write_text(f"[Events]\nDialogue: {texts}\n", encoding="utf-8")
        elif name == "generate_clipped_ass_subtitles":
            (job_dir / "subtitles_clipped.ass").write_text(f"[Events]\nDialogue: {texts}\n", encoding="utf-8")
        else:
            (job_dir / "render_preview.json").write_text(json.dumps({"for": texts}), encoding="utf-8")
            (job_dir / "render_review.ps1").write_text(f"# {texts}\n", encoding="utf-8")

    def _make_approvable(self) -> None:
        """What the real quality gate needs: a non-empty review video and a known duration."""
        (self.job_dir / "review.mp4").write_bytes(b"approved review video")
        (self.job_dir / "manifest.json").write_text(json.dumps({"duration_seconds": 30}), encoding="utf-8")

    def _job_files(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted(self.job_dir.iterdir())
                if path.is_file() and path.name != "job.json" and not path.name.startswith(".")}

    def _approve_in_thread(self, barrier_entered: threading.Event, barrier_release: threading.Event) -> dict:
        """Approve with the real quality gate, paused after the gate and before the status write."""
        real_gate = api_routes_jobs.evaluate_job_quality

        def paused_gate(*args, **kwargs):
            result = real_gate(*args, **kwargs)
            barrier_entered.set()
            barrier_release.wait(15)
            return result
        result: dict = {}
        patcher = patch.object(api_routes_jobs, "evaluate_job_quality", side_effect=paused_gate)
        patcher.start()
        self.addCleanup(patcher.stop)

        def run() -> None:
            result["response"] = self._post("/jobs/job-one/approve", {})
        result["thread"] = threading.Thread(target=run, daemon=True)
        result["thread"].start()
        result["patcher"] = patcher
        return result

    def _request(self, method: str, path: str, payload: object = None) -> tuple[int, dict]:
        host, port = self.server.server_address
        # GETs are retried: job_files() can stat a job.json temp file that vanishes during a concurrent
        # save and drop the connection (a separate, pre-existing issue). POSTs are never retried.
        for attempt in range(1 if method == "POST" else 10):
            connection = http.client.HTTPConnection(host, port, timeout=10)
            try:
                body = None if payload is None else json.dumps(payload).encode("utf-8")
                connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read().decode("utf-8"))
            except (ConnectionError, http.client.RemoteDisconnected):
                if method == "POST" or attempt == 9:
                    raise
                time.sleep(0.05)
            finally:
                connection.close()
        raise AssertionError("unreachable")

    def _post(self, path: str, payload: object) -> tuple[int, dict]:
        return self._request("POST", path, payload)

    def _save_state(self) -> object:
        return self._request("GET", "/jobs/job-one")[1].get("transcript_save")

    def _save(self, segments: object) -> tuple[int, dict]:
        return self._post("/jobs/job-one/transcript", {"segments": segments})

    def _start_managed_render(self) -> str:
        item = self.queue.enqueue("job-one", {"path": "source.mp4"})
        claimed = self.queue.claim_next(worker_pid=os.getpid())
        self.assertEqual(claimed["id"], item["id"])
        self.assertEqual(self.queue.get_by_job("job-one")["status"], "running")
        return str(item["id"])

    def _finish_render(self, queue_id: str, *, complete_queue: bool = True) -> None:
        load_job(self.job_dir / "job.json").set_status("needs_review")
        if complete_queue:
            self.queue.complete(queue_id)

    def _texts(self) -> list[str]:
        transcript = json.loads((self.job_dir / "transcript.json").read_text(encoding="utf-8"))
        return [segment["text"] for segment in transcript["segments"]]

    def _transcript_revisions(self) -> int:
        items = repository_for(self.settings).list_revisions("job-one")
        return sum(1 for item in items if item.get("kind") == "transcript")

    def _wait_for(self, condition, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.05)
        return condition()

    def _edited(self, *texts: str) -> list[dict]:
        return [dict(segment, text=text) for segment, text in zip(ORIGINAL, texts, strict=True)]

    def test_save_during_managed_render_is_held_then_applied_once(self) -> None:
        queue_id = self._start_managed_render()
        status, body = self._save(self._edited("edited line", "second line"))
        self.assertEqual(status, 202)
        self.assertEqual(set(body), {"job", "pending"})
        self.assertIs(body["pending"], True)
        self.assertEqual(body["job"]["status"], "rendering_review")
        time.sleep(0.6)  # several applier polls while the render still owns the job
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.assertEqual(self._transcript_revisions(), 0)
        self.assertEqual(self.generated, [])

        self._finish_render(queue_id)
        self.assertTrue(self._wait_for(lambda: self._texts() == ["edited line", "second line"]))
        self.assertTrue(self._wait_for(lambda: self._transcript_revisions() == 1))
        time.sleep(0.6)
        self.assertEqual(self._transcript_revisions(), 1)
        self.assertEqual(self.generated.count("generate_render_preview"), 1)
        self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")
        self.assertFalse(api_routes_jobs.transcript_save_is_held(load_job(self.job_dir / "job.json")))

    def test_two_saves_during_one_render_apply_the_latest_full_transcript_once(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("first edit", "second line"))[0], 202)
        self.assertEqual(self._save(self._edited("first edit", "second edit"))[0], 202)
        self._finish_render(queue_id)
        self.assertTrue(self._wait_for(lambda: self._texts() == ["first edit", "second edit"]))
        time.sleep(0.6)
        self.assertEqual(self._transcript_revisions(), 1)
        self.assertEqual(self.generated.count("generate_render_preview"), 1)

    def test_busy_job_without_a_managed_task_still_returns_409(self) -> None:
        status, body = self._save(self._edited("edited line", "second line"))
        self.assertEqual((status, body), (409, {"error": BUSY_ERROR}))
        self.assertFalse(api_routes_jobs.transcript_save_is_held(load_job(self.job_dir / "job.json")))

    def test_invalid_segments_during_render_are_rejected_and_not_held(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save("not a list"), (400, {"error": "segments must be a list"}))
        self.assertEqual(self._save([{"text": "no timing"}]),
                         (400, {"error": "transcript segment 1 start/end is invalid"}))
        self.assertFalse(api_routes_jobs.transcript_save_is_held(load_job(self.job_dir / "job.json")))
        self._finish_render(queue_id)
        time.sleep(0.6)
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.assertEqual(self._transcript_revisions(), 0)

    def test_approve_waits_until_the_held_save_is_applied(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
        self._finish_render(queue_id, complete_queue=False)  # job reads needs_review, worker still owns the item
        status, body = self._post("/jobs/job-one/approve", {})
        self.assertEqual((status, body), (409, {"error": "a transcript save is still being applied; try again in a moment"}))
        self.queue.complete(queue_id)
        # The guard holds until the whole apply has returned, so wait for the confirmed outcome.
        self.assertTrue(self._wait_for(lambda: self._save_state() == {"state": "applied", "error": None}))
        self.assertEqual(self._transcript_revisions(), 1)
        # Once applied, approval goes on to the existing quality gate. A transcript save always removes
        # the render outputs, so this fixture (no video) is then blocked by "render_missing", as on base.
        status, body = self._post("/jobs/job-one/approve", {})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "quality_gate_failed")
        self.assertIn("render_missing", {item["code"] for item in body["error"]["details"]["blocking"]})

    def test_save_is_held_while_the_worker_still_owns_a_job_that_reads_needs_review(self) -> None:
        # Blocker 1: the worker writes needs_review before it releases its queue item.
        queue_id = self._start_managed_render()
        self._finish_render(queue_id, complete_queue=False)
        self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")
        (self.job_dir / "review.mp4").write_bytes(b"review video being finalized")
        status, body = self._save(self._edited("edited line", "second line"))
        self.assertEqual(status, 202, body)
        self.assertEqual(set(body), {"job", "pending"})
        time.sleep(0.8)  # several applier polls while the queue item still says running
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.assertEqual((self.job_dir / "review.mp4").read_bytes(), b"review video being finalized")
        self.assertEqual(self._transcript_revisions(), 0)
        self.assertEqual(self.generated, [])
        self.assertEqual(self._save_state(), {"state": "pending", "error": None})

        self.queue.complete(queue_id)
        self.assertTrue(self._wait_for(lambda: self._transcript_revisions() == 1))
        time.sleep(0.6)
        self.assertEqual(self._texts(), ["edited line", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)
        self.assertEqual(self.generated.count("generate_render_preview"), 1)
        self.assertFalse((self.job_dir / "review.mp4").exists())  # removed by the apply, as by any transcript save
        self.assertEqual(self._save_state(), {"state": "applied", "error": None})

    def test_second_save_while_the_worker_still_owns_the_job_replaces_the_held_edit(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("held edit", "second line"))[0], 202)
        self._finish_render(queue_id, complete_queue=False)
        status, body = self._save(self._edited("newer edit", "second line"))
        self.assertEqual(status, 202, body)
        self.queue.complete(queue_id)
        self.assertTrue(self._wait_for(lambda: self._transcript_revisions() == 1))
        time.sleep(0.6)
        self.assertEqual(self._texts(), ["newer edit", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)

    def test_direct_save_after_release_supersedes_a_held_edit_not_yet_applied(self) -> None:
        with patch.object(api_routes_jobs, "HELD_TRANSCRIPT_POLL_SECONDS", 3.0):
            queue_id = self._start_managed_render()
            self.assertEqual(self._save(self._edited("held edit", "second line"))[0], 202)
            self._finish_render(queue_id)  # released; the applier is still in its first 3 s poll
            status, body = self._save(self._edited("newer edit", "second line"))
            self.assertEqual(status, 200, body)
            self.assertEqual(set(body), {"job", "transcript", "revision"})
            self.assertIsNone(self._save_state())
            time.sleep(3.5)  # the applier wakes, finds nothing held, and exits
        self.assertEqual(self._texts(), ["newer edit", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)
        for name in ("subtitles.ass", "subtitles_clipped.ass"):
            text = (self.job_dir / name).read_text(encoding="utf-8")
            self.assertIn("newer edit", text)
            self.assertNotIn("held edit", text)

    def test_approve_stays_blocked_while_the_held_save_is_being_applied(self) -> None:
        # Blocker 2: the guard must cover the apply itself, not only the wait before it.
        entered, release = threading.Event(), threading.Event()
        self.hooks["generate_ass_subtitles"] = lambda: (entered.set(), release.wait(15))
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
        self._finish_render(queue_id)
        self.assertTrue(entered.wait(10), "the applier did not reach the barrier")
        guard = (409, {"error": "a transcript save is still being applied; try again in a moment"})
        try:
            # A permissive quality gate shows that the transcript guard, not the gate, refuses approval.
            with patch.object(api_routes_jobs, "evaluate_job_quality", return_value={"blocking": []}):
                for _ in range(4):
                    self.assertEqual(self._post("/jobs/job-one/approve", {}), guard)
                    self.assertEqual(self._save_state(), {"state": "applying", "error": None})
                    time.sleep(0.2)
            self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")
        finally:
            release.set()
        self.assertTrue(self._wait_for(lambda: self._save_state() == {"state": "applied", "error": None}))
        self.assertEqual(self._transcript_revisions(), 1)
        status, body = self._post("/jobs/job-one/approve", {})  # the real gate: the apply removed the render
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "quality_gate_failed")
        self.assertIn("render_missing", {item["code"] for item in body["error"]["details"]["blocking"]})

    def test_failed_held_apply_is_reported_and_a_retry_applies_everything(self) -> None:
        # Blocker 3: a failure after transcript.json was written must stay visible, never look applied.
        def fail() -> None:
            raise RuntimeError("forced subtitle failure")
        self.hooks["generate_ass_subtitles"] = fail
        self._make_approvable()
        (self.job_dir / "subtitles.ass").write_text("[Events]\nDialogue: original line | second line\n", encoding="utf-8")
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
        self._finish_render(queue_id)
        before = self._job_files()
        self.assertTrue(self._wait_for(lambda: (self._save_state() or {}).get("state") == "failed"))
        self.assertEqual(self._save_state(), {"state": "failed", "error": "forced subtitle failure"})
        # The failure came after transcript.json was rewritten; the save is undone completely.
        self.assertEqual(self._job_files(), before)
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.assertEqual(self._transcript_revisions(), 0)
        self.assertEqual(self.generated, ["generate_ass_subtitles"])
        self.assertFalse(api_routes_jobs.transcript_save_is_held(load_job(self.job_dir / "job.json")))
        self.assertEqual([p.name for p in self.settings.jobs_dir.iterdir()], ["job-one"])  # no backup left

        del self.hooks["generate_ass_subtitles"]
        status, body = self._save(self._edited("edited line", "second line"))  # the retry, job now idle
        self.assertEqual(status, 200, body)
        self.assertEqual(self._transcript_revisions(), 1)
        self.assertEqual(self.generated[1:], ["generate_ass_subtitles", "generate_clipped_ass_subtitles",
                                              "generate_render_preview"])
        self.assertIsNone(self._save_state())
        for name in ("subtitles.ass", "subtitles_clipped.ass"):
            self.assertIn("edited line", (self.job_dir / name).read_text(encoding="utf-8"))

    def test_approval_after_a_restored_failure_goes_through_the_real_gate(self) -> None:
        # P1-A, second case: the gate must never pass against a half-applied edit.
        self._make_approvable()
        self._write_job("needs_review")

        def fail() -> None:
            raise RuntimeError("forced subtitle failure")
        self.hooks["generate_ass_subtitles"] = fail
        before = self._job_files()
        self.assertEqual(self._save(self._edited("edited line", "second line")), (400, {"error": "forced subtitle failure"}))
        self.assertEqual(self._job_files(), before)  # previous state fully restored, review.mp4 included
        self.assertEqual(self._transcript_revisions(), 0)
        status, body = self._post("/jobs/job-one/approve", {})  # the real gate, on consistent files
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "done")

    def test_late_failure_restores_the_removed_review_video(self) -> None:
        # The preview step runs after the render outputs were removed; a failure there restores them too.
        self._make_approvable()
        self._write_job("needs_review")

        def fail() -> None:
            raise RuntimeError("forced preview failure")
        self.hooks["generate_render_preview"] = fail
        before = self._job_files()
        self.assertEqual(self._save(self._edited("edited line", "second line")), (400, {"error": "forced preview failure"}))
        self.assertEqual(self._job_files(), before)
        self.assertEqual((self.job_dir / "review.mp4").read_bytes(), b"approved review video")
        self.assertEqual(self._save_state(), {"state": "failed", "error": "forced preview failure"})

    def test_failed_retry_keeps_a_queryable_failure(self) -> None:
        # P1-B: a failed retry must not erase the earlier "failed" state.
        def fail() -> None:
            raise RuntimeError("forced subtitle failure")
        self.hooks["generate_ass_subtitles"] = fail
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
        self._finish_render(queue_id)
        self.assertTrue(self._wait_for(lambda: (self._save_state() or {}).get("state") == "failed"))
        self.hooks["generate_ass_subtitles"] = lambda: (_ for _ in ()).throw(RuntimeError("retry also failed"))
        self.assertEqual(self._save(self._edited("edited line", "second line")), (400, {"error": "retry also failed"}))
        self.assertEqual(self._save_state(), {"state": "failed", "error": "retry also failed"})
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.assertEqual(self._transcript_revisions(), 0)

    def test_invalid_save_after_release_keeps_the_held_edit(self) -> None:
        # P1-B: a rejected save changes nothing, so the edit that already got 202 still applies once.
        with patch.object(api_routes_jobs, "HELD_TRANSCRIPT_POLL_SECONDS", 2.0):
            queue_id = self._start_managed_render()
            self.assertEqual(self._save(self._edited("held edit", "second line"))[0], 202)
            self._finish_render(queue_id)  # released; the applier is still in its first 2 s poll
            self.assertEqual(self._save([{"text": "no timing"}]),
                             (400, {"error": "transcript segment 1 start/end is invalid"}))
            self.assertEqual(self._save("not a list"), (400, {"error": "segments must be a list"}))
            self.assertEqual(self._save_state(), {"state": "pending", "error": None})
            self.assertEqual(self._transcript_revisions(), 0)
            self.assertTrue(self._wait_for(lambda: self._save_state() == {"state": "applied", "error": None}, 8))
        time.sleep(0.6)
        self.assertEqual(self._texts(), ["held edit", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)

    def test_approval_paused_after_the_gate_does_not_commit_over_a_held_save(self) -> None:
        # P1-A, first case: approve passed the real gate, then a save was accepted and applied.
        self._make_approvable()
        queue_id = self._start_managed_render()
        self._finish_render(queue_id, complete_queue=False)  # job reads needs_review; the worker owns it
        entered, release = threading.Event(), threading.Event()
        approval = self._approve_in_thread(entered, release)
        self.assertTrue(entered.wait(10), "approval did not reach the barrier")
        try:
            self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
            self.queue.complete(queue_id)
            self.assertTrue(self._wait_for(lambda: self._save_state() == {"state": "applied", "error": None}))
        finally:
            release.set()
        approval["thread"].join(10)
        approval["patcher"].stop()
        self.assertEqual(approval["response"],
                         (409, {"error": "the transcript was saved during approval; review the job and approve again"}))
        self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")
        self.assertEqual(self._texts(), ["edited line", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)
        status, body = self._post("/jobs/job-one/approve", {})  # the usual gate: the apply removed the video
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "quality_gate_failed")
        self.assertIn("render_missing", {item["code"] for item in body["error"]["details"]["blocking"]})

    def test_approval_paused_after_the_gate_does_not_commit_over_a_direct_save(self) -> None:
        self._make_approvable()
        self._write_job("needs_review")
        entered, release = threading.Event(), threading.Event()
        approval = self._approve_in_thread(entered, release)
        self.assertTrue(entered.wait(10), "approval did not reach the barrier")
        try:
            status, body = self._save(self._edited("edited line", "second line"))  # not blocked by approval
            self.assertEqual(status, 200, body)
        finally:
            release.set()
        approval["thread"].join(10)
        approval["patcher"].stop()
        self.assertEqual(approval["response"],
                         (409, {"error": "the transcript was saved during approval; review the job and approve again"}))
        self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")
        self.assertFalse((self.job_dir / "review.mp4").exists())

    def test_approval_without_a_concurrent_save_still_commits(self) -> None:
        self._make_approvable()
        self._write_job("needs_review")
        entered, release = threading.Event(), threading.Event()
        approval = self._approve_in_thread(entered, release)
        self.assertTrue(entered.wait(10))
        release.set()
        approval["thread"].join(10)
        approval["patcher"].stop()
        self.assertEqual(approval["response"][0], 200, approval["response"])
        self.assertEqual(load_job(self.job_dir / "job.json").status, "done")

    def test_held_save_waits_through_pause_and_applies_after_cancel(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("edited line", "second line"))[0], 202)
        self.queue.pause(queue_id)
        self.queue.acknowledge_control(queue_id, "paused")
        load_job(self.job_dir / "job.json").set_status("paused")
        self.assertEqual(self._save(self._edited("edited while paused", "second line"))[0], 202)
        time.sleep(0.6)
        self.assertEqual(self._texts(), ["original line", "second line"])
        self.queue.cancel(queue_id)
        load_job(self.job_dir / "job.json").cancel()
        self.assertTrue(self._wait_for(lambda: self._texts() == ["edited while paused", "second line"]))
        time.sleep(0.6)
        self.assertEqual(self._transcript_revisions(), 1)
        self.assertEqual(load_job(self.job_dir / "job.json").status, "needs_review")

    def test_idle_job_save_response_is_unchanged(self) -> None:
        self._write_job("needs_review")
        status, body = self._save(self._edited("edited line", "second line"))
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"job", "transcript", "revision"})
        self.assertEqual(self._texts(), ["edited line", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)


if __name__ == "__main__":
    unittest.main()
