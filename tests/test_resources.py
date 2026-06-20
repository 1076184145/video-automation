from __future__ import annotations

import threading
import time
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_automation import render, transcribe
from video_automation.resources import (
    ExecutionGate,
    job_gpu_status_callbacks,
    rendering_uses_gpu,
    transcription_uses_gpu,
)


class RecordingGate:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @contextmanager
    def slot(self, **kwargs: object):
        self.calls.append(kwargs)
        yield False


class ResourceClassificationTests(unittest.TestCase):
    def test_transcription_uses_gpu_for_any_configured_cuda_backend(self) -> None:
        faster = SimpleNamespace(
            whisper_backend="faster-whisper",
            faster_whisper_device="cuda",
            funasr_device="cpu",
        )
        funasr = SimpleNamespace(
            whisper_backend="funasr",
            faster_whisper_device="cpu",
            funasr_device="cuda:0",
        )
        fallback = SimpleNamespace(
            whisper_backend="funasr-whisper",
            faster_whisper_device="cuda",
            funasr_device="cpu",
        )

        self.assertTrue(transcription_uses_gpu(faster))
        self.assertTrue(transcription_uses_gpu(funasr))
        self.assertTrue(transcription_uses_gpu(fallback))

    def test_transcription_does_not_use_gpu_for_cpu_backends(self) -> None:
        settings = SimpleNamespace(
            whisper_backend="funasr-whisper",
            faster_whisper_device="cpu",
            funasr_device="cpu",
        )

        self.assertFalse(transcription_uses_gpu(settings))

    def test_rendering_uses_gpu_only_for_nvenc(self) -> None:
        self.assertTrue(rendering_uses_gpu(SimpleNamespace(render_video_encoder="h264_nvenc")))
        self.assertTrue(rendering_uses_gpu(SimpleNamespace(render_video_encoder="NVENC")))
        self.assertFalse(rendering_uses_gpu(SimpleNamespace(render_video_encoder="libx264")))


class ExecutionGateTests(unittest.TestCase):
    def test_one_slot_serializes_work_and_notifies_waiter(self) -> None:
        gate = ExecutionGate(1)
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        waiter_notified = threading.Event()
        active = 0
        max_active = 0
        active_lock = threading.Lock()

        def run_first() -> None:
            nonlocal active, max_active
            with gate.slot():
                with active_lock:
                    active += 1
                    max_active = max(max_active, active)
                first_entered.set()
                release_first.wait(timeout=2)
                with active_lock:
                    active -= 1

        def run_second() -> None:
            nonlocal active, max_active
            first_entered.wait(timeout=2)
            with gate.slot(on_wait=waiter_notified.set):
                with active_lock:
                    active += 1
                    max_active = max(max_active, active)
                second_entered.set()
                with active_lock:
                    active -= 1

        first = threading.Thread(target=run_first)
        second = threading.Thread(target=run_second)
        first.start()
        second.start()

        self.assertTrue(first_entered.wait(timeout=2))
        self.assertTrue(waiter_notified.wait(timeout=2))
        self.assertFalse(second_entered.wait(timeout=0.05))
        release_first.set()
        self.assertTrue(second_entered.wait(timeout=2))

        first.join(timeout=2)
        second.join(timeout=2)
        self.assertEqual(max_active, 1)

    def test_disabled_slot_does_not_serialize_cpu_work(self) -> None:
        gate = ExecutionGate(1)
        first_entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()

        def run(entered: threading.Event) -> None:
            with gate.slot(enabled=False):
                entered.set()
                release.wait(timeout=2)

        first = threading.Thread(target=run, args=(first_entered,))
        second = threading.Thread(target=run, args=(second_entered,))
        first.start()
        second.start()

        self.assertTrue(first_entered.wait(timeout=2))
        self.assertTrue(second_entered.wait(timeout=2))
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)

    def test_acquired_callback_runs_after_waiting(self) -> None:
        gate = ExecutionGate(1)
        first_entered = threading.Event()
        release_first = threading.Event()
        callbacks: list[str] = []

        def hold_slot() -> None:
            with gate.slot():
                first_entered.set()
                release_first.wait(timeout=2)

        first = threading.Thread(target=hold_slot)
        first.start()
        self.assertTrue(first_entered.wait(timeout=2))

        def release_later() -> None:
            time.sleep(0.05)
            release_first.set()

        releaser = threading.Thread(target=release_later)
        releaser.start()
        with gate.slot(
            on_wait=lambda: callbacks.append("waiting"),
            on_acquired=lambda: callbacks.append("acquired"),
        ):
            callbacks.append("running")

        first.join(timeout=2)
        releaser.join(timeout=2)
        self.assertEqual(callbacks, ["waiting", "acquired", "running"])


