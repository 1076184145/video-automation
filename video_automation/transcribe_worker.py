from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .task_queue import QueueControlRequested


class WorkerInfrastructureError(RuntimeError):
    pass


class TranscriptionTaskError(RuntimeError):
    pass


class PersistentTranscriptionWorker:
    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
        poll_interval: float = 0.05,
    ) -> None:
        self._process_factory = process_factory
        self._poll_interval = max(0.0, poll_interval)
        self._lock = threading.Lock()
        self._process: Any | None = None
        self._signature: tuple[Any, ...] | None = None

    def run(
        self,
        *,
        command: Sequence[str],
        signature: tuple[Any, ...],
        request: dict[str, Any],
        timeout_seconds: float,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        control_callback: Callable[[], str | None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            deadline = time.monotonic() + max(0.01, float(timeout_seconds))
            last_error: WorkerInfrastructureError | None = None
            for _attempt in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    return self._run_once(
                        command=command,
                        signature=signature,
                        request=request,
                        timeout_seconds=remaining,
                        cwd=cwd,
                        env=env,
                        control_callback=control_callback,
                    )
                except QueueControlRequested:
                    self._stop_unlocked()
                    raise
                except WorkerInfrastructureError as exc:
                    last_error = exc
                    self._stop_unlocked()
            assert last_error is not None
            raise last_error

    def close(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def _run_once(
        self,
        *,
        command: Sequence[str],
        signature: tuple[Any, ...],
        request: dict[str, Any],
        timeout_seconds: float,
        cwd: Path | str | None,
        env: dict[str, str] | None,
        control_callback: Callable[[], str | None] | None,
    ) -> dict[str, Any]:
        process = self._ensure_process(command, signature, cwd=cwd, env=env)
        job_dir = Path(str(request["job_dir"]))
        job_dir.mkdir(parents=True, exist_ok=True)
        response_path = job_dir / f".transcribe-response-{uuid.uuid4().hex}.json"
        payload = dict(request)
        payload["response_path"] = str(response_path)

        try:
            stdin = getattr(process, "stdin", None)
            if stdin is None:
                raise WorkerInfrastructureError("persistent transcription worker has no stdin")
            stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            response_path.unlink(missing_ok=True)
            raise WorkerInfrastructureError(f"failed to send transcription request: {exc}") from exc

        deadline = time.monotonic() + max(0.01, float(timeout_seconds))
        try:
            while time.monotonic() < deadline:
                action = control_callback() if control_callback else None
                if action in {"paused", "canceled"}:
                    raise QueueControlRequested(action)
                if response_path.exists():
                    try:
                        response = json.loads(response_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError) as exc:
                        raise WorkerInfrastructureError(f"invalid transcription worker response: {exc}") from exc
                    if not isinstance(response, dict):
                        raise WorkerInfrastructureError("transcription worker response must be an object")
                    if response.get("status") == "error":
                        raise TranscriptionTaskError(str(response.get("error") or "transcription failed"))
                    if response.get("status") != "ok":
                        raise WorkerInfrastructureError("transcription worker response has an invalid status")
                    return response
                returncode = process.poll()
                if returncode is not None:
                    raise WorkerInfrastructureError(
                        f"persistent transcription worker exited before responding (exit code {returncode})"
                    )
                time.sleep(self._poll_interval)
            raise WorkerInfrastructureError("persistent transcription worker timed out")
        finally:
            response_path.unlink(missing_ok=True)

    def _ensure_process(
        self,
        command: Sequence[str],
        signature: tuple[Any, ...],
        *,
        cwd: Path | str | None,
        env: dict[str, str] | None,
    ) -> Any:
        if self._process is not None:
            if self._signature != signature or self._process.poll() is not None:
                self._stop_unlocked()
        if self._process is None:
            self._process = self._process_factory(
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                cwd=str(cwd) if cwd is not None else None,
                env=env,
            )
            self._signature = signature
        return self._process

    def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._signature = None
        if process is None:
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
