from __future__ import annotations

import unittest

from video_automation.pipeline_scheduler import expand_stage_selection


class RecipePipelineTests(unittest.TestCase):
    def test_render_final_recipe_adds_dependencies_but_skips_unselected_analysis(self) -> None:
        selected = expand_stage_selection(["render_final"])

        self.assertTrue(
            {
                "probe",
                "extract_audio",
                "transcribe",
                "plan_cuts",
                "refine_cuts",
                "style_subtitles",
                "plan_render",
                "render_final",
            }.issubset(selected)
        )
        self.assertNotIn("detect_silence", selected)
        self.assertNotIn("detect_freeze", selected)

    def test_empty_selection_keeps_legacy_pipeline_behavior(self) -> None:
        self.assertIsNone(expand_stage_selection(None))
        self.assertIsNone(expand_stage_selection([]))

    def test_unknown_recipe_stage_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown pipeline stage"):
            expand_stage_selection(["teleport_video"])


if __name__ == "__main__":
    unittest.main()
