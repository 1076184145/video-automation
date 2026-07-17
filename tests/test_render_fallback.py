from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.config import Settings
from video_automation import render


class RenderFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        render._NVENC_PROBE_CACHE.clear()

    def test_nvenc_probe_runs_a_real_one_frame_encode(self) -> None:
        result = SimpleNamespace(returncode=1, stderr="incompatible client key")
        with patch.object(render, "run_ffmpeg_with_progress", return_value=result) as runner:
            probe = render.probe_nvenc_encoder("ffmpeg", force=True)

        self.assertFalse(probe["available"])
        command = runner.call_args.args[0]
        self.assertIn("h264_nvenc", command)
        self.assertIn("color=c=black:s=640x360:r=1", command)

    def test_unavailable_nvenc_falls_back_to_libx264(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                Settings.load(),
                root=Path(temp_dir),
                render_video_encoder="h264_nvenc",
            )
            with patch.object(
                render,
                "probe_nvenc_encoder",
                return_value={"available": False, "detail": "incompatible client key"},
            ):
                effective, reason = render.effective_render_settings(settings)

        self.assertEqual(effective.render_video_encoder, "libx264")
        self.assertEqual(reason, "incompatible client key")

    def test_failed_final_output_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "final.mp4"
            path.write_bytes(b"incomplete")
            render._remove_failed_output(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
