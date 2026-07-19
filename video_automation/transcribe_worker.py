from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .io_utils import read_json_file
from .process_tree import attach_process_tree, process_group_popen_kwargs, terminate_process_tree
from .task_queue import QueueControlRequested


class WorkerInfrastructureError(RuntimeError):
    pass


class WorkerNoProgressError(WorkerInfrastructureError):
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
        self._log_handle: Any | None = None

    def run(
        self,
        *,
        command: Sequence[str],
        signature: tuple[Any, ...],
        request: dict[str, Any],
        timeout_seconds: float,
        no_progress_timeout_seconds: float | None = None,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        log_path: Path | str | None = None,
        log_max_bytes: int = 5 * 1024 * 1024,
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
                        no_progress_timeout_seconds=no_progress_timeout_seconds,
                        cwd=cwd,
                        env=env,
                        log_path=log_path,
                        log_max_bytes=log_max_bytes,
                        control_callback=control_callback,
                    )
                except QueueControlRequested:
                    self._stop_unlocked()
                    raise
                except WorkerInfrastructureError as exc:
                    last_error = exc
                    self._stop_unlocked()
                    if isinstance(exc, WorkerNoProgressError):
                        break
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
        no_progress_timeout_seconds: float | None,
        cwd: Path | str | None,
        env: dict[str, str] | None,
        log_path: Path | str | None,
        log_max_bytes: int,
        control_callback: Callable[[], str | None] | None,
    ) -> dict[str, Any]:
        process = self._ensure_process(
            command,
            signature,
            cwd=cwd,
            env=env,
            log_path=log_path,
            log_max_bytes=log_max_bytes,
        )
        job_dir = Path(str(request["job_dir"]))
        job_dir.mkdir(parents=True, exist_ok=True)
        response_path = job_dir / f".transcribe-response-{uuid.uuid4().hex}.json"
        heartbeat_path = job_dir / f".transcribe-heartbeat-{uuid.uuid4().hex}.json"
        payload = dict(request)
        payload["response_path"] = str(response_path)
        payload["heartbeat_path"] = str(heartbeat_path)

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
        progress_timeout = max(
            0.01,
            float(no_progress_timeout_seconds)
            if no_progress_timeout_seconds is not None
            else float(timeout_seconds),
        )
        last_progress_at = time.monotonic()
        last_heartbeat_mtime_ns = 0
        last_phase = "request_sent"
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
                try:
                    heartbeat_mtime_ns = heartbeat_path.stat().st_mtime_ns
                except OSError:
                    heartbeat_mtime_ns = 0
                if heartbeat_mtime_ns and heartbeat_mtime_ns != last_heartbeat_mtime_ns:
                    last_heartbeat_mtime_ns = heartbeat_mtime_ns
                    last_progress_at = time.monotonic()
                    heartbeat = read_json_file(heartbeat_path)
                    if isinstance(heartbeat, dict):
                        last_phase = str(heartbeat.get("phase") or last_phase)
                returncode = process.poll()
                if returncode is not None:
                    raise WorkerInfrastructureError(
                        f"persistent transcription worker exited before responding (exit code {returncode})"
                    )
                stalled_for = time.monotonic() - last_progress_at
                if stalled_for >= progress_timeout:
                    raise WorkerNoProgressError(
                        "persistent transcription worker made no progress "
                        f"for {stalled_for:.1f}s (last phase: {last_phase})"
                    )
                time.sleep(self._poll_interval)
            raise WorkerInfrastructureError("persistent transcription worker timed out")
        finally:
            response_path.unlink(missing_ok=True)
            heartbeat_path.unlink(missing_ok=True)

    def _ensure_process(
        self,
        command: Sequence[str],
        signature: tuple[Any, ...],
        *,
        cwd: Path | str | None,
        env: dict[str, str] | None,
        log_path: Path | str | None,
        log_max_bytes: int,
    ) -> Any:
        if self._process is not None:
            if self._signature != signature or self._process.poll() is not None:
                self._stop_unlocked()
        if self._process is None:
            self._log_handle = self._open_log(log_path, log_max_bytes)
            output_target = self._log_handle if self._log_handle is not None else subprocess.DEVNULL
            self._process = self._process_factory(
                list(command),
                stdin=subprocess.PIPE,
                stdout=output_target,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                **process_group_popen_kwargs(),
            )
            attach_process_tree(self._process)
            self._signature = signature
        return self._process

    def _open_log(self, log_path: Path | str | None, max_bytes: int) -> Any | None:
        if log_path is None:
            return None
        path = Path(log_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size >= max(1024, int(max_bytes)):
                rotated = path.with_suffix(path.suffix + ".1")
                rotated.unlink(missing_ok=True)
                os.replace(path, rotated)
            return path.open("ab")
        except OSError:
            return None

    def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._signature = None
        if process is None:
            self._close_log()
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        if process.poll() is not None:
            self._close_log()
            return
        if int(getattr(process, "pid", 0) or 0) > 0:
            terminate_process_tree(process, timeout=3)
        else:
            try:
                process.terminate()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        self._close_log()

    def _close_log(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            pass
