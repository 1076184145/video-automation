from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from video_automation.api import _handler_class
from video_automation.config import Settings
from video_automation.routing import CORE_ROUTER


class RouterTests(unittest.TestCase):
    def test_static_and_job_file_routes_capture_nested_paths(self) -> None:
        static = CORE_ROUTER.resolve("GET", "/static/css/app.css")
        job_file = CORE_ROUTER.resolve("GET", "/jobs/job%20one/files/covers/a.jpg")

        self.assertEqual(static.endpoint, "static_file")
        self.assertEqual(static.params["asset_path"], "css/app.css")
        self.assertEqual(job_file.endpoint, "job_file")
        self.assertEqual(job_file.params, {"job_name": "job one", "filename": "covers/a.jpg"})

    def test_job_actions_resolve_without_manual_path_splitting(self) -> None:
        matched = CORE_ROUTER.resolve("POST", "/jobs/demo/subtitles/render-translated")
        cancel = CORE_ROUTER.resolve("POST", "/jobs/demo/cancel")
        self.assertEqual(matched.endpoint, "render_translated_subtitles")
        self.assertEqual(matched.params["job_name"], "demo")
        self.assertEqual(cancel.endpoint, "cancel_job")

    def test_method_and_segment_count_are_enforced(self) -> None:
        self.assertIsNone(CORE_ROUTER.resolve("GET", "/jobs/demo/approve"))
        self.assertIsNone(CORE_ROUTER.resolve("DELETE", "/jobs/demo/extra"))

    def test_every_declared_endpoint_has_a_composed_route_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(
                Settings.load(),
                root=root,
                jobs_dir=root / "processing" / "jobs",
                logs_dir=root / "logs",
                api_host="127.0.0.1",
                api_port=0,
            )
            handler = _handler_class(settings)

        missing = [
            route.endpoint
            for route in CORE_ROUTER.routes
            if not callable(getattr(handler, f"_route_{route.endpoint}", None))
        ]
        self.assertEqual(missing, [])
        self.assertEqual(
            handler._route_update_settings.__module__,
            "video_automation.api_routes_system",
        )
        self.assertEqual(
            handler._route_update_job_cuts.__module__,
            "video_automation.api_routes_jobs",
        )
        self.assertEqual(
            handler._route_generate_job_covers.__module__,
            "video_automation.api_routes_enhancements",
        )

    def test_route_context_replaces_settings_and_origins_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(
                Settings.load(),
                root=root,
                jobs_dir=root / "processing" / "jobs",
                logs_dir=root / "logs",
                api_host="127.0.0.1",
                api_port=8765,
                api_allowed_origins=(),
            )
            handler = _handler_class(settings)
            updated = replace(
                settings,
                api_allowed_origins=("https://review.example",),
            )

            self.assertFalse(
                handler.api_context.origin_is_allowed("https://review.example")
            )
            handler.api_context.replace_settings(updated)

        self.assertIs(handler.api_context.settings, updated)
        self.assertTrue(handler.api_context.origin_is_allowed("https://review.example"))

    def test_api_entrypoint_stays_a_thin_composition_root(self) -> None:
        api_path = Path(__file__).resolve().parents[1] / "video_automation" / "api.py"
        source = api_path.read_text(encoding="utf-8")

        self.assertLessEqual(len(source.splitlines()), 600)
        self.assertNotIn("def do_GET", source)
        self.assertNotIn("def do_POST", source)
        self.assertNotIn("def _route_", source)


if __name__ == "__main__":
    unittest.main()
