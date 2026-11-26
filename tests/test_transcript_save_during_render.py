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
        for name in ("generate_ass_subtitles", "generate_clipped_ass_subtitles", "generate_render_preview"):
            patcher = patch.object(api_routes_jobs, name, side_effect=lambda *a, _n=name, **k: self.generated.append(_n))
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

    def _post(self, path: str, payload: object) -> tuple[int, dict]:
        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            connection.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

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
        self.assertTrue(self._wait_for(lambda: self._transcript_revisions() == 1))
        # Once applied, approval goes on to the existing quality gate. A transcript save always removes
        # the render outputs, so this fixture (no video) is then blocked by "render_missing", as on base.
        status, body = self._post("/jobs/job-one/approve", {})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "quality_gate_failed")
        self.assertIn("render_missing", {item["code"] for item in body["error"]["details"]["blocking"]})

    def test_direct_save_after_the_render_supersedes_the_held_edit(self) -> None:
        queue_id = self._start_managed_render()
        self.assertEqual(self._save(self._edited("held edit", "second line"))[0], 202)
        self._finish_render(queue_id, complete_queue=False)
        status, body = self._save(self._edited("newer edit", "second line"))
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"job", "transcript", "revision"})
        self.queue.complete(queue_id)
        time.sleep(0.8)
        self.assertEqual(self._texts(), ["newer edit", "second line"])
        self.assertEqual(self._transcript_revisions(), 1)

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
