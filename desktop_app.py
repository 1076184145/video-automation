from __future__ import annotations

import errno
import sys
import threading
import time
import traceback
import webbrowser
from contextlib import suppress
from http.server import ThreadingHTTPServer
from pathlib import Path

from video_automation.api import create_server
from video_automation.config import Settings
from video_automation.worker import bootstrap_dirs, health_payload


APP_TITLE = "Video Automation"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 960
MIN_WIDTH = 1100
MIN_HEIGHT = 700


def main() -> int:
    settings = Settings.load()
    bootstrap_dirs(settings)
    health = health_payload(settings)
    missing_required = _missing_required_tools(health)
    route = "/#/health" if missing_required else "/#/"
    url = _app_url(settings, route)
    server, started = _start_server(settings)
    if started:
        print(f"{APP_TITLE} API listening on {url}", flush=True)
    else:
        print(f"{APP_TITLE} API appears to be running already; opening {url}", flush=True)
    if missing_required:
        _show_missing_tools_notice(missing_required)
    try:
        _open_window(url, server if started else None)
    finally:
        if started:
            _stop_server(server)
    return 0


def _app_url(settings: Settings, route: str = "/#/") -> str:
    host = settings.api_host.strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    suffix = route if route.startswith("/") else f"/{route}"
    return f"http://{host}:{settings.api_port}{suffix}"


def _missing_required_tools(health: dict[str, object]) -> list[str]:
    checks = health.get("checks")
    if not isinstance(checks, list):
        return []
    missing: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("exists") is False and check.get("optional") is False:
            missing.append(str(check.get("name") or "unknown"))
    return missing


def _show_missing_tools_notice(names: list[str]) -> None:
    message = (
        "Some required tools are missing:\n\n"
        + "\n".join(f"- {name}" for name in names)
        + "\n\nThe app will open the Health page first so you can fix the configuration."
    )
    print(message, flush=True)
    try:
        import tkinter
        from tkinter import messagebox
    except Exception:
        return
    try:
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showwarning(APP_TITLE, message)
        root.destroy()
    except Exception:
        return


def _start_server(settings: Settings) -> tuple[ThreadingHTTPServer | None, bool]:
    try:
        server = create_server(settings)
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, 10048}:
            return None, False
        raise
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    thread.start()
    return server, True


def _open_window(url: str, server: ThreadingHTTPServer | None) -> None:
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        _open_browser(url, server)
        return

    window = webview.create_window(
        APP_TITLE,
        url,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
    )
    with suppress(Exception):
        window.events.closed += lambda: _stop_server(server)
    try:
        webview.start()
    finally:
        _stop_server(server)


def _open_browser(url: str, server: ThreadingHTTPServer | None) -> None:
    webbrowser.open(url)
    print("pywebview is not installed; opened the system browser instead.", flush=True)
    print("Press Ctrl+C in this terminal to stop the local service.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _stop_server(server)


def _stop_server(server: ThreadingHTTPServer | None) -> None:
    if server is None:
        return
    with suppress(Exception):
        server.shutdown()
    with suppress(Exception):
        server.server_close()


def _startup_log_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "desktop_app_error.log"
    return Path(__file__).resolve().parent / "desktop_app_error.log"


def _write_startup_error() -> None:
    try:
        _startup_log_path().write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        return


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        _write_startup_error()
        raise
