from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_automation import covers, render
from video_automation.jobs import load_job
from video_automation.subtitle_translation import _validate_translation_workload


class SecurityHardeningTests(unittest.TestCase):
    def test_load_job_ignores_serialized_job_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "jobs" / "demo"
            job_dir.mkdir(parents=True)
            outside = root / "outside"
            state_path = job_dir / "job.json"
            state_path.write_text(
                json.dumps({
                    "source_path": str(root / "source.mp4"),
                    "job_dir": str(outside),
                    "status": "needs_review",
                }),
                encoding="utf-8",
            )

            job = load_job(state_path)

            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job.job_dir, job_dir)

    def test_crop_filter_allows_generated_templates_only(self) -> None:
        self.assertEqual(
            render._safe_crop_filter("crop=100:200:0:0,scale=1080:1920"),
            "crop=100:200:0:0,scale=1080:1920",
        )
        self.assertIsNone(render._safe_crop_filter("movie=/etc/passwd,scale=1080:1920"))
        self.assertIsNone(render._safe_crop_filter("crop=100:200:0:0;movie=http://127.0.0.1/x"))

    def test_remote_cover_image_rejects_private_hosts(self) -> None:
        for url in [
            "http://127.0.0.1/image.png",
            "http://localhost/image.png",
            "http://10.0.0.1/image.png",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/image.png",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError):
                    covers._validate_remote_image_url(url)

    def test_remote_cover_image_connects_to_the_validated_address(self) -> None:
        resolver_calls = []
        connection_calls = []

        def resolve(host, port, *, type):
            resolver_calls.append((host, port, type))
            return [(2, 1, 6, "", ("93.184.216.34", port))]

        class Response:
            status = 200
            headers = {"Content-Length": "4"}

            def __init__(self):
                self._data = b"data"

            def read(self, _size):
                data, self._data = self._data, b""
                return data

        class Connection:
            def request(self, method, target, *, headers):
                connection_calls.append((method, target, headers))

            def getresponse(self):
                return Response()

            def close(self):
                return None

        def connection_factory(parsed, address, timeout):
            connection_calls.append((parsed.hostname, address, timeout))
            return Connection()

        data = covers._fetch_remote_image(
            "https://example.test/image.png?size=cover",
            resolve=resolve,
            connection_factory=connection_factory,
        )

        self.assertEqual(data, b"data")
        self.assertEqual(len(resolver_calls), 1)
        self.assertEqual(connection_calls[0][1], "93.184.216.34")
        self.assertEqual(connection_calls[1][1], "/image.png?size=cover")

    def test_remote_cover_image_enforces_total_deadline(self) -> None:
        class Response:
            status = 200
            headers = {}

            def read(self, _size):
                return b"x"

        class Connection:
            sock = None

            def request(self, _method, _target, *, headers):
                return headers

            def getresponse(self):
                return Response()

            def close(self):
                return None

        ticks = iter([0.0, 0.0, 61.0])
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            covers._fetch_remote_image(
                "https://example.test/image.png",
                resolve=lambda *_args, **_kwargs: [
                    (2, 1, 6, "", ("93.184.216.34", 443)),
                ],
                connection_factory=lambda *_args: Connection(),
                clock=lambda: next(ticks),
            )

    def test_cover_image_pixel_budget_rejects_oversized_dimensions(self) -> None:
        covers._validate_cover_image_dimensions(4096, 4096)
        with self.assertRaisesRegex(RuntimeError, "pixel limit"):
            covers._validate_cover_image_dimensions(10_000, 10_000)

    def test_translation_workload_has_global_limits(self) -> None:
        with self.assertRaises(RuntimeError):
            _validate_translation_workload([{"text": "x"} for _ in range(1201)])
        with self.assertRaises(RuntimeError):
            _validate_translation_workload([{"text": "x" * 240001}])


if __name__ == "__main__":
    unittest.main()
