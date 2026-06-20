from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation import transcribe
from video_automation.transcribe_worker import TranscriptionTaskError, WorkerInfrastructureError


class TranscribeFallbackTests(unittest.TestCase):
    def test_funasr_whisper_backend_falls_back_to_faster_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            audio_path = job_dir / "audio.wav"
            settings = SimpleNamespace(whisper_backend="funasr-whisper")

            def fake_faster_whisper(_settings: object, _audio_path: Path, _job_dir: Path) -> None:
                (job_dir / "transcript.txt").write_text("hello", encoding="utf-8")
                (job_dir / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
                (job_dir / "transcript.json").write_text(
                    json.dumps({"backend": "faster-whisper", "segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                    encoding="utf-8",
                )

            with (
                patch.object(transcribe, "_run_funasr_subprocess", side_effect=RuntimeError("funasr unavailable")),
                patch.object(transcribe, "_run_faster_whisper_subprocess", side_effect=fake_faster_whisper),
            ):
                transcribe.transcribe_audio(settings, audio_path, job_dir, force=True)  # type: ignore[arg-type]

            payload = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["fallback_from"], "funasr")
            self.assertIn("funasr unavailable", payload["fallback_reason"])
            self.assertEqual(payload["backend"], "faster-whisper (fallback)")

    def test_funasr_uses_persistent_worker_by_default(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=True)
        with (
            patch.object(transcribe, "_run_funasr_persistent") as persistent,
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        persistent.assert_called_once()
        one_shot.assert_not_called()

    def test_funasr_infrastructure_failure_falls_back_to_one_shot_runner(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=True)
        with (
            patch.object(
                transcribe,
                "_run_funasr_persistent",
                side_effect=WorkerInfrastructureError("pipe closed"),
            ),
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        one_shot.assert_called_once()

    def test_funasr_task_failure_is_not_retried_in_one_shot_runner(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=True)
        with (
            patch.object(
                transcribe,
                "_run_funasr_persistent",
                side_effect=TranscriptionTaskError("no speech"),
            ),
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            with self.assertRaisesRegex(TranscriptionTaskError, "no speech"):
                transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        one_shot.assert_not_called()

    def test_funasr_persistent_worker_can_be_disabled(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=False)
        with (
            patch.object(transcribe, "_run_funasr_persistent") as persistent,
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        persistent.assert_not_called()
        one_shot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
