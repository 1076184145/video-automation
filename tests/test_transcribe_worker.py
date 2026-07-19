from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from video_automation.transcribe_worker import (
    PersistentTranscriptionWorker,
    TranscriptionTaskError,
    WorkerInfrastructureError,
)
from video_automation.transcribe_worker_runner import process_request


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process

    def write(self, value: str) -> int:
        request = json.loads(value)
        self.process.requests.append(request)
        self.process.on_request(self.process, request)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self, on_request: Callable[["_FakeProcess", dict[str, Any]], None]) -> None:
        self.on_request = on_request
        self.requests: list[dict[str, Any]] = []
        self.returncode: int | None = None
        self.stdin = _FakeStdin(self)
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode or 0

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9


def _write_response(request: dict[str, Any], payload: dict[str, Any]) -> None:
    Path(request["response_path"]).write_text(json.dumps(payload), encoding="utf-8")


class PersistentTranscriptionWorkerTests(unittest.TestCase):
    def test_no_progress_timeout_reports_last_worker_phase_without_repeating_inference(self) -> None:
        processes: list[_FakeProcess] = []

        def stall(_process: _FakeProcess, request: dict[str, Any]) -> None:
            Path(request["heartbeat_path"]).write_text(
                json.dumps({"phase": "transcribing"}),
                encoding="utf-8",
            )

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(stall)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0.005)
            with self.assertRaisesRegex(WorkerInfrastructureError, "last phase: transcribing"):
                worker.run(
                    command=["python"],
                    signature=("model-a",),
                    request={"audio_path": "audio.wav", "job_dir": temp_dir},
                    timeout_seconds=0.2,
                    no_progress_timeout_seconds=0.03,
                )

        self.assertEqual(len(processes), 1)
        self.assertTrue(all(process.terminated for process in processes))

    def test_restart_attempts_share_one_timeout_budget(self) -> None:
        processes: list[_FakeProcess] = []

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(lambda _process, _request: None)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0.01)
            with self.assertRaisesRegex(WorkerInfrastructureError, "timed out"):
                worker.run(
                    command=["python"],
                    signature=("model-a",),
                    request={"audio_path": "audio.wav", "job_dir": temp_dir},
                    timeout_seconds=0.05,
                )

        self.assertEqual(len(processes), 1)

    def test_reuses_one_process_for_matching_configuration(self) -> None:
        processes: list[_FakeProcess] = []

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(lambda _process, request: _write_response(request, {"status": "ok"}))
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0)
            request = {"audio_path": "audio.wav", "job_dir": temp_dir}

            worker.run(command=["python", "-m", "worker"], signature=("funasr", "cuda"), request=request, timeout_seconds=1)
            worker.run(command=["python", "-m", "worker"], signature=("funasr", "cuda"), request=request, timeout_seconds=1)

            self.assertEqual(len(processes), 1)
            self.assertEqual(len(processes[0].requests), 2)
            self.assertEqual(list(Path(temp_dir).glob(".transcribe-response-*.json")), [])

    def test_configuration_change_replaces_the_process(self) -> None:
        processes: list[_FakeProcess] = []

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(lambda _process, request: _write_response(request, {"status": "ok"}))
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0)
            request = {"audio_path": "audio.wav", "job_dir": temp_dir}

            worker.run(command=["python"], signature=("model-a",), request=request, timeout_seconds=1)
            worker.run(command=["python"], signature=("model-b",), request=request, timeout_seconds=1)

            self.assertEqual(len(processes), 2)
            self.assertTrue(processes[0].terminated)

    def test_crashed_process_is_restarted_once(self) -> None:
        processes: list[_FakeProcess] = []

        def crash(process: _FakeProcess, _request: dict[str, Any]) -> None:
            process.returncode = 7

        callbacks = [
            crash,
            lambda _process, request: _write_response(request, {"status": "ok"}),
        ]

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(callbacks[len(processes)])
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0)
            result = worker.run(
                command=["python"],
                signature=("model-a",),
                request={"audio_path": "audio.wav", "job_dir": temp_dir},
                timeout_seconds=1,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(processes), 2)

    def test_task_error_is_not_retried_as_infrastructure_failure(self) -> None:
        processes: list[_FakeProcess] = []

        def factory(*_args: object, **_kwargs: object) -> _FakeProcess:
            process = _FakeProcess(
                lambda _process, request: _write_response(
                    request,
                    {"status": "error", "error": "audio contains no speech"},
                )
            )
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = PersistentTranscriptionWorker(process_factory=factory, poll_interval=0)

            with self.assertRaisesRegex(TranscriptionTaskError, "audio contains no speech"):
                worker.run(
                    command=["python"],
                    signature=("model-a",),
                    request={"audio_path": "audio.wav", "job_dir": temp_dir},
                    timeout_seconds=1,
                )

            self.assertEqual(len(processes), 1)


class TranscriptionWorkerProtocolTests(unittest.TestCase):
    def test_warmup_request_waits_for_model_without_transcribing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            response_path = root / "response.json"
            calls: list[object] = []

            payload = process_request(
                SimpleNamespace(),
                object(),
                {
                    "warmup": True,
                    "job_dir": str(root),
                    "response_path": str(response_path),
                },
                transcribe=lambda *_args: calls.append(object()),
            )

            self.assertEqual(payload, {"status": "ok", "warmup": True})
            self.assertEqual(calls, [])

    def test_process_request_writes_success_response_after_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "job"
            response_path = root / "response.json"
            calls: list[tuple[object, Path, Path]] = []

            def transcribe(
                settings: object,
                model: object,
                audio_path: Path,
                txt_path: Path,
                srt_path: Path,
                json_path: Path,
            ) -> None:
                calls.append((model, audio_path, txt_path.parent))
                txt_path.parent.mkdir(parents=True, exist_ok=True)
                txt_path.write_text("hello", encoding="utf-8")
                srt_path.write_text("hello", encoding="utf-8")
                json_path.write_text("{}", encoding="utf-8")

            payload = process_request(
                SimpleNamespace(),
                object(),
                {
                    "audio_path": str(root / "audio.wav"),
                    "job_dir": str(job_dir),
                    "response_path": str(response_path),
                },
                transcribe=transcribe,
            )

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(json.loads(response_path.read_text(encoding="utf-8"))["status"], "ok")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], job_dir)

    def test_process_request_reports_task_error_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            response_path = root / "response.json"

            def fail(*_args: object) -> None:
                raise RuntimeError("decoder rejected audio")

            payload = process_request(
                SimpleNamespace(),
                object(),
                {
                    "audio_path": str(root / "audio.wav"),
                    "job_dir": str(root / "job"),
                    "response_path": str(response_path),
                },
                transcribe=fail,
            )

            self.assertEqual(payload["status"], "error")
            self.assertIn("decoder rejected audio", payload["error"])
            self.assertEqual(json.loads(response_path.read_text(encoding="utf-8"))["status"], "error")


if __name__ == "__main__":
    unittest.main()
