from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from ctypes import wintypes

from .config import Settings
from .events import configure_event_store
from .library_api import library_database_path, queue_repository_for
from .process_tree import attach_process_tree, process_group_popen_kwargs, terminate_process_tree
from .task_queue import QueueService


class QueueWorkerProcess:
    """Supervise queue execution outside the API process."""

    def __init__(
        self,
        settings: Settings,
        *,
        workers: int | None = None,
        owner_pid: int | None = None,
        max_rapid_restarts: int = 3,
        restart_window_seconds: float = 60.0,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.settings = settings
        self.workers = max(1, int(workers or settings.api_parallel_jobs))
        self.owner_pid = int(owner_pid or os.getpid())
        self.max_rapid_restarts = max(1, int(max_rapid_restarts))
        self.restart_window_seconds = max(5.0, float(restart_window_seconds))
        self._process_factory = process_factory
        self._process: Any | None = None
        self._worker_pid: int | None = None
        self._worker_pid_path = self.settings.logs_dir / f"queue_worker_{self.owner_pid}.json"
        self._log_handle: Any | None = None
        self._lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self.restart_count = 0
        self.last_exit_code: int | None = None
        self.circuit_open = False
        self._restart_times: deque[float] = deque()

    @property
    def pid(self) -> int | None:
        self._refresh_worker_pid()
        if self._worker_pid and process_is_alive(self._worker_pid):
            return self._worker_pid
        return getattr(self._process, "pid", None) if self._launcher_is_running else None

    @property
    def is_running(self) -> bool:
        if self._launcher_is_running:
            self._refresh_worker_pid()
            return True
        self._refresh_worker_pid()
        return bool(self._worker_pid and process_is_alive(self._worker_pid))

    @property
    def _launcher_is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._monitor_stop.clear()
            self.circuit_open = False
            self._restart_times.clear()
            self._spawn_locked()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="video-automation-queue-supervisor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _spawn_locked(self) -> None:
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        self._worker_pid = None
        try:
            self._worker_pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._close_log()
        self._log_handle = (self.settings.logs_dir / "queue_worker.log").open("ab")
        env = os.environ.copy()
        env["VIDEO_AUTOMATION_ROOT"] = str(self.settings.root)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self._process = self._process_factory(
                self._command(),
                cwd=str(self.settings.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                **process_group_popen_kwargs(),
            )
            attach_process_tree(self._process)
        except Exception:
            self._close_log()
            raise

    def stop(self) -> None:
        self._monitor_stop.set()
        monitor = self._monitor_thread
        self._monitor_thread = None
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=2)
        with self._lock:
            process = self._process
            self._process = None
        if process is not None:
            terminate_process_tree(process)
        self._close_log()

    def status(self) -> dict[str, Any]:
        return {
            "mode": "process",
            "running": self.is_running,
            "pid": self.pid,
            "workers": self.workers,
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
            "circuit_open": self.circuit_open,
        }

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(1.0):
            with self._lock:
                if self.is_running:
                    continue
                process = self._process
                exit_code = process.poll() if process is not None else None
                if process is not None:
                    terminate_process_tree(process)
                if exit_code is not None:
                    self.last_exit_code = int(exit_code)
                    self._write_supervisor_log(
                        f"Queue worker exited unexpectedly with code {exit_code}; restarting."
                    )
                self._process = None
                now = time.monotonic()
                while self._restart_times and now - self._restart_times[0] > self.restart_window_seconds:
                    self._restart_times.popleft()
                if len(self._restart_times) >= self.max_rapid_restarts:
                    self.circuit_open = True
                    self._write_supervisor_log(
                        "Queue worker restart circuit opened after repeated rapid exits."
                    )
                    self._close_log()
                    self._monitor_stop.set()
                    return
                self._restart_times.append(now)
                self.restart_count += 1
                try:
                    self._spawn_locked()
                except Exception:
                    self._process = None
                    self._close_log()

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--queue-worker",
                "--workers",
                str(self.workers),
                "--owner-pid",
                str(self.owner_pid),
                "--pid-file",
                str(self._worker_pid_path),
            ]
        return [
            sys.executable,
            "-m",
            "video_automation.queue_worker",
            "--workers",
            str(self.workers),
            "--owner-pid",
            str(self.owner_pid),
            "--pid-file",
            str(self._worker_pid_path),
        ]

    def _refresh_worker_pid(self) -> None:
        try:
            payload = json.loads(self._worker_pid_path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if pid > 0:
            self._worker_pid = pid

    def _close_log(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _write_supervisor_log(self, message: str) -> None:
        handle = self._log_handle
        if handle is None:
            return
        try:
            timestamp = datetime.now().isoformat(timespec="seconds")
            handle.write(f"{timestamp} [SUPERVISOR] {message}\n".encode("utf-8"))
            handle.flush()
        except OSError:
            pass


def recover_stale_queue_items(
    repository: Any,
    *,
    stale_seconds: float = 30.0,
    now: datetime | None = None,
) -> int:
    cutoff = (now or datetime.now()) - timedelta(seconds=max(1.0, stale_seconds))
    return int(repository.recover_interrupted(cutoff.isoformat(timespec="seconds")))


def process_is_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def run_queue_worker(
    settings: Settings,
    *,
    workers: int,
    owner_pid: int = 0,
    pid_file: Path | None = None,
) -> int:
    # Imported here so the API module can import QueueWorkerProcess without a
    # circular import during server construction.
    from .api import _execute_queue_item, _start_transcription_warmup
    from .worker import bootstrap_dirs

    bootstrap_dirs(settings)
    database_path = library_database_path(settings)
    configure_event_store(database_path)
    if pid_file is not None:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        temp_pid_file = pid_file.with_suffix(pid_file.suffix + ".tmp")
        temp_pid_file.write_text(
            json.dumps({"pid": os.getpid(), "owner_pid": owner_pid}),
            encoding="utf-8",
        )
        temp_pid_file.replace(pid_file)
    repository = queue_repository_for(settings)
    recover_stale_queue_items(repository)
    service = QueueService(
        repository,
        lambda item: _execute_queue_item(Settings.load(), item),
    )
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is not None:
            try:
                signal.signal(signum, request_stop)
            except (OSError, ValueError):
                pass

    service.start(workers=max(1, workers))
    _start_transcription_warmup(settings)
    next_recovery_at = time.monotonic() + 5.0
    owner_misses = 0
    try:
        while not stop.wait(0.5):
            if owner_pid > 0:
                if process_is_alive(owner_pid):
                    owner_misses = 0
                else:
                    owner_misses += 1
                    if owner_misses >= 5:
                        break
            if time.monotonic() < next_recovery_at:
                continue
            recover_stale_queue_items(repository)
            next_recovery_at = time.monotonic() + 5.0
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        if pid_file is not None:
            try:
                payload = json.loads(pid_file.read_text(encoding="utf-8"))
                if int(payload.get("pid") or 0) == os.getpid():
                    pid_file.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Video Automation queue worker")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--owner-pid", type=int, default=0)
    parser.add_argument("--pid-file", type=Path)
    args = parser.parse_args(argv)
    settings = Settings.load()
    return run_queue_worker(
        settings,
        workers=max(1, args.workers or settings.api_parallel_jobs),
        owner_pid=max(0, args.owner_pid),
        pid_file=args.pid_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
