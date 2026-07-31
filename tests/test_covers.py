from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from video_automation import covers
from video_automation.config import Settings
from video_automation.io_utils import write_json_atomic
from video_automation.provider_errors import ProviderRequestError


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class CoverContextTests(unittest.TestCase):
    def test_summary_cover_title_stops_at_first_clause(self) -> None:
        self.assertEqual(
            covers._summary_cover_title(
                "主播因抽奖结果不满，情绪激动地指责系统不公平。"
            ),
            "主播因抽奖结果不满",
        )

    def test_prefers_metadata_title_and_semantic_highlight_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"source_name": "raw_stream_name.mp4"})
            write_json_atomic(job_dir / "metadata.json", {
                "titles": ["普通标题"],
                "cover_titles": ["挑战成功的瞬间"],
                "descriptions": ["元数据简介"],
            })
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 0, "end": 8, "text": "开场寒暄"},
                    {"start": 48, "end": 54, "text": "她决定再试一次"},
                    {"start": 54, "end": 62, "text": "真正爆点：挑战成功后大笑"},
                ],
            })
            write_json_atomic(job_dir / "cuts.json", {
                "clips": [
                    {
                        "start": 0,
                        "end": 8,
                        "content_score": 100,
                        "transcript_text": "结构分数很高但只是开场",
                    },
                ],
            })
            write_json_atomic(job_dir / "highlights.json", {
                "status": "ready",
                "summary": "主播多次尝试动作，最后意外成功并大笑。",
                "highlights": [
                    {
                        "start": 50,
                        "end": 60,
                        "score": 97,
                        "reason": "失败铺垫后突然成功，反应强烈",
                        "recommended_use": "竖屏视频封面主事件",
                    },
                ],
            })

            title = covers._preferred_cover_title(job_dir, "")
            context = covers._cover_context(job_dir, title)
            prompt = covers._build_prompt(context, "9:16", "short_video")

            self.assertEqual(title, "挑战成功的瞬间")
            self.assertEqual(context["summary"], "主播多次尝试动作，最后意外成功并大笑。")
            self.assertIn("真正爆点", context["highlights"])
            self.assertIn("失败铺垫后突然成功", context["highlights"])
            self.assertNotIn("结构分数很高但只是开场", context["highlights"])
            self.assertIn("do not invent public figures", prompt)
            self.assertIn("the application adds the title separately after generation", prompt)
            self.assertIn("not a poster", prompt)
            self.assertIn("never render or copy their wording", prompt)
            self.assertEqual(covers._cover_reference_timestamp(job_dir), 55)

    def test_fallback_ranks_clips_by_final_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"source_name": "fallback.mp4"})
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [{"start": 0, "end": 4, "text": "视频内容"}],
            })
            write_json_atomic(job_dir / "cuts.json", {
                "clips": [
                    {
                        "start": 0,
                        "end": 10,
                        "content_score": 90,
                        "final_score": 20,
                        "transcript_text": "结构高但语义弱",
                    },
                    {
                        "start": 20,
                        "end": 30,
                        "content_score": 30,
                        "final_score": 95,
                        "transcript_text": "语义评分最高的片段",
                    },
                ],
            })

            context = covers._cover_context(job_dir, "")

            self.assertLess(
                context["highlights"].find("语义评分最高的片段"),
                context["highlights"].find("结构高但语义弱"),
            )

    def test_cover_context_is_bounded_for_the_local_encoder_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"source_name": "long.mp4"})
            write_json_atomic(job_dir / "transcript.json", {"segments": []})
            write_json_atomic(job_dir / "cuts.json", {"clips": []})
            write_json_atomic(job_dir / "highlights.json", {
                "summary": "摘要" * 500,
                "highlights": [
                    {
                        "start": 10,
                        "end": 30,
                        "score": 99,
                        "reason": "高光证据" * 300,
                        "recommended_use": "主封面",
                    },
                ],
            })

            context = covers._cover_context(job_dir, "标题")

            self.assertLessEqual(
                len(context["summary"]),
                covers.COVER_SUMMARY_MAX_CHARS,
            )
            self.assertLessEqual(
                len(context["highlights"]),
                covers.COVER_HIGHLIGHTS_MAX_CHARS,
            )

    def test_openrouter_uses_dedicated_image_api_with_one_reference_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_path = Path(temp_dir) / "reference.jpg"
            reference_path.write_bytes(b"reference-image")
            settings = replace(
                Settings.load(),
                cover_provider="openrouter",
                cover_model="recraft/recraft-v4.1",
                cover_base_url="https://openrouter.ai/api/v1",
                cover_api_key="test-key",
                cover_quality="medium",
                cover_output_format="jpeg",
            )
            response = {
                "data": [
                    {"b64_json": "image-one"},
                    {"b64_json": "image-two"},
                    {"b64_json": "image-three"},
                ],
            }

            with patch.object(covers.urllib.request, "urlopen", return_value=_JsonResponse(response)) as urlopen:
                result = covers._openrouter_generate_images(
                    settings,
                    "Create a grounded cover",
                    3,
                    "9:16",
                    reference_path=reference_path,
                )

            self.assertEqual(len(result["data"]), 3)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/images")
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["n"], 3)
            self.assertEqual(body["aspect_ratio"], "9:16")
            self.assertNotIn("messages", body)
            reference_url = body["input_references"][0]["image_url"]["url"]
            self.assertTrue(reference_url.startswith("data:image/jpeg;base64,"))

    def test_openrouter_auth_failure_has_stable_provider_error_code(self) -> None:
        settings = replace(
            Settings.load(),
            cover_provider="openrouter",
            cover_model="recraft/recraft-v4.1",
            cover_base_url="https://openrouter.ai/api/v1",
            cover_api_key="test-key",
        )
        http_error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/images",
            401,
            "Unauthorized",
            {},
            BytesIO(b'{"error":{"message":"User not found."}}'),
        )

        with (
            patch.object(covers.urllib.request, "urlopen", side_effect=http_error),
            self.assertRaises(ProviderRequestError) as raised,
        ):
            covers._openrouter_generate_images(
                settings,
                "Create a grounded cover",
                1,
                "9:16",
            )

        self.assertEqual(raised.exception.code, "credentials_invalid")
        self.assertEqual(raised.exception.http_status, 401)

    def test_cover_generation_preflight_rejects_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                Settings.load(),
                cover_provider="openrouter",
                cover_model="",
                cover_api_key="test-key",
            )

            with self.assertRaises(ProviderRequestError) as raised:
                covers.generate_cover_candidates(settings, Path(temp_dir))

            self.assertEqual(raised.exception.code, "model_missing")

    def test_cover_manifest_persists_provider_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"source_name": "source.mp4"})
            settings = replace(
                Settings.load(),
                cover_provider="openrouter",
                cover_model="recraft/recraft-v4.1",
                cover_api_key="test-key",
            )
            provider_error = ProviderRequestError(
                "OpenRouter",
                "image generation",
                "credentials_invalid",
                "User not found.",
                http_status=401,
            )

            with (
                patch.object(covers, "_generate_images", side_effect=provider_error),
                self.assertRaises(ProviderRequestError),
            ):
                covers.generate_cover_candidates(
                    settings,
                    job_dir,
                    count=3,
                    aspects=["9:16"],
                )

            manifest = json.loads((job_dir / "cover_manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error_code"], "credentials_invalid")
            self.assertIn("[credentials_invalid]", manifest["error"])

    def test_semantic_summary_becomes_title_when_metadata_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "manifest.json", {"source_name": "raw_name.mp4"})
            write_json_atomic(job_dir / "highlights.json", {
                "summary": "主播连续失败后终于完成挑战！随后笑到停不下来。",
                "highlights": [],
            })

            title = covers._preferred_cover_title(job_dir, "")

            self.assertEqual(title, "主播连续失败后终于完成挑战")

    def test_cover_reference_sampling_checks_the_full_highlight(self) -> None:
        timestamps = covers._cover_reference_sample_timestamps(100, 140)

        self.assertEqual(timestamps, [106.0, 114.0, 120.0, 126.0, 134.0])
        self.assertEqual(covers._cover_reference_sample_timestamps(10, 13), [11.5])

    def test_cover_reference_can_choose_clean_frame_from_second_highlight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            source_path = job_dir / "source.mp4"
            source_path.write_bytes(b"video")
            write_json_atomic(job_dir / "manifest.json", {
                "source_path": str(source_path),
                "source_name": source_path.name,
            })
            write_json_atomic(job_dir / "highlights.json", {
                "highlights": [
                    {"start": 100, "end": 140, "score": 97},
                    {"start": 10, "end": 30, "score": 92},
                ],
            })

            def extract_frame(settings, source, output, timestamp):
                output.write_bytes(b"frame")
                return True

            frame_scores = (
                [{"score": -150, "face_count": 0, "repeated_thirds": 1.0}] * 5
                + [{"score": 140, "face_count": 1, "repeated_thirds": 0.0}] * 5
            )
            with (
                patch.object(covers, "_extract_cover_reference_frame", side_effect=extract_frame),
                patch.object(covers, "_cover_reference_frame_score", side_effect=frame_scores),
            ):
                result = covers._prepare_cover_reference(Settings.load(), job_dir)

            metadata = json.loads((job_dir / "highlight_thumbnail.json").read_text("utf-8"))
            self.assertEqual(result, job_dir / "highlight_thumbnail.jpg")
            self.assertEqual(metadata["interval_start"], 10)
            self.assertEqual(metadata["interval_end"], 30)
            self.assertEqual(metadata["semantic_score"], 92)
            self.assertEqual(metadata["repeated_thirds"], 0)
            self.assertEqual(metadata["selection_algorithm"], "semantic_visual_v3")

    def test_pillow_repeated_thirds_detector_distinguishes_mirrored_layout(self) -> None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest("Pillow is not installed")

        pattern = Image.new("L", (40, 40), 10)
        pattern_draw = ImageDraw.Draw(pattern)
        pattern_draw.ellipse((6, 4, 34, 36), fill=220)
        repeated = Image.new("L", (120, 40))
        for x in (0, 40, 80):
            repeated.paste(pattern, (x, 0))
        distinct = Image.new("L", (120, 40))
        distinct.paste(Image.new("L", (40, 40), 5), (0, 0))
        distinct.paste(Image.new("L", (40, 40), 125), (40, 0))
        distinct.paste(Image.new("L", (40, 40), 245), (80, 0))

        self.assertGreater(covers._repeated_vertical_thirds_pillow_score(repeated), 0.95)
        self.assertEqual(covers._repeated_vertical_thirds_pillow_score(distinct), 0)


if __name__ == "__main__":
    unittest.main()