class CoreResourceIntegrationTests(unittest.TestCase):
    def test_cuda_transcription_enters_gpu_gate_and_forwards_callbacks(self) -> None:
        gate = RecordingGate()
        settings = SimpleNamespace(
            whisper_backend="faster-whisper",
            faster_whisper_device="cuda",
            funasr_device="cpu",
        )
        waiting = Mock()
        acquired = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(transcribe, "GPU_EXECUTION_GATE", gate, create=True),
                patch.object(transcribe, "_transcribe_audio_unlocked", create=True) as unlocked,
            ):
                transcribe.transcribe_audio(
                    settings,  # type: ignore[arg-type]
                    root / "audio.wav",
                    root,
                    force=True,
                    resource_wait_callback=waiting,
                    resource_acquired_callback=acquired,
                )

        unlocked.assert_called_once()
        self.assertEqual(len(gate.calls), 1)
        self.assertIs(gate.calls[0]["on_wait"], waiting)
        self.assertIs(gate.calls[0]["on_acquired"], acquired)
        self.assertTrue(gate.calls[0]["enabled"])

    def test_cpu_transcription_uses_disabled_gpu_slot(self) -> None:
        gate = RecordingGate()
        settings = SimpleNamespace(
            whisper_backend="faster-whisper",
            faster_whisper_device="cpu",
            funasr_device="cpu",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(transcribe, "GPU_EXECUTION_GATE", gate, create=True),
                patch.object(transcribe, "_transcribe_audio_unlocked", create=True),
            ):
                transcribe.transcribe_audio(
                    settings,  # type: ignore[arg-type]
                    root / "audio.wav",
                    root,
                    force=True,
                )

        self.assertEqual(len(gate.calls), 1)
        self.assertFalse(gate.calls[0]["enabled"])

    def test_review_render_uses_resource_aware_ffmpeg_runner(self) -> None:
        result = SimpleNamespace(returncode=0, stderr="")
        settings = SimpleNamespace(render_video_encoder="h264_nvenc")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preview = {
                "output_path": str(root / "review.mp4"),
                "command": ["ffmpeg", "-version"],
                "clips": [{"start": 0.0, "end": 1.0, "duration": 1.0}],
            }
            with (
                patch.object(render, "generate_render_preview", return_value=preview),
                patch.object(render, "run_ffmpeg_with_progress", return_value=result),
                patch.object(render, "_run_ffmpeg_with_resource_gate", return_value=result, create=True) as gated_runner,
                patch.object(render, "_refresh_web_preview"),
            ):
                render.render_review_video(settings, root, root / "source.mp4", force=True)  # type: ignore[arg-type]

        gated_runner.assert_called_once()


class JobResourceStatusTests(unittest.TestCase):
    def test_job_callbacks_preserve_progress_and_describe_gpu_wait(self) -> None:
        job = SimpleNamespace(stage_progress=37.5, updates=[])

        def update_stage_progress(percent: float | None, *, message: str | None = None) -> None:
            job.updates.append((percent, message))

        job.update_stage_progress = update_stage_progress
        waiting, acquired = job_gpu_status_callbacks(job, "transcription")

        waiting()
        acquired()

        self.assertEqual(
            job.updates,
            [
                (37.5, "Waiting for GPU to start transcription."),
                (37.5, "GPU available. Starting transcription."),
            ],
        )


if __name__ == "__main__":
    unittest.main()
