from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation import transcribe, transcribe_runtime
from video_automation.transcribe_worker import TranscriptionTaskError, WorkerInfrastructureError
from video_automation.task_queue import QueueControlRequested


class TranscribeFallbackTests(unittest.TestCase):
    def test_preexisting_cancel_does_not_spawn_transcription_child(self) -> None:
        with patch.object(transcribe_runtime.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(QueueControlRequested, "canceled"):
                transcribe._run_transcription_process(
                    ["unused"],
                    cwd=str(Path.cwd()),
                    env=os.environ.copy(),
                    timeout=30,
                    control_callback=lambda: "canceled",
                )
        popen.assert_not_called()

    def test_backend_attempt_timeout_caps_legacy_duration_budget(self) -> None:
        settings = SimpleNamespace(transcribe_attempt_timeout_seconds=1800)
        with patch.object(transcribe_runtime, "_transcribe_timeout", return_value=12000):
            self.assertEqual(transcribe_runtime._backend_attempt_timeout(settings, Path("audio.wav")), 1800)  # type: ignore[arg-type]

    def test_model_reference_prefers_complete_project_local_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_model = root / "config" / "models" / "faster-whisper-small"
            local_model.mkdir(parents=True)
            (local_model / "config.json").write_text("{}", encoding="utf-8")
            (local_model / "model.bin").write_bytes(b"model")

            resolved = transcribe._resolve_model_reference(SimpleNamespace(root=root), "small")  # type: ignore[arg-type]

        self.assertEqual(resolved, str(local_model))

    def test_model_reference_ignores_incomplete_project_local_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_model = root / "config" / "models" / "faster-whisper-small"
            local_model.mkdir(parents=True)
            (local_model / "config.json").write_text("{}", encoding="utf-8")

            resolved = transcribe._resolve_model_reference(SimpleNamespace(root=root), "small")  # type: ignore[arg-type]

        self.assertEqual(resolved, "small")

    def test_failed_primary_opens_circuit_and_next_job_skips_it(self) -> None:
        backend = "test-primary-circuit"
        settings = SimpleNamespace(transcribe_backend_cooldown_seconds=60)
        primary_calls = 0

        def primary() -> None:
            nonlocal primary_calls
            primary_calls += 1
            raise WorkerInfrastructureError("worker stalled")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def fallback() -> None:
                (root / "transcript.json").write_text(
                    json.dumps({"backend": "fallback", "segments": []}),
                    encoding="utf-8",
                )

            for _ in range(2):
                transcribe._run_primary_with_fallback(
                    settings,  # type: ignore[arg-type]
                    root,
                    root / "transcript.json",
                    primary_backend=backend,
                    primary_model="primary-model",
                    primary=primary,
                    fallback_backend="fallback",
                    fallback_model="fallback-model",
                    fallback=fallback,
                )

            attempts = json.loads((root / "transcription_attempts.json").read_text(encoding="utf-8"))["attempts"]

        self.assertEqual(primary_calls, 1)
        self.assertEqual([item["status"] for item in attempts], ["failed", "complete", "skipped", "complete"])
        transcribe._reset_backend_circuit(backend)

    def test_task_specific_primary_failure_does_not_open_circuit(self) -> None:
        backend = "test-primary-task-error"
        settings = SimpleNamespace(transcribe_backend_cooldown_seconds=60)
        primary_calls = 0

        def primary() -> None:
            nonlocal primary_calls
            primary_calls += 1
            raise TranscriptionTaskError("bad input")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def fallback() -> None:
                (root / "transcript.json").write_text(
                    json.dumps({"backend": "fallback", "segments": []}),
                    encoding="utf-8",
                )

            for _ in range(2):
                transcribe._run_primary_with_fallback(
                    settings,  # type: ignore[arg-type]
                    root,
                    root / "transcript.json",
                    primary_backend=backend,
                    primary_model="primary-model",
                    primary=primary,
                    fallback_backend="fallback",
                    fallback_model="fallback-model",
                    fallback=fallback,
                )

        self.assertEqual(primary_calls, 2)
        self.assertEqual(transcribe._backend_circuit_remaining(backend), 0)

    def test_transcription_child_is_killed_when_queue_cancel_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ready_path = Path(temp_dir) / "ready"
            cancel_requested_at: float | None = None

            def control() -> str | None:
                nonlocal cancel_requested_at
                if not ready_path.is_file():
                    return None
                cancel_requested_at = cancel_requested_at or time.monotonic()
                return "canceled"

            with self.assertRaisesRegex(QueueControlRequested, "canceled"):
                transcribe._run_transcription_process(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; import sys, time; "
                        "Path(sys.argv[1]).write_text('ready'); time.sleep(10)",
                        str(ready_path),
                    ],
                    cwd=str(Path.cwd()),
                    env=os.environ.copy(),
                    timeout=30,
                    control_callback=control,
                )
            self.assertIsNotNone(cancel_requested_at)
            self.assertLess(time.monotonic() - float(cancel_requested_at), 1.5)

    def test_transcription_child_is_killed_when_heartbeat_stalls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat_path = Path(temp_dir) / "heartbeat.json"
            with self.assertRaisesRegex(WorkerInfrastructureError, "last phase: process_started"):
                transcribe._run_transcription_process(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    cwd=str(Path.cwd()),
                    env=os.environ.copy(),
                    timeout=30,
                    heartbeat_path=heartbeat_path,
                    no_progress_timeout=0.05,
                )

    def test_faster_whisper_accepts_complete_outputs_after_cuda_child_exit_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = SimpleNamespace(
                root=job_dir,
                whisper_model="medium",
                whisper_model_fallbacks=(),
                whisper_language="zh",
            )

            def complete_then_crash(*_args: object, **_kwargs: object) -> SimpleNamespace:
                (job_dir / "transcript.txt").write_text("hello", encoding="utf-8")
                (job_dir / "transcript.srt").write_text("", encoding="utf-8")
                (job_dir / "transcript.json").write_text(
                    json.dumps({"segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=1, stdout="", stderr="native CUDA shutdown error")

            with patch.object(transcribe, "_run_transcription_process", side_effect=complete_then_crash):
                transcribe._run_faster_whisper_subprocess(settings, Path("audio.wav"), job_dir)  # type: ignore[arg-type]

    def test_faster_whisper_rejects_zero_exit_without_complete_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = SimpleNamespace(
                root=job_dir,
                whisper_model="medium",
                whisper_model_fallbacks=(),
                whisper_language="zh",
            )
            result = SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(transcribe, "_run_transcription_process", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "without complete output files"):
                    transcribe._run_faster_whisper_subprocess(settings, Path("audio.wav"), job_dir)  # type: ignore[arg-type]

    def test_faster_whisper_child_receives_frozen_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = SimpleNamespace(
                root=job_dir,
                whisper_backend="faster-whisper",
                whisper_model="medium",
                whisper_model_fallbacks=(),
                whisper_language="zh",
                whisper_initial_prompt="frozen prompt",
                whisper_word_timestamps=True,
                whisper_vad_filter=False,
                faster_whisper_device="cuda",
                faster_whisper_compute_type="float16",
                faster_whisper_batch_size=4,
                subtitle_censor_replacement="[beep]",
                profanity_words=("word-a", "word-b"),
                subtitle_replacements=(("before", "after"),),
            )
            result = SimpleNamespace(returncode=1, stdout="", stderr="failed")
            with patch.object(transcribe, "_run_transcription_process", return_value=result) as run:
                with self.assertRaises(RuntimeError):
                    transcribe._run_faster_whisper_subprocess(settings, Path("audio.wav"), job_dir)  # type: ignore[arg-type]

            child_env = run.call_args.kwargs["env"]
            self.assertEqual(child_env["WHISPER_MODEL"], "medium")
            self.assertEqual(child_env["FASTER_WHISPER_DEVICE"], "cuda")
            self.assertEqual(child_env["FASTER_WHISPER_COMPUTE_TYPE"], "float16")
            self.assertEqual(child_env["FASTER_WHISPER_BATCH_SIZE"], "4")
            self.assertEqual(child_env["WHISPER_INITIAL_PROMPT"], "frozen prompt")
            self.assertEqual(child_env["SUBTITLE_REPLACEMENTS"], "before=>after")

    def test_transcription_snapshot_is_stable_and_excludes_secrets(self) -> None:
        settings = SimpleNamespace(
            whisper_backend="faster-whisper",
            whisper_model="medium",
            whisper_language="zh",
            faster_whisper_device="cuda",
            faster_whisper_compute_type="float16",
            openai_api_key="must-not-be-persisted",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            first = transcribe._write_transcription_settings_snapshot(settings, job_dir)  # type: ignore[arg-type]
            second = transcribe._write_transcription_settings_snapshot(settings, job_dir)  # type: ignore[arg-type]
            persisted = json.loads((job_dir / "transcription_settings.json").read_text(encoding="utf-8"))

        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(persisted["settings"]["whisper_model"], "medium")
        self.assertNotIn("openai_api_key", persisted["settings"])
        self.assertNotIn("must-not-be-persisted", json.dumps(persisted))

    def test_funasr_warmup_uses_persistent_worker_protocol(self) -> None:
        settings = SimpleNamespace(
            root=Path("D:/video-automation"),
            whisper_backend="funasr-whisper",
            funasr_persistent_worker=True,
            funasr_model="paraformer-zh",
            funasr_vad_model="fsmn-vad",
            funasr_punc_model="ct-punc",
            funasr_device="cuda:0",
            funasr_hotwords="",
            funasr_batch_size_s=300,
            funasr_max_segment_ms=60000,
            whisper_language="zh",
            subtitle_replacements=(),
            profanity_words=(),
            subtitle_censor_replacement="[beep]",
        )
        with (
            patch.object(transcribe, "_project_python", return_value=Path("python")),
            patch.object(transcribe._FUNASR_PERSISTENT_WORKER, "run", return_value={"status": "ok"}) as run,
        ):
            self.assertTrue(transcribe.warm_transcription_backend(settings))  # type: ignore[arg-type]

        self.assertTrue(run.call_args.kwargs["request"]["warmup"])
        self.assertIn("processing", run.call_args.kwargs["request"]["job_dir"])

    def test_funasr_whisper_backend_falls_back_to_faster_whisper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            audio_path = job_dir / "audio.wav"
            settings = SimpleNamespace(whisper_backend="funasr-whisper")

            def fake_faster_whisper(
                _settings: object,
                _audio_path: Path,
                _job_dir: Path,
                **_kwargs: object,
            ) -> None:
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

    def test_funasr_infrastructure_failure_does_not_repeat_same_backend(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=True)
        with (
            patch.object(
                transcribe,
                "_run_funasr_persistent",
                side_effect=WorkerInfrastructureError("pipe closed"),
            ),
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            with self.assertRaisesRegex(WorkerInfrastructureError, "pipe closed"):
                transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        one_shot.assert_not_called()

    def test_funasr_timeout_does_not_start_a_fresh_one_shot_budget(self) -> None:
        settings = SimpleNamespace(funasr_persistent_worker=True)

        def exhaust_budget(*_args: object, **_kwargs: object) -> None:
            time.sleep(0.02)
            raise WorkerInfrastructureError("timed out")

        with (
            patch.object(transcribe, "_transcribe_timeout", return_value=0.01),
            patch.object(transcribe, "_run_funasr_persistent", side_effect=exhaust_budget),
            patch.object(transcribe, "_run_funasr_one_shot_subprocess") as one_shot,
        ):
            with self.assertRaisesRegex(WorkerInfrastructureError, "timed out"):
                transcribe._run_funasr_subprocess(settings, Path("audio.wav"), Path("job"))  # type: ignore[arg-type]

        one_shot.assert_not_called()

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
