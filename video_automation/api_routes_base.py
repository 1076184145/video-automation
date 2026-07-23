from __future__ import annotations

import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .api_context import APIContext
from .api_http_utils import event_last_id, format_sse, parse_range
from .api_system import health_response, publish_package_queue, recording_files, tools_install_snapshot
from .config import Settings
from .events import current_event_id, wait_for_events
from .jobs import list_jobs
from .library_api import dispatch_library_request
from .routing import CORE_ROUTER, RouteMatch


mimetypes.add_type("font/woff2", ".woff2")

CHUNK_SIZE = 1024 * 1024
MAX_JSON_BODY_SIZE = 2 * 1024 * 1024


class CoreHTTPRoutes:
    """HTTP transport, static files, SSE, and generic route dispatch."""

    api_context: APIContext

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._require_allowed_origin():
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_allowed_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            response = dispatch_library_request(
                self.api_context.settings,
                "GET",
                unquote(parsed.path),
            )
            if response is not None:
                status, payload = response
                self._json(payload, status=status)
                return
        if not self._dispatch_core_route("GET", parsed.path, parsed.query):
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_allowed_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            payload = self._read_json()
            if payload is None:
                return
            response = dispatch_library_request(
                self.api_context.settings,
                "POST",
                unquote(parsed.path),
                payload,
            )
            if response is not None:
                status, body = response
                self._json(body, status=status)
                return
        if not self._dispatch_core_route("POST", parsed.path, parsed.query):
            self._json({"error": "not found"}, status=404)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._require_allowed_origin():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            response = dispatch_library_request(
                self.api_context.settings,
                "DELETE",
                unquote(parsed.path),
            )
            if response is not None:
                status, payload = response
                self._json(payload, status=status)
                return
        if not self._dispatch_core_route("DELETE", parsed.path, parsed.query):
            self._json({"error": "not found"}, status=404)

    def _dispatch_core_route(self, method: str, path: str, query: str) -> bool:
        matched = CORE_ROUTER.resolve(method, path)
        if matched is None:
            return False
        handler = getattr(self, f"_route_{matched.endpoint}", None)
        if not callable(handler):
            return False
        handler(matched, query)
        return True

    def _route_root(self, _matched: RouteMatch, _query: str) -> None:
        self._send_static_file("index.html")

    def _route_static_file(self, matched: RouteMatch, _query: str) -> None:
        self._send_static_file(matched.params["asset_path"])

    def _route_health(self, _matched: RouteMatch, _query: str) -> None:
        payload = health_response(Settings.load())
        queue_worker = getattr(self.server, "queue_worker", None)
        payload["queue_worker"] = (
            queue_worker.status()
            if queue_worker is not None
            else {
                "mode": "disabled",
                "running": False,
                "pid": None,
                "workers": 0,
                "restart_count": 0,
            }
        )
        self._json(payload)

    def _route_recordings(self, _matched: RouteMatch, _query: str) -> None:
        self._json(recording_files(self.api_context.settings))

    def _route_publish_packages(self, _matched: RouteMatch, _query: str) -> None:
        self._json(publish_package_queue(self.api_context.settings))

    def _route_events(self, _matched: RouteMatch, query: str) -> None:
        self._send_events(query)

    def _route_job_file(self, matched: RouteMatch, query: str) -> None:
        self._send_job_file(
            matched.params.get("job_name", ""),
            matched.params["filename"],
            query,
        )

    def _send_events(self, query: str = "") -> None:
        requested_last_id = event_last_id(self.headers.get("Last-Event-ID"), query)
        last_id = requested_last_id
        snapshot_id = current_event_id()
        try:
            self.send_response(200)
            self._cors_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(
                format_sse(
                    "hello",
                    {
                        "jobs": [self._job_payload(job) for job in list_jobs(self.api_context.settings)],
                        "tools_install": tools_install_snapshot(),
                        "server_time": datetime.now().isoformat(timespec="seconds"),
                    },
                    event_id=snapshot_id,
                ).encode("utf-8")
            )
            self.wfile.flush()
            if requested_last_id <= 0 or requested_last_id > snapshot_id:
                last_id = snapshot_id
            while True:
                events = wait_for_events(last_id, timeout_seconds=15.0)
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    last_id = event.id
                    self.wfile.write(
                        format_sse(event.type, event.payload, event_id=event.id).encode("utf-8")
                    )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _send_job_file(self, job_name: str, filename: str, query: str = "") -> None:
        settings = self.api_context.settings
        job_dir = (settings.jobs_dir / job_name).resolve()
        try:
            job_dir.relative_to(settings.jobs_dir.resolve())
        except ValueError:
            self._json({"error": "invalid job"}, status=400)
            return
        path = (job_dir / filename).resolve()
        try:
            path.relative_to(job_dir)
        except ValueError:
            self._json({"error": "invalid file"}, status=400)
            return
        if not path.is_file():
            self._json({"error": "file not found"}, status=404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_file(path, content_type, attachment=("download=1" in query))

    def _send_static_file(self, raw_path: str) -> None:
        web_root = (self.api_context.settings.root / "web").resolve()
        path = (web_root / raw_path).resolve()
        try:
            path.relative_to(web_root)
        except ValueError:
            self._json({"error": "invalid static path"}, status=400)
            return
        if not path.is_file():
            self._json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_file(
            path,
            content_type,
            attachment=False,
            cache_control="no-store, max-age=0",
        )

    def _send_file(
        self,
        path: Path,
        content_type: str,
        *,
        attachment: bool,
        cache_control: str | None = None,
    ) -> None:
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        byte_range = parse_range(range_header, size) if range_header else None
        if range_header and byte_range is None:
            self.send_response(416)
            self._cors_headers()
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        start, end = byte_range if byte_range else (0, max(0, size - 1))
        content_length = max(0, end - start + 1)
        self.send_response(206 if byte_range else 200)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = content_length
            try:
                while remaining > 0:
                    chunk = handle.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._json({"error": "invalid Content-Length"}, status=400)
            return None
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_SIZE:
            self._json({"error": "request body too large"}, status=413)
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self._json({"error": "invalid JSON body"}, status=400)
            return None
        if not isinstance(payload, dict):
            self._json({"error": "JSON body must be an object"}, status=400)
            return None
        return payload

    def _json(self, payload: Any, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        origin = self.api_context.cors_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header(
            "Access-Control-Expose-Headers",
            "Accept-Ranges, Content-Range, Content-Length, Content-Disposition",
        )

    def _require_allowed_origin(self) -> bool:
        if self.api_context.origin_is_allowed(self.headers.get("Origin")):
            return True
        self._json({"error": "origin not allowed"}, status=403)
        return False
