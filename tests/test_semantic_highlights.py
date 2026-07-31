from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation import llm_tools
from video_automation.config import Settings
from video_automation.cuts import generate_cuts
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.provider_errors import ProviderRequestError


class SemanticHighlightCutTests(unittest.TestCase):
    def test_highlights_feed_final_score_without_reordering_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "silence.json", {
                "status": "ok",
                "min_gap_seconds": 0,
                "silences": [{"start": 10.0, "end": 12.0}],
            })
            write_json_atomic(job_dir / "freeze.json", {"status": "skipped", "freezes": []})
            write_json_atomic(job_dir / "scene.json", {"status": "ok", "scenes": []})
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 1.0, "end": 8.0, "text": "普通开场说明"},
                    {"start": 13.0, "end": 22.0, "text": "这里突然情绪激动并出现核心爆点"},
                ]
            })
            write_json_atomic(job_dir / "highlights.json", {
                "status": "ready",
                "summary": "第二段更适合切片",
                "highlights": [
                    {
                        "start": 13.0,
                        "end": 22.0,
                        "score": 92,
                        "reason": "情绪激动，适合短视频开头",
                        "recommended_use": "15 秒爆点",
                    }
                ],
            })

            cuts = generate_cuts(job_dir, 30.0, force=True)
            clips = cuts["clips"]

            self.assertLess(clips[0]["start"], clips[1]["start"])
            self.assertEqual(clips[1]["semantic_score"], 92.0)
            self.assertIn("情绪激动", clips[1]["semantic_reasons"][0])
            self.assertGreater(clips[1]["final_score"], clips[0]["final_score"])
            self.assertEqual(clips[1]["final_rank"], 1)
            self.assertEqual(cuts["content_scoring"]["method"], "0.4*structure_score+0.6*semantic_score")

    def test_generate_highlights_normalizes_scores_ranges_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 120})
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 120,
                "clips": [
                    {"start": 10, "end": 35, "duration": 25, "keep": True, "content_score": 55},
                    {"start": 60, "end": 95, "duration": 35, "keep": True, "content_score": 70},
                ],
            })
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 10, "end": 20, "text": "挑战开始"},
                    {"start": 20, "end": 34, "text": "突然成功并大笑"},
                    {"start": 60, "end": 72, "text": "普通说明"},
                ],
            })
            model_payload = {
                "summary": "主播完成挑战并出现强烈反应",
                "highlights": [
                    {
                        "start": 11,
                        "end": 30,
                        "score": 140,
                        "reason": " 挑战成功，主播大笑 ",
                        "recommended_use": " 开场爆点 ",
                    },
                    {
                        "start": 12,
                        "end": 29,
                        "score": 90,
                        "reason": "与第一条重复",
                        "recommended_use": "重复",
                    },
                    {
                        "start": 100,
                        "end": 110,
                        "score": 99,
                        "reason": "不在提供的时间范围",
                        "recommended_use": "无效",
                    },
                    {
                        "start": 10,
                        "end": 95,
                        "score": 88,
                        "reason": "时间过长",
                        "recommended_use": "无效",
                    },
                ],
            }

            with patch.object(llm_tools, "_call_structured_llm", return_value=model_payload):
                payload = llm_tools.generate_highlights(Settings.load(), job_dir, force=True)

            self.assertEqual(payload["summary"], "主播完成挑战并出现强烈反应")
            self.assertEqual(len(payload["highlights"]), 1)
            self.assertEqual(payload["highlights"][0]["start"], 11.0)
            self.assertEqual(payload["highlights"][0]["end"], 30.0)
            self.assertEqual(payload["highlights"][0]["score"], 100.0)
            self.assertEqual(payload["highlights"][0]["reason"], "挑战成功，主播大笑")
            saved = read_json_file(job_dir / "highlights.json")
            self.assertEqual(saved["highlights"], payload["highlights"])
            attached = read_json_file(job_dir / "cuts.json")
            self.assertEqual(attached["semantic_highlights"], payload["highlights"])
            attempt = read_json_file(job_dir / "highlights_attempt.json")
            self.assertEqual(attempt["status"], "done")
            self.assertEqual(attempt["highlight_count"], 1)
            self.assertNotIn("transcript", attempt)

    def test_failed_highlight_attempt_is_recorded_without_overwriting_last_good_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            existing = {
                "status": "debug_gold",
                "backend": "manual_debug",
                "highlights": [{"start": 10, "end": 20, "score": 95, "reason": "人工基准"}],
            }
            write_json_atomic(job_dir / "highlights.json", existing)
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 60})
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 60,
                "clips": [{"start": 10, "end": 20, "keep": True}],
            })
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [{"start": 10, "end": 20, "text": "候选高光"}],
            })
            settings = replace(
                Settings.load(),
                llm_provider="openai",
                llm_model="",
                openai_api_key="test-key",
            )

            with self.assertRaises(ProviderRequestError) as raised:
                llm_tools.generate_highlights(settings, job_dir, force=True)

            self.assertEqual(raised.exception.code, "model_missing")
            self.assertEqual(read_json_file(job_dir / "highlights.json"), existing)
            attempt = read_json_file(job_dir / "highlights_attempt.json")
            self.assertEqual(attempt["status"], "failed")
            self.assertEqual(attempt["error_code"], "model_missing")
            self.assertEqual(attempt["provider"], "openai")
            self.assertEqual(attempt["model"], "")
            self.assertNotIn("候选高光", attempt["error"])

    def test_highlight_prompt_samples_the_full_transcript_and_candidate_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            segments = [
                {"start": index * 2, "end": index * 2 + 1.5, "text": f"第{index}句"}
                for index in range(240)
            ]
            write_json_atomic(job_dir / "manifest.json", {
                "source_name": "long-stream.mp4",
                "duration_seconds": 480,
            })
            write_json_atomic(job_dir / "transcript.json", {
                "language": "ko",
                "segments": segments,
            })
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 480,
                "clips": [
                    {"start": 430, "end": 450, "content_score": 99, "scene_count": 3},
                ],
            })
            write_json_atomic(job_dir / "scene.json", {"scenes": []})

            context = llm_tools._highlights_context(job_dir)
            prompt = json.loads(llm_tools._highlights_prompt(job_dir))

            self.assertEqual(len(context["transcript_sample"]), 180)
            self.assertEqual(context["transcript_sample"][0]["text"], "第0句")
            self.assertEqual(context["transcript_sample"][-1]["text"], "第239句")
            self.assertIn("第215句", context["candidate_clips"][0]["text"])
            self.assertEqual(context["transcript_language"], "ko")
            self.assertEqual(prompt["selection_task"]["target_highlight_count"], 4)
            self.assertEqual(len(prompt["transcript_spans"]), 180)
            self.assertNotIn("candidate_clips", prompt)
            self.assertNotIn("scenes", prompt)

    def test_local_highlight_spans_keep_global_context_and_bound_chunk_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            words = [
                {
                    "start": float(index),
                    "end": float(index) + 0.8,
                    "word": f"词{index}",
                }
                for index in range(125)
            ]
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 125})
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {
                        "start": 0,
                        "end": 125,
                        "text": " ".join(item["word"] for item in words),
                        "words": words,
                    }
                ],
            })

            context = llm_tools._highlights_context(job_dir)
            global_spans = llm_tools._global_highlight_prompt_spans(context)
            bounded_spans = llm_tools._highlight_prompt_spans(job_dir, context)

            self.assertEqual(len(global_spans), 1)
            self.assertEqual(global_spans[0]["end"], 125.0)
            self.assertEqual(
                [(item["start"], item["end"]) for item in bounded_spans],
                [(0.0, 50.0), (40.0, 90.0), (80.0, 125.0)],
            )
            self.assertTrue(all(item["end"] - item["start"] <= 50 for item in bounded_spans))

    def test_local_highlight_analysis_routes_long_transcripts_through_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            segments = [
                {
                    "start": index * 12,
                    "end": index * 12 + 10,
                    "text": f"第{index}段发生具体事件并出现反应",
                }
                for index in range(13)
            ]
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 166})
            write_json_atomic(job_dir / "transcript.json", {"segments": segments})
            settings = replace(Settings.load(), llm_provider="local")
            chunk_payload = {
                "summary": "分块分析",
                "highlights": [
                    {
                        "start": 0,
                        "end": 10,
                        "score": 90,
                        "reason": "开场发生具体事件",
                        "recommended_use": "候选",
                    }
                ],
            }

            with (
                patch.object(
                    llm_tools,
                    "_call_local_chunked_highlights",
                    return_value=chunk_payload,
                ) as chunked,
                patch.object(llm_tools, "_call_structured_llm") as global_call,
            ):
                payload = llm_tools.analyze_highlights(settings, job_dir)

            self.assertEqual(payload["summary"], "分块分析")
            self.assertEqual(len(payload["highlights"]), 1)
            chunked.assert_called_once()
            global_call.assert_not_called()

    def test_local_highlight_analysis_keeps_short_transcripts_in_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 30})
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 0, "end": 10, "text": "开场提出挑战"},
                    {"start": 12, "end": 25, "text": "挑战成功并出现反应"},
                ],
            })
            settings = replace(Settings.load(), llm_provider="local")
            global_payload = {
                "summary": "整段分析",
                "highlights": [
                    {
                        "start": 12,
                        "end": 25,
                        "score": 93,
                        "reason": "挑战成功并出现反应",
                        "recommended_use": "主高光",
                    }
                ],
            }

            with (
                patch.object(
                    llm_tools,
                    "_call_structured_llm",
                    return_value=global_payload,
                ) as global_call,
                patch.object(llm_tools, "_call_local_chunked_highlights") as chunked,
            ):
                payload = llm_tools.analyze_highlights(settings, job_dir)

            self.assertEqual(payload["summary"], "整段分析")
            self.assertEqual(payload["highlights"][0]["start"], 12.0)
            global_call.assert_called_once()
            chunked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
