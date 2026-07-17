from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from video_automation.credentials import MemoryCredentialStore
from video_automation.providers.bilibili import BilibiliHttpTransport, BilibiliProvider


class BilibiliProviderTests(unittest.TestCase):
    def test_resumable_upload_continues_from_persisted_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "final.mp4"
            video.write_bytes(b"0123456789")
            credentials = MemoryCredentialStore()
            credentials.set(
                "bilibili:creator-one",
                json.dumps({"access_token": "secret-token", "client_id": "client-one"}),
            )

            class Transport:
                def __init__(self):
                    self.chunks = []

                def validate(self, token, client_id):
                    self.validated = (token, client_id)
                    return {"can_publish": True}

                def create_upload(self, token, metadata, total_bytes, previous_url=None):
                    return {"upload_url": previous_url or "sandbox://upload/one", "upload_id": "upload-one"}

                def upload_chunk(self, token, session, chunk, offset, total_bytes):
                    self.chunks.append((offset, bytes(chunk)))
                    return offset + len(chunk)

                def complete_upload(self, token, session):
                    return {"video_id": "video-one"}

                def publish(self, token, video_id, metadata):
                    return {"remote_id": "BV1demo", "status": "processing"}

                def query(self, token, remote_id):
                    return {"remote_id": remote_id, "status": "published"}

            transport = Transport()
            provider = BilibiliProvider(credentials, transport, chunk_size=4)
            attempt = {
                "credential_ref": "bilibili:creator-one",
                "payload": {"video_path": str(video), "title": "Demo"},
                "uploaded_bytes": 4,
                "upload_url": "sandbox://upload/one",
            }
            progress = []

            provider.validate(attempt)
            result = provider.upload(attempt, lambda uploaded, url: progress.append((uploaded, url)))

            self.assertEqual(transport.validated, ("secret-token", "client-one"))
            self.assertEqual(transport.chunks, [(4, b"4567"), (8, b"89")])
            self.assertEqual([value for value, _url in progress], [8, 10])
            self.assertEqual(result["remote_id"], "BV1demo")

    def test_missing_secure_credential_is_not_treated_as_retryable_network_failure(self) -> None:
        provider = BilibiliProvider(MemoryCredentialStore(), object())
        with self.assertRaises(PermissionError):
            provider.validate({"credential_ref": "bilibili:missing", "payload": {}})

    def test_configured_http_transport_uses_bearer_auth_and_resumable_offsets(self) -> None:
        calls = []

        def request(method, url, headers, body):
            calls.append((method, url, headers, body))
            if url.endswith("/validate"):
                return {"can_publish": True}
            if url.endswith("/upload/init"):
                return {"upload_url": "https://upload.test/chunk", "upload_id": "u1"}
            if url == "https://upload.test/chunk":
                return {"next_offset": 4}
            if url.endswith("/upload/complete"):
                return {"video_id": "v1"}
            if url.endswith("/publish"):
                return {"remote_id": "BV1", "status": "processing"}
            return {"remote_id": "BV1", "status": "published"}

        transport = BilibiliHttpTransport(
            "https://sandbox.test",
            {
                "validate": "/validate",
                "create_upload": "/upload/init",
                "complete_upload": "/upload/complete",
                "publish": "/publish",
                "query": "/query/{remote_id}",
            },
            request=request,
            resolve=lambda *_args, **_kwargs: [
                (2, 1, 6, "", ("1.1.1.1", 443)),
            ],
        )

        self.assertTrue(transport.validate("token", "client")["can_publish"])
        session = transport.create_upload("token", {"title": "Demo"}, 10)
        self.assertEqual(transport.upload_chunk("token", session, b"0123", 0, 10), 4)
        self.assertEqual(transport.complete_upload("token", session)["video_id"], "v1")
        self.assertEqual(transport.publish("token", "v1", {"title": "Demo"})["remote_id"], "BV1")
        self.assertEqual(transport.query("token", "BV1")["status"], "published")
        platform_calls = [call for call in calls if call[1].startswith("https://sandbox.test/")]
        upload_calls = [call for call in calls if call[1] == "https://upload.test/chunk"]
        self.assertTrue(all(call[2]["Authorization"] == "Bearer token" for call in platform_calls))
        self.assertNotIn("Authorization", upload_calls[0][2])

    def test_control_endpoints_must_remain_on_the_configured_origin(self) -> None:
        calls = []
        transport = BilibiliHttpTransport(
            "https://sandbox.test",
            {
                "validate": "https://other.test/validate",
                "create_upload": "/upload/init",
                "complete_upload": "/upload/complete",
                "publish": "/publish",
                "query": "/query/{remote_id}",
            },
            request=lambda *args: calls.append(args) or {},
        )

        with self.assertRaisesRegex(RuntimeError, "configured origin"):
            transport.validate("token", "client")
        self.assertEqual(calls, [])

    def test_cross_origin_upload_url_must_resolve_to_a_public_address(self) -> None:
        calls = []
        transport = BilibiliHttpTransport(
            "https://sandbox.test",
            {
                "validate": "/validate",
                "create_upload": "/upload/init",
                "complete_upload": "/upload/complete",
                "publish": "/publish",
                "query": "/query/{remote_id}",
            },
            request=lambda *args: calls.append(args) or {"next_offset": 4},
            resolve=lambda *_args, **_kwargs: [
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "public address"):
            transport.upload_chunk(
                "token",
                {"upload_url": "https://upload.test/chunk"},
                b"0123",
                0,
                4,
            )
        self.assertEqual(calls, [])

    def test_authenticated_control_request_does_not_follow_redirects(self) -> None:
        redirected_headers = []

        class DestinationHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                redirected_headers.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"can_publish": true}')

            def log_message(self, _format, *_args):
                return

        destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
        destination_thread = threading.Thread(target=destination.serve_forever, daemon=True)
        destination_thread.start()
        destination_url = f"http://127.0.0.1:{destination.server_address[1]}/capture"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                content_length = int(self.headers.get("Content-Length") or 0)
                if content_length:
                    self.rfile.read(content_length)
                self.send_response(302)
                self.send_header("Location", destination_url)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        redirector = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirector_thread = threading.Thread(target=redirector.serve_forever, daemon=True)
        redirector_thread.start()
        base_url = f"http://127.0.0.1:{redirector.server_address[1]}"
        transport = BilibiliHttpTransport(
            base_url,
            {
                "validate": "/redirect",
                "create_upload": "/upload/init",
                "complete_upload": "/upload/complete",
                "publish": "/publish",
                "query": "/query/{remote_id}",
            },
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
                transport.validate("dummy-token", "client")
            self.assertEqual(redirected_headers, [])
        finally:
            redirector.shutdown()
            redirector.server_close()
            redirector_thread.join(timeout=5)
            destination.shutdown()
            destination.server_close()
            destination_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
