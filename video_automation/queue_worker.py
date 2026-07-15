from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from .config import Settings
from .events import configure_event_store
from .library_api import library_database_path, queue_repository_for
from .task_queue import QueueService


class QueueWorkerProcess:
    """Supervise queue execution outside the API process."""

    def __init__(
        self,
        settings: Settings,
        *,
        workers: int | None = None,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.settings = settings
        self.workers = max(1, int(workers or settings.api_parallel_jobs))
        self._process_factory = process_factory
        self._process: Any | None = None
        self._log_handle: Any | None = None
        self._lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self.restart_count = 0
        self.last_exit_code: int | None = None

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None) if self.is_running else None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._monitor_stop.clear()
            self._spawn_locked()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="video-automation-queue-supervisor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _spawn_locked(self) -> None:
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log_handle = (self.settings.logs_dir / "queue_worker.log").open("ab")
        env = os.environ.copy()
        env["VIDEO_AUTOMATION_ROOT"] = str(self.settings.root)
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._process = self._process_factory(
                self._command(),
                cwd=str(self.settings.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
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
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._close_log()

    def status(self) -> dict[str, Any]:
        return {
            "mode": "process",
            "running": self.is_running,
            "pid": self.pid,
            "workers": self.workers,
            "restart_count": self.restart_count,
            "last_exit_code": self.last_exit_code,
        }

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(1.0):
            with self._lock:
                if self.is_running:
                    continue
                process = self._process
                exit_code = process.poll() if process is not None else None
                if exit_code is not None:
                    self.last_exit_code = int(exit_code)
                    self._write_supervisor_log(
                        f"Queue worker exited unexpectedly with code {exit_code}; restarting."
                    )
                self._process = None
                self.restart_count += 1
                try:
                    self._spawn_locked()
                except Exception:
                    self._process = None
                    self._close_log()

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--queue-worker", "--workers", str(self.workers)]
        return [sys.executable, "-m", "video_automation.queue_worker", "--workers", str(self.workers)]

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


def run_queue_worker(settings: Settings, *, workers: int) -> int:
    # Imported here so the API module can import QueueWorkerProcess without a
    # circular import during server construction.
    from .api import _execute_queue_item, _start_transcription_warmup
    from .worker import bootstrap_dirs

    bootstrap_dirs(settings)
    database_path = library_database_path(settings)
    configure_event_store(database_path)
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
    try:
        while not stop.wait(0.5):
            if time.monotonic() < next_recovery_at:
                continue
            recover_stale_queue_items(repository)
            next_recovery_at = time.monotonic() + 5.0
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Video Automation queue worker")
    parser.add_argument("--workers", type=int, default=0)
    args = parser.parse_args(argv)
    settings = Settings.load()
    return run_queue_worker(settings, workers=max(1, args.workers or settings.api_parallel_jobs))


if __name__ == "__main__":
    raise SystemExit(main())
