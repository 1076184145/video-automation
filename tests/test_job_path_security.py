from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from video_automation import api


class JobPathSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.jobs_dir = self.root / "processing" / "jobs"
        self.jobs_dir.mkdir(parents=True)
        self.outside = self.root / "outside"
        self._write_job(self.outside, "outside")
        self._write_job(self.root / "processing" / "jobs-backup", "sibling")
        for name in ("sample", "中文 demo", "literal%2fjob"):
            self._write_job(self.jobs_dir / name, name)
        (self.jobs_dir / "alias").mkdir()
        self.settings = SimpleNamespace(
            root=self.root, jobs_dir=self.jobs_dir, api_host="127.0.0.1",
            api_port=0, api_parallel_jobs=1, api_allowed_origins=(),
        )
        self.server = api.create_server(self.settings, start_queue_worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _write_job(self, directory: Path, marker: str) -> None:
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({
            "source_path": str(self.root / f"{marker}.mp4"), "status": "done",
        }), encoding="utf-8")
        (directory / "feedback.json").write_text(json.dumps({
            "items": [], "marker": marker,
        }), encoding="utf-8")
        (directory / "fixture.txt").write_text(marker, encoding="utf-8")

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, method: str, path: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=b"{}" if method == "POST" else None)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def _assert_invalid_job(self, method: str, path: str) -> None:
        status, body = self._request(method, path)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"error": "invalid job"})

    def test_details_keep_ordinary_names_aliases_and_single_decoding(self) -> None:
        for name, expected in (
            ("sample", "sample"), ("中文 demo", "中文 demo"),
            ("literal%2fjob", "literal%2fjob"), ("alias/../sample", "sample"),
        ):
            with self.subTest(name=name):
                status, body = self._request("GET", f"/jobs/{quote(name, safe='')}")
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertEqual(payload["feedback"]["marker"], expected)
                self.assertEqual(Path(payload["job_dir"]), (self.jobs_dir / expected).resolve())
                self.assertIn("fixture.txt", [entry["name"] for entry in payload["files"]])
        status, body = self._request("GET", "/jobs/missing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {"error": "job not found"})
        self.assertEqual(self._request("GET", "/jobs/sample/files/fixture.txt"), (200, b"sample"))

    def test_details_reject_encoded_and_absolute_outside_paths(self) -> None:
        names = [
            "..%2F..%2Foutside", "%2e%2E%2f%2e%2e%2foutside",
            "..%2fjobs-backup", quote(str(self.outside.resolve()), safe=""),
        ]
        if os.name == "nt":
            names.extend((
                "..%5C..%5Coutside", "..%5c..%2Foutside",
                quote(str(self.outside.resolve()).replace("\\", "/"), safe=""),
            ))
        for name in names:
            with self.subTest(name=name):
                self._assert_invalid_job("GET", f"/jobs/{name}")

    def test_shared_routes_reject_outside_job_without_reading_or_mutating_it(self) -> None:
        before = (self.outside / "job.json").read_bytes()
        with patch("video_automation.api_routes_jobs.load_job") as load:
            for method, suffix in (
                ("GET", ""), ("POST", "/approve"), ("POST", "/cancel"),
                ("DELETE", ""), ("GET", "/files/fixture.txt"),
            ):
                with self.subTest(method=method, suffix=suffix):
                    self._assert_invalid_job(method, f"/jobs/..%2F..%2Foutside{suffix}")
            load.assert_not_called()
        self.assertEqual((self.outside / "job.json").read_bytes(), before)

    def test_resolution_errors_return_bad_request_and_keep_server_usable(self) -> None:
        for error in (OSError("unresolvable path"), ValueError("invalid path"), RuntimeError("symlink loop")):
            with self.subTest(error=type(error).__name__):
                with patch("video_automation.jobs.Path.resolve", side_effect=error):
                    self._assert_invalid_job("GET", "/jobs/sample")
        self.assertEqual(self._request("GET", "/jobs/sample")[0], 200)

    def test_directory_links_cannot_escape_and_internal_files_remain_downloadable(self) -> None:
        try:
            (self.jobs_dir / "outside-link").symlink_to(self.outside, target_is_directory=True)
            (self.jobs_dir / "inside-link").symlink_to(self.jobs_dir / "sample", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        self._assert_invalid_job("GET", "/jobs/outside-link")
        status, body = self._request("GET", "/jobs/inside-link/files/fixture.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"sample")


class QueueJobPathSecurityTests(unittest.TestCase):
    def test_outside_queue_names_are_rejected_before_loading_or_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jobs_dir = root / "processing" / "jobs"
            jobs_dir.mkdir(parents=True)
            settings = SimpleNamespace(jobs_dir=jobs_dir)
            names = ["../../outside", str(root / "outside"), "../jobs-backup"]
            if os.name == "nt":
                names.append("..\\..\\outside")
            with (
                patch.object(api, "load_job") as load,
                patch.object(api, "ensure_job_capacity") as capacity,
                patch.object(api, "write_json_atomic") as write,
                patch.object(api, "process_job") as process,
            ):
                for name in names:
                    with self.subTest(name=name):
                        with self.assertRaisesRegex(RuntimeError, "queued job not found"):
                            api._execute_queue_item(settings, {"job_name": name})
                load.assert_not_called()
                capacity.assert_not_called()
                write.assert_not_called()
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
