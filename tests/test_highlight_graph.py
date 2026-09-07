from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tools.compare_highlight_runs import summarize_run
from video_automation import config, highlight_checker, highlight_graph, llm_evaluator
from video_automation.highlight_graph import HighlightGraph, effective_graph_concurrency, merge_candidate_pool, select_reviewed_candidates
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.llm_evaluator import review_candidate_limit
from video_automation.provider_errors import ProviderRequestError
from video_automation.runtime_config import apply_runtime_settings_snapshot, snapshot_runtime_settings
from video_automation.task_queue import QueueControlRequested
from video_automation.unattended_highlights import UnattendedHighlights


def sentences(count: int = 8, duration: float = 10.) -> list[dict]:
    return [dict(id=i + 1, start=i * duration, end=(i + 1) * duration, text=f"Complete thought {i + 1}.") for i in range(count)]


def candidate(start: int = 1, end: int = 4, score: int = 90, title: str = "A complete idea") -> dict:
    return dict(start_id=start, end_id=end, hook_score=score, title=title, reason="Setup, explanation and payoff.")


def model_response(*args, **kwargs) -> dict:
    source = json.loads(kwargs["user"])["sentences"]
    return {"clips": [candidate(source[0]["id"], source[3]["id"])]}


def review_response(*args, **kwargs) -> dict:
    source = json.loads(kwargs["user"])["candidates"]
    return {"results": [dict(candidate_id=c["candidate_id"], status="pass", reason="A self-contained explanation.",
                             evidence_ids=[c["sentences"][0]["id"]]) for c in source]}


class HighlightGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict(os.environ, {}, clear=True), patch.object(config, "PROJECT_ROOT", self.root), patch.object(config, "_cached_env_file", return_value={}):
            self.settings = config.Settings.load()

    def test_all_focuses_cover_every_long_video_window_with_real_ids(self) -> None:
        source = sentences(120, 15.)
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response) as call:
            report = HighlightGraph(self.settings).generate(source)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(call.call_count, 6)
        self.assertEqual({c.kwargs["schema_name"] for c in call.call_args_list}, {"highlight_hook", "highlight_emotion", "highlight_conflict"})
        for name in ("highlight_hook", "highlight_emotion", "highlight_conflict"):
            prompts = [json.loads(c.kwargs["user"])["sentences"] for c in call.call_args_list if c.kwargs["schema_name"] == name]
            self.assertEqual({s["id"] for batch in prompts for s in batch}, set(range(1, 121)))
            self.assertTrue(any({79, 80, 81, 82, 83} <= {s["id"] for s in batch} for batch in prompts))
        self.assertEqual(len(report["candidates"]), 2)
        self.assertTrue(all(len(c["sources"]) == 3 for c in report["candidates"]))
        for c in call.call_args_list:
            self.assertIn("NEVER output or invent timestamps", c.kwargs["system"])
            self.assertEqual(c.kwargs["schema"]["properties"]["clips"]["maxItems"], 8)

    def test_cloud_parallelism_is_bounded_and_local_is_serial(self) -> None:
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = peak = calls = 0

        def request(*args, **kwargs):
            nonlocal active, peak, calls
            with lock:
                active += 1
                peak = max(peak, active)
                calls += 1
                number = calls
            if number <= 2:
                barrier.wait(timeout=3)
            response = model_response(*args, **kwargs)
            with lock:
                active -= 1
            return response

        with patch.object(highlight_graph, "request_highlight_json", side_effect=request):
            report = HighlightGraph(replace(self.settings, highlight_graph_concurrency=2)).generate(sentences())
        self.assertEqual(peak, 2)
        self.assertEqual(report["status"], "complete")
        for settings in (replace(self.settings, llm_provider="local"),
                         replace(self.settings, llm_fallback_provider="local"),
                         replace(self.settings, llm_openai_base_url="http://127.0.0.1:11434/v1"),
                         replace(self.settings, llm_openai_base_url="http://[::1]:11434/v1")):
            self.assertEqual(effective_graph_concurrency(settings), 1)
        self.assertEqual(effective_graph_concurrency(replace(self.settings, highlight_graph_concurrency=999)), 3)

    def test_partial_failure_keeps_provenance_and_retries_only_failed_node(self) -> None:
        secret_failure = ProviderRequestError("test", "analysis", "credentials_invalid", "SECRET_ECHO", http_status=401)

        def mixed(*args, **kwargs):
            if kwargs["schema_name"] == "highlight_hook":
                raise secret_failure
            return model_response(*args, **kwargs)

        with patch.object(highlight_graph, "request_highlight_json", side_effect=mixed):
            partial = HighlightGraph(self.settings).generate(sentences())
        self.assertEqual((partial["status"], partial["failed_nodes"]), ("partial", 1))
        self.assertNotIn("SECRET_ECHO", json.dumps(partial))
        self.assertEqual(len(partial["candidates"][0]["sources"]), 2)
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response) as call:
            complete = HighlightGraph(self.settings).generate(sentences(), previous=partial)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs["schema_name"], "highlight_hook")
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(len(complete["candidates"][0]["sources"]), 3)

    def test_failed_graph_is_not_a_successful_empty_selection(self) -> None:
        updates = []
        with patch.object(highlight_graph, "request_highlight_json", side_effect=RuntimeError("all fail")), self.assertRaisesRegex(RuntimeError, "All highlight analyzers failed"):
            HighlightGraph(self.settings).generate(sentences(), on_update=lambda r: updates.append(r))
        self.assertEqual(updates[-1]["status"], "failed")
        self.assertEqual(updates[-1]["error_code"], "all_analyzers_failed")
        with patch.object(highlight_graph, "request_highlight_json", return_value={"clips": []}):
            empty = HighlightGraph(self.settings).generate(sentences())
        self.assertEqual((empty["status"], empty["candidates"]), ("complete", []))

    def test_preflight_caps_windows_and_prompt_size_before_requests(self) -> None:
        for source in ([{**sentences()[0], "text": "X" * 30000}], sentences(129, 1200.)):
            with patch.object(highlight_graph, "request_highlight_json") as call, self.assertRaises(ValueError):
                HighlightGraph(self.settings).generate(source)
            call.assert_not_called()

    def test_out_of_window_ids_and_invalid_scores_never_enter_pool(self) -> None:
        payload = {"clips": [candidate(999, 1001), candidate(score=True), candidate(end=8), candidate()]}
        with patch.object(highlight_graph, "request_highlight_json", return_value=payload):
            report = HighlightGraph(self.settings).generate(sentences())
        self.assertEqual(report["rejected_count"], 9)
        self.assertEqual(len(report["candidates"]), 1)

    def test_cancel_stops_scheduling_and_records_control_status(self) -> None:
        callback = Mock(return_value=None)
        updates = []

        def stop_after_first(*args, **kwargs):
            callback.return_value = "paused"
            return model_response(*args, **kwargs)

        with patch.object(highlight_graph, "request_highlight_json", side_effect=stop_after_first) as call, self.assertRaises(QueueControlRequested):
            HighlightGraph(replace(self.settings, llm_provider="local"), control_callback=callback).generate(sentences(), on_update=lambda r: updates.append(r))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(updates[-1]["status"], "paused")
        self.assertFalse(any(n["status"] in {"running", "pending", "failed"} for n in updates[-1]["nodes"]))

    def test_exact_merge_and_final_nms_do_not_let_rejected_clip_suppress_valid_one(self) -> None:
        high = {**candidate(score=99, title="Unsupported title"), "start": 0., "end": 40., "sources": ["hook"]}
        good = {**candidate(score=80), "start": 0., "end": 40., "sources": ["emotion"]}
        pool, omitted = merge_candidate_pool([high, good, {**good, "sources": ["conflict"]}], 36)
        self.assertEqual((len(pool), omitted), (2, 0))
        self.assertEqual(pool[1]["sources"], ["conflict", "emotion"])
        selected, decisions = select_reviewed_candidates(pool, {"candidate-2"}, 1)
        self.assertEqual(selected, [pool[1]])
        self.assertEqual([d["status"] for d in decisions], ["review_rejected", "selected"])

    def test_strict_iou_and_count_limit_have_explicit_decisions(self) -> None:
        items = [{**candidate(), "start": 0., "end": 60.},
                 {**candidate(score=80), "start": 20., "end": 80.},
                 {**candidate(score=70), "start": 100., "end": 140.},
                 {**candidate(score=60), "start": 1., "end": 61.}]
        selected, decisions = select_reviewed_candidates(items, {f"candidate-{i + 1}" for i in range(4)}, 2)
        self.assertEqual(selected, items[:2])  # IoU exactly .5 survives.
        self.assertEqual([d["status"] for d in decisions], ["selected", "selected", "limit", "overlap"])

    def test_config_loads_clamps_and_snapshots_new_options(self) -> None:
        self.assertFalse(self.settings.highlight_graph_enabled)
        with patch.dict(os.environ, {"HIGHLIGHT_GRAPH_ENABLED": "true", "HIGHLIGHT_GRAPH_CONCURRENCY": "99", "HIGHLIGHT_REVIEW_MAX_CANDIDATES": "999"}, clear=True), patch.object(config, "PROJECT_ROOT", self.root), patch.object(config, "_cached_env_file", return_value={}):
            settings = config.Settings.load()
        self.assertEqual((settings.highlight_graph_enabled, settings.highlight_graph_concurrency, settings.highlight_review_max_candidates), (True, 3, 150))
        restored = apply_runtime_settings_snapshot(self.settings, snapshot_runtime_settings(settings))
        self.assertEqual(restored.highlight_review_max_candidates, 150)
        self.assertTrue(restored.highlight_graph_enabled)
        self.assertEqual(review_candidate_limit(replace(self.settings, highlight_max_clips=50, highlight_review_max_candidates=1)), 50)

    def pipeline(self, *, review: bool = True) -> UnattendedHighlights:
        source = self.root / "source.mp4"
        source.touch()
        with wave.open(str(self.root / "audio.wav"), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 8000 * 85)
        write_json_atomic(self.root / "transcript.json", {"segments": sentences()})
        write_json_atomic(self.root / "silence.json", {"silences": []})
        write_json_atomic(self.root / "manifest.json", {"duration_seconds": 85.})
        return UnattendedHighlights(replace(self.settings, highlight_graph_enabled=True, highlight_llm_checker_enabled=review), self.root, source)

    def test_pipeline_graph_review_cache_and_config_invalidation(self) -> None:
        pipeline = self.pipeline()
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response), patch.object(highlight_checker, "request_highlight_json", side_effect=review_response):
            data = pipeline.evaluate()
        self.assertEqual((data["generation_status"], data["review_status"]), ("complete", "complete"))
        with patch.object(highlight_graph, "request_highlight_json") as generator, patch.object(highlight_checker, "request_highlight_json") as reviewer:
            self.assertEqual(pipeline.evaluate(), data)
        generator.assert_not_called()
        reviewer.assert_not_called()
        pipeline.settings = replace(pipeline.settings, highlight_graph_concurrency=3)
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response) as generator, patch.object(highlight_checker, "request_highlight_json", side_effect=review_response):
            pipeline.evaluate()
        self.assertEqual(generator.call_count, 3)
        write_json_atomic(pipeline.directory / "generation.json", {})
        with self.assertRaisesRegex(ValueError, "generation is incomplete"):
            pipeline.plan()

    def test_failed_generation_blocks_and_forced_retry_does_not_reuse_approval(self) -> None:
        pipeline = self.pipeline(review=False)
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response):
            pipeline.evaluate()
        pipeline.force = True
        with patch.object(highlight_graph, "request_highlight_json", side_effect=RuntimeError("failed")), self.assertRaises(RuntimeError):
            pipeline.evaluate()
        with self.assertRaisesRegex(ValueError, "generation is incomplete"):
            pipeline.plan()
        self.assertEqual(read_json_file(pipeline.directory / "candidates.json")["candidates"], [])

    def test_partial_generation_remains_partial_even_if_all_selected_clips_render(self) -> None:
        pipeline = self.pipeline(review=False)

        def mixed(*args, **kwargs):
            if kwargs["schema_name"] == "highlight_hook":
                raise RuntimeError("failed route")
            return model_response(*args, **kwargs)

        with patch.object(highlight_graph, "request_highlight_json", side_effect=mixed):
            pipeline.evaluate()
        pipeline.plan()
        with patch("video_automation.unattended_highlights.generate_vertical_crop_plan"), patch("video_automation.unattended_highlights.render_highlight_edit"):
            report = pipeline.render()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["clips"][0]["status"], "done")
        self.assertEqual(report["generation_file"], "auto_clips/generation.json")
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response) as call:
            pipeline.evaluate()
        self.assertEqual(call.call_count, 1)

    def test_partial_empty_generation_is_not_no_qualifying_clips(self) -> None:
        pipeline = self.pipeline(review=False)

        def mixed(*args, **kwargs):
            if kwargs["schema_name"] != "highlight_hook":
                raise RuntimeError("failed route")
            return {"clips": []}

        with patch.object(highlight_graph, "request_highlight_json", side_effect=mixed):
            pipeline.evaluate()
        pipeline.plan()
        self.assertEqual(pipeline.render()["status"], "partial")

    def test_single_route_also_defers_nms_until_after_review(self) -> None:
        pipeline = self.pipeline()
        pipeline.settings = replace(pipeline.settings, highlight_graph_enabled=False, highlight_max_clips=1)

        def reject_highest(*args, **kwargs):
            payload = review_response(*args, **kwargs)
            payload["results"][0]["status"] = "reject"
            return payload

        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate(score=99, title="Unsupported"), candidate(score=80)]}), patch.object(highlight_checker, "request_highlight_json", side_effect=reject_highest):
            data = pipeline.evaluate()
        self.assertEqual(len(data["raw_candidates"]), 2)
        self.assertEqual(data["candidates"][0]["hook_score"], 80)

    def test_offline_comparison_uses_current_reports_without_exposing_text(self) -> None:
        pipeline = self.pipeline()
        with patch.object(highlight_graph, "request_highlight_json", side_effect=model_response), patch.object(highlight_checker, "request_highlight_json", side_effect=review_response):
            pipeline.evaluate()
        summary = summarize_run(pipeline.directory)
        self.assertEqual((summary["selected_count"], summary["analysis_nodes_completed"], summary["review_passed_count"]), (1, 3, 1))
        self.assertIsNone(summary["measured_cost"])
        self.assertIsNone(summary["measured_editorial_quality"])
        self.assertNotIn("Complete thought", json.dumps(summary))
        write_json_atomic(pipeline.directory / "index.json", {"fingerprint": "stale", "status": "done", "clips": [{"status": "done"}]})
        self.assertEqual(summarize_run(pipeline.directory)["rendered_count"], 0)


if __name__ == "__main__":
    unittest.main()
