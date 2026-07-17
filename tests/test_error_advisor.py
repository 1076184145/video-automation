from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_automation.error_advisor import advise_error
from video_automation.jobs import Job, load_job


class ErrorAdvisorTests(unittest.TestCase):
    def test_cuda_oom_suggests_medium_retry(self) -> None:
        advice = advise_error("RuntimeError: CUDA out of memory while transcribing")
        self.assertEqual(advice["code"], "gpu_memory")
        self.assertIn("显卡内存不足", advice["title"])
        action = advice["actions"][0]
        self.assertEqual(action["type"], "settings_patch_and_rerun")
        self.assertEqual(action["stage"], "transcribe")
        self.assertEqual(action["env"]["WHISPER_MODEL"], "medium")

    def test_ffmpeg_missing_suggests_health_repair(self) -> None:
        advice = advise_error("[WinError 2] The system cannot find the file specified: ffmpeg")
        self.assertEqual(advice["code"], "missing_ffmpeg")
        self.assertEqual(advice["actions"][0]["type"], "open_health")

    def test_nvenc_session_failure_suggests_cpu_render_retry(self) -> None:
        advice = advise_error("OpenEncodeSessionEx failed: incompatible client key (21)")
        self.assertEqual(advice["code"], "nvenc_unavailable")
        self.assertEqual(advice["actions"][0]["env"]["RENDER_VIDEO_ENCODER"], "libx264")
        self.assertEqual(advice["actions"][0]["stage"], "render_final")

    def test_unmatched_http_errors_use_generic_advice(self) -> None:
        advice = advise_error("HTTP Error 403: Forbidden")
        self.assertEqual(advice["code"], "generic")
        self.assertEqual(advice["actions"][0]["type"], "open_health")

    def test_empty_transcript_suggests_skip_or_audio_check(self) -> None:
        advice = advise_error("transcription returned empty segments")
        self.assertEqual(advice["code"], "empty_transcript")
        self.assertEqual({action["type"] for action in advice["actions"]}, {"rerun_stage", "skip_transcribe"})

    def test_disk_space_suggests_cleanup(self) -> None:
        advice = advise_error("OSError: No space left on device")
        self.assertEqual(advice["code"], "disk_space")
        self.assertEqual(advice["actions"][0]["type"], "open_cleanup")

    def test_job_fail_persists_error_advice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            job = Job(source_path=Path("D:/input.mp4"), job_dir=job_dir)
            job.fail("ffmpeg not found")
            data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
            self.assertEqual(data["error_advice"]["code"], "missing_ffmpeg")
            loaded = load_job(job_dir / "job.json")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.error_advice["code"], "missing_ffmpeg")


if __name__ == "__main__":
    unittest.main()
