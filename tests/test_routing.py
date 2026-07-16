from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
