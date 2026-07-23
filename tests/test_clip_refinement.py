from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.clip_refinement import refine_clip_boundaries
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.task_queue import QueueControlRequested


def _settings(**updates):
    values = {
        "cut_min_clip_seconds": 2.0,
        "clip_refinement_max_attempts": 3,
        "clip_refinement_time_budget_seconds": 5.0,
        "clip_refinement_boundary_tolerance_seconds": 0.05,
        "clip_refinement_max_shift_seconds": 0.75,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _write_inputs(
    job_dir: Path,
    *,
    start: float = 1.2,
    end: float = 8.0,
    word_start: float = 1.0,
    word_end: float = 1.5,
    invalid_segments: list[dict] | None = None,
) -> None:
    job_dir.mkdir(parents=True)
    write_json_atomic(
        job_dir / "cuts.json",
        {
            "source": "source.mp4",
            "status": "needs_review",
            "duration_seconds": 10.0,
            "invalid_segments": invalid_segments or [],
            "highlight_signals": {
                "scene_count": 0,
                "scenes": [],
            },
            "clips": [
                {
                    "start": start,
                    "end": end,
                    "duration": round(end - start, 3),
                    "keep": True,
                    "reason": "test",
                }
            ],
            "notes": [],
        },
    )
    write_json_atomic(
        job_dir / "transcript.json",
        {
            "segments": [
                {
                    "start": word_start,
                    "end": word_end,
                    "text": "测试",
                    "words": [
                        {
                            "start": word_start,
                            "end": word_end,
                            "word": "测试",
                        }
                    ],
                }
            ]
        },
    )


class ClipRefinementTests(unittest.TestCase):
    def test_repairs_word_boundary_and_invalidates_only_downstream_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)
            (job_dir / "final.mp4").write_bytes(b"stale")
            (job_dir / "subtitles_clipped.ass").write_text("stale", encoding="utf-8")
            (job_dir / "subtitles.ass").write_text("source", encoding="utf-8")

            state = refine_clip_boundaries(_settings(), job_dir, force=True)

            cuts = read_json_file(job_dir / "cuts.json")
            self.assertEqual(state["status"], "accepted")
            self.assertTrue(state["changed"])
            self.assertEqual(state["attempt_count"], 2)
            self.assertEqual(cuts["clips"][0]["start"], 1.0)
            self.assertEqual(cuts["refinement"]["initial_score"], 95.0)
            self.assertEqual(cuts["refinement"]["final_score"], 100.0)
            self.assertFalse((job_dir / "final.mp4").exists())
            self.assertFalse((job_dir / "subtitles_clipped.ass").exists())
            self.assertTrue((job_dir / "subtitles.ass").exists())

    def test_clean_windows_are_accepted_without_invalidating_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir, start=0.0, end=10.0)
            (job_dir / "final.mp4").write_bytes(b"keep")

            state = refine_clip_boundaries(_settings(), job_dir, force=True)

            self.assertEqual(state["status"], "accepted")
            self.assertFalse(state["changed"])
            self.assertEqual(state["attempt_count"], 1)
            self.assertTrue((job_dir / "final.mp4").exists())

    def test_existing_merged_invalid_segment_does_not_fail_refinement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(
                job_dir,
                start=0.0,
                end=10.0,
                invalid_segments=[
                    {
                        "start": 4.0,
                        "end": 4.5,
                        "duration": 0.5,
                        "reason": "short_silence_merged_by_planner",
                    }
                ],
            )

            state = refine_clip_boundaries(_settings(), job_dir, force=True)

            self.assertEqual(state["status"], "accepted")
            self.assertFalse(state["changed"])

    def test_boundary_repair_trims_inward_instead_of_reintroducing_invalid_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(
                job_dir,
                start=1.2,
                end=8.0,
                word_start=1.0,
                word_end=1.5,
                invalid_segments=[
                    {
                        "start": 0.9,
                        "end": 1.1,
                        "duration": 0.2,
                        "reason": "silence",
                    }
                ],
            )

            state = refine_clip_boundaries(_settings(), job_dir, force=True)

            cuts = read_json_file(job_dir / "cuts.json")
            self.assertEqual(state["status"], "accepted")
            self.assertEqual(cuts["clips"][0]["start"], 1.5)
            self.assertGreaterEqual(cuts["clips"][0]["start"], 1.1)

    def test_unsafe_large_shift_requires_review_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(
                job_dir,
                start=1.5,
                end=8.0,
                word_start=0.0,
                word_end=2.0,
            )
            (job_dir / "final.mp4").write_bytes(b"keep")

            state = refine_clip_boundaries(
                _settings(clip_refinement_max_shift_seconds=0.2),
                job_dir,
                force=True,
            )

            cuts = read_json_file(job_dir / "cuts.json")
            self.assertEqual(state["status"], "needs_review")
            self.assertFalse(state["changed"])
            self.assertEqual(cuts["clips"][0]["start"], 1.5)
            self.assertTrue((job_dir / "final.mp4").exists())

    def test_cancel_is_checked_before_each_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)

            with self.assertRaises(QueueControlRequested):
                refine_clip_boundaries(
                    _settings(),
                    job_dir,
                    force=True,
                    control_callback=lambda: "canceled",
                )

            self.assertFalse((job_dir / "clip_refinement.json").exists())
            self.assertEqual(read_json_file(job_dir / "cuts.json")["clips"][0]["start"], 1.2)

    def test_running_state_resumes_after_cooperative_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)
            calls = 0

            def cancel_after_first_attempt() -> str | None:
                nonlocal calls
                calls += 1
                return None if calls == 1 else "canceled"

            with self.assertRaises(QueueControlRequested):
                refine_clip_boundaries(
                    _settings(),
                    job_dir,
                    force=False,
                    control_callback=cancel_after_first_attempt,
                )

            running = read_json_file(job_dir / "clip_refinement.json")
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["attempt_count"], 1)

            resumed = refine_clip_boundaries(_settings(), job_dir, force=False)

            self.assertEqual(resumed["status"], "accepted")
            self.assertEqual(resumed["attempt_count"], 2)
            self.assertEqual(read_json_file(job_dir / "cuts.json")["clips"][0]["start"], 1.0)

    def test_incomplete_recovery_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)
            write_json_atomic(
                job_dir / "clip_refinement.json",
                {
                    "schema_version": 1,
                    "status": "accepted",
                    "max_attempts": 3,
                    "original_windows": [],
                    "current_windows": [],
                    "attempts": [],
                },
            )

            state = refine_clip_boundaries(_settings(), job_dir, force=False)

            self.assertEqual(state["status"], "accepted")
            self.assertEqual(state["attempt_count"], 2)

    def test_terminal_cache_is_invalidated_when_policy_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir, start=0.0, end=10.0)

            accepted = refine_clip_boundaries(_settings(), job_dir, force=False)
            reevaluated = refine_clip_boundaries(
                _settings(cut_min_clip_seconds=12.0),
                job_dir,
                force=False,
            )

            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(reevaluated["status"], "needs_review")
            self.assertEqual(reevaluated["attempt_count"], 1)

    def test_changed_terminal_state_is_reused_without_losing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)

            first = refine_clip_boundaries(_settings(), job_dir, force=False)
            cached = refine_clip_boundaries(_settings(), job_dir, force=False)

            self.assertTrue(first["changed"])
            self.assertEqual(cached, first)
            self.assertEqual(cached["original_windows"][0]["start"], 1.2)
            self.assertEqual(cached["attempt_count"], 2)

    def test_time_budget_stops_before_an_unbounded_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)

            with patch(
                "video_automation.clip_refinement.time.monotonic",
                side_effect=[0.0, 1.0],
            ):
                state = refine_clip_boundaries(
                    _settings(clip_refinement_time_budget_seconds=0.05),
                    job_dir,
                    force=True,
                )

            self.assertEqual(state["status"], "needs_review")
            self.assertEqual(state["stop_reason"], "time_budget_exhausted")
            self.assertEqual(state["attempt_count"], 0)

    def test_same_output_fingerprint_stops_no_progress_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job"
            _write_inputs(job_dir)

            with patch(
                "video_automation.clip_refinement.apply_refinement_action",
                side_effect=lambda windows, _action, **_kwargs: windows,
            ):
                state = refine_clip_boundaries(_settings(), job_dir, force=True)

            self.assertEqual(state["status"], "needs_review")
            self.assertEqual(state["stop_reason"], "no_progress_or_oscillation")
            self.assertEqual(state["attempt_count"], 1)
            self.assertFalse(state["changed"])


if __name__ == "__main__":
    unittest.main()
