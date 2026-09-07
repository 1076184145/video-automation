from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from video_automation import config, highlight_checker, llm_evaluator
from video_automation.highlight_checker import LLMHighlightChecker, checker_schema
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.provider_errors import ProviderRequestError
from video_automation.runtime_config import apply_runtime_settings_snapshot, snapshot_runtime_settings
from video_automation.task_queue import QueueControlRequested
from video_automation.unattended_highlights import UnattendedHighlights


def sentences(count: int = 8) -> list[dict]:
    return [dict(id=i + 1, start=i * 10., end=(i + 1) * 10., text=f"Complete source sentence {i + 1}.")
            for i in range(count)]


def candidate(start_id: int = 1, end_id: int = 4) -> dict:
    return dict(title="An informative explanation", start_id=start_id, end_id=end_id,
                start=(start_id - 1) * 10., end=end_id * 10., hook_score=90, reason="GENERATOR_BIAS")


def result(ident: str = "candidate-1", status: str = "pass", evidence: int = 1) -> dict:
    return dict(candidate_id=ident, status=status, reason="The selected text supplies setup and conclusion.", evidence_ids=[evidence])


def approve_request(*args, **kwargs) -> dict:
    items = json.loads(kwargs["user"])["candidates"]
    return {"results": [result(item["candidate_id"], evidence=item["sentences"][0]["id"]) for item in items]}


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with patch.dict(os.environ, {}, clear=True), patch.object(config, "PROJECT_ROOT", self.root), patch.object(config, "_cached_env_file", return_value={}):
            self.settings = config.Settings.load()

    def test_full_text_context_and_original_inputs_are_preserved(self) -> None:
        source = sentences()
        source[4]["text"] = "Long conclusion: " + "details " * 100 + "THE_ACTUAL_PAYOFF"
        selected = candidate(2, 5)
        before = copy.deepcopy((selected, source))
        with patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request) as call:
            report = LLMHighlightChecker(self.settings).review([selected], source)
        prompt = json.loads(call.call_args.kwargs["user"])["candidates"][0]
        self.assertEqual([s["id"] for s in prompt["sentences"]], [2, 3, 4, 5])
        self.assertEqual(prompt["context_before"], [source[0]])
        self.assertEqual(prompt["context_after"], [source[5]])
        self.assertTrue(prompt["sentences"][-1]["text"].endswith("THE_ACTUAL_PAYOFF"))
        self.assertNotIn("GENERATOR_BIAS", call.call_args.kwargs["user"])
        self.assertNotIn("hook_score", prompt)
        self.assertEqual(report["passed_count"], 1)
        self.assertEqual((selected, source), before)
        self.assertEqual(report["results"][0]["sentences"], source[1:5])

    def test_verdicts_use_candidate_ids_not_response_order(self) -> None:
        reply = {"results": [result("candidate-2", "reject", 5), result()]}
        with patch.object(highlight_checker, "request_highlight_json", return_value=reply):
            report = LLMHighlightChecker(self.settings).review([candidate(), candidate(5, 8)], sentences())
        self.assertEqual([r["status"] for r in report["results"]], ["pass", "reject"])
        self.assertEqual((report["passed_count"], report["rejected_count"]), (1, 1))

    def test_natural_pauses_do_not_fail_a_speech_density_threshold(self) -> None:
        source = [{**s, "end": s["end"] - 5} for s in sentences(4)]
        with patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request):
            report = LLMHighlightChecker(self.settings).review([{**candidate(), "end": 35.}], source)
        self.assertEqual(report["results"][0]["status"], "pass")

    def test_invalid_candidates_fail_rules_without_model_calls(self) -> None:
        bad = [{**candidate(), **override} for override in (
            {"start_id": True}, {"end_id": 999}, {"start": float("nan")}, {"hook_score": 101},
            {"title": " "}, {"end": 75.}, {"start_id": 1, "end_id": 8, "end": 80.},
        )]
        with patch.object(highlight_checker, "request_highlight_json") as call:
            report = LLMHighlightChecker(self.settings).review(bad, sentences())
        call.assert_not_called()
        self.assertEqual(report["rejected_count"], len(bad))
        self.assertTrue(all(r["stage"] == "rules" for r in report["results"]))

    def test_malformed_verdicts_fail_closed(self) -> None:
        invalid = [[], [result(), result()], [result("unknown")], [{**result(), "status": "maybe"}],
                   [{**result(), "reason": " "}], [{**result(), "evidence_ids": [True]}],
                   [{**result(), "evidence_ids": [5]}], [{**result(), "evidence_ids": []}],
                   [{**result(), "evidence_ids": [1, 1]}], [{**result(), "start_id": 1}]]
        for results in invalid:
            with self.subTest(results=results):
                updates = []
                with patch.object(highlight_checker, "request_highlight_json", return_value={"results": results}), self.assertRaises(ProviderRequestError):
                    LLMHighlightChecker(self.settings).review([candidate()], sentences(), on_update=lambda r: updates.append(copy.deepcopy(r)))
                self.assertEqual(updates[-1]["status"], "failed")
                self.assertEqual(updates[-1]["error_code"], "response_invalid")
                self.assertEqual(updates[-1]["results"][0]["status"], "pending")

    def test_strict_schema_requires_all_fields_and_closed_objects(self) -> None:
        def validate(schema: dict) -> None:
            if schema.get("type") == "object":
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                for value in schema["properties"].values():
                    validate(value)
            if schema.get("type") == "array":
                validate(schema["items"])
        validate(checker_schema())

    def test_oversized_candidate_preflights_all_batches_without_truncation(self) -> None:
        source = sentences()
        source[-1]["text"] = "Full ending " * 2000
        updates = []
        with patch.object(highlight_checker, "request_highlight_json") as call, self.assertRaisesRegex(ValueError, "Full candidate/context exceeds"):
            LLMHighlightChecker(self.settings).review([candidate(), candidate(5, 8)], source, on_update=lambda r: updates.append(copy.deepcopy(r)))
        call.assert_not_called()
        self.assertEqual(updates[-1]["status"], "failed")
        self.assertEqual(updates[-1]["results"][-1]["sentences"][-1]["text"], source[-1]["text"])

    def test_completed_batches_resume_and_provider_errors_are_redacted(self) -> None:
        selections = [candidate(i, i + 3) for i in range(1, 25, 4)]
        failure = ProviderRequestError("test", "review", "credentials_invalid", "SECRET_TOKEN_AND_TRANSCRIPT", http_status=401)
        updates = []
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise failure
            return approve_request(*args, **kwargs)

        with patch.object(highlight_checker, "request_highlight_json", side_effect=fail_second), self.assertRaises(ProviderRequestError):
            LLMHighlightChecker(self.settings).review(selections, sentences(24), on_update=lambda r: updates.append(copy.deepcopy(r)))
        checkpoint = updates[-1]
        self.assertEqual(checkpoint["status"], "failed")
        self.assertEqual([r["status"] for r in checkpoint["results"]], ["pass"] * 4 + ["pending"] * 2)
        self.assertNotIn("SECRET_TOKEN", json.dumps(checkpoint))
        with patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request) as call:
            report = LLMHighlightChecker(self.settings).review(selections, sentences(24), previous=checkpoint)
        self.assertEqual(call.call_count, 1)
        self.assertEqual([r["candidate_id"] for r in json.loads(call.call_args.kwargs["user"])["candidates"]], ["candidate-5", "candidate-6"])
        self.assertEqual(report["passed_count"], 6)

    def test_pause_is_recorded_and_not_swallowed(self) -> None:
        updates = []
        with patch.object(highlight_checker, "request_highlight_json", side_effect=QueueControlRequested("paused")), self.assertRaises(QueueControlRequested):
            LLMHighlightChecker(self.settings).review([candidate()], sentences(), on_update=lambda r: updates.append(copy.deepcopy(r)))
        self.assertEqual(updates[-1]["status"], "paused")

    def test_shared_transport_retries_and_checks_cancel_after_response(self) -> None:
        transient = ProviderRequestError("test", "request", "rate_limited", "retry", http_status=429)
        with patch.object(llm_evaluator, "call_structured_llm", side_effect=[transient, {"results": [result()]}]) as call, patch.object(llm_evaluator.time, "sleep"):
            report = LLMHighlightChecker(self.settings).review([candidate()], sentences())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(report["passed_count"], 1)
        callback = Mock(return_value=None)

        def canceled_response(*args, **kwargs):
            callback.return_value = "canceled"
            return {"results": [result()]}

        with patch.object(llm_evaluator, "call_structured_llm", side_effect=canceled_response), self.assertRaises(QueueControlRequested):
            LLMHighlightChecker(self.settings, control_callback=callback).review([candidate()], sentences())

    def fixture_pipeline(self, enabled: bool = True) -> UnattendedHighlights:
        source = self.root / "source.mp4"
        source.touch()
        (self.root / "audio.wav").write_bytes(b"synthetic fixture; no acoustic processing")
        write_json_atomic(self.root / "transcript.json", {"segments": sentences()})
        write_json_atomic(self.root / "silence.json", {"silences": []})
        write_json_atomic(self.root / "manifest.json", {"duration_seconds": 80.})
        return UnattendedHighlights(replace(self.settings, highlight_llm_checker_enabled=enabled), self.root, source)

    def test_switch_loads_and_roundtrips_queue_snapshot(self) -> None:
        self.assertFalse(self.settings.highlight_llm_checker_enabled)
        with patch.dict(os.environ, {"HIGHLIGHT_LLM_CHECKER_ENABLED": "true"}, clear=True), patch.object(config, "PROJECT_ROOT", self.root), patch.object(config, "_cached_env_file", return_value={}):
            enabled = config.Settings.load()
        self.assertTrue(enabled.highlight_llm_checker_enabled)
        restored = apply_runtime_settings_snapshot(self.settings, snapshot_runtime_settings(enabled))
        self.assertTrue(restored.highlight_llm_checker_enabled)
        self.assertNotEqual(snapshot_runtime_settings(enabled)["revision"], snapshot_runtime_settings(self.settings)["revision"])

    def test_disabled_mode_never_calls_checker_and_preserves_selection(self) -> None:
        pipeline = self.fixture_pipeline(enabled=False)
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json") as checker:
            data = pipeline.evaluate()
        checker.assert_not_called()
        self.assertEqual(data["review_status"], "disabled")
        self.assertEqual(data["candidates"][0]["title"], candidate()["title"])
        self.assertFalse((pipeline.directory / "review.json").exists())

    def test_pipeline_caches_review_and_rejects_stale_or_missing_reports(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request):
            original = pipeline.evaluate()
        with patch.object(llm_evaluator, "call_structured_llm") as generator, patch.object(highlight_checker, "request_highlight_json") as reviewer:
            self.assertEqual(pipeline.evaluate(), original)
        generator.assert_not_called()
        reviewer.assert_not_called()
        self.assertEqual(pipeline._current("candidates.json")["review_status"], "complete")
        report_path = pipeline.directory / "review.json"
        saved = read_json_file(report_path)
        write_json_atomic(report_path, {**saved, "status": "failed"})
        with self.assertRaisesRegex(ValueError, "review is incomplete"):
            pipeline.plan()
        write_json_atomic(report_path, {})
        with patch.object(llm_evaluator, "call_structured_llm") as generator, patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request) as reviewer:
            pipeline.evaluate()
        generator.assert_not_called()
        reviewer.assert_called_once()
        write_json_atomic(self.root / "transcript.json", {"segments": sentences(9)})
        with self.assertRaisesRegex(ValueError, "changed"):
            pipeline.plan()

    def test_pipeline_failure_blocks_planning_and_reuses_generated_candidates(self) -> None:
        pipeline = self.fixture_pipeline()
        failure = ProviderRequestError("test", "review", "credentials_invalid", "test failure", http_status=401)
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json", side_effect=failure), self.assertRaises(ProviderRequestError):
            pipeline.evaluate()
        pending = read_json_file(pipeline.directory / "candidates.json")
        self.assertEqual(pending["review_status"], "failed")
        self.assertEqual(pending["candidates"], [])
        self.assertEqual(len(pending["raw_candidates"]), 1)
        with self.assertRaisesRegex(ValueError, "review is incomplete"):
            pipeline.plan()
        with patch.object(llm_evaluator, "call_structured_llm") as generator, patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request):
            finished = pipeline.evaluate()
        generator.assert_not_called()
        self.assertEqual(finished["review_status"], "complete")
        self.assertEqual(len(finished["candidates"]), 1)

    def test_all_rejected_is_complete_review_not_provider_failure(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json", return_value={"results": [result(status="reject")]}):
            data = pipeline.evaluate()
        self.assertEqual(data["candidates"], [])
        self.assertEqual(read_json_file(pipeline.directory / "review.json")["rejected_count"], 1)
        pipeline.plan()
        report = pipeline.render()
        self.assertEqual(report["status"], "no_qualifying_clips")
        self.assertEqual(report["review_status"], "complete")
        self.assertEqual(report["review_file"], "auto_clips/review.json")
        self.assertFalse((self.root / "cuts.json").exists())
        self.assertFalse((self.root / "final.mp4").exists())

    def test_force_failure_cannot_reuse_previous_approval(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json", side_effect=approve_request):
            pipeline.evaluate()
        pipeline.force = True
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [candidate()]}), patch.object(highlight_checker, "request_highlight_json", side_effect=RuntimeError("failure")), self.assertRaises(RuntimeError):
            pipeline.evaluate()
        with self.assertRaisesRegex(ValueError, "review is incomplete"):
            pipeline._current("candidates.json")


if __name__ == "__main__":
    unittest.main()
