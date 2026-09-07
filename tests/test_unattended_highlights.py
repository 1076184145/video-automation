from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import urllib.error
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_automation import api, config, llm_evaluator, llm_tools, pipeline_executor, render, worker
from video_automation.cuts import build_filter_complex_with_crossfade, snap_to_silence_valley
from video_automation.highlight_edits import build_highlight_edit, map_interval
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.llm_evaluator import LLMClipEvaluator, normalize_sentences, temporal_nms
from video_automation.provider_errors import ProviderRequestError
from video_automation.subtitles import write_highlight_ass
from video_automation.task_queue import QueueControlRequested
from video_automation.unattended_highlights import UnattendedHighlights


def selection(start_id: int = 1, end_id: int = 4, score: int = 90) -> dict:
    return dict(title="A complete idea", start_id=start_id, end_id=end_id, hook_score=score, reason="Hook, evidence, conclusion")


def sentences(count: int = 4, duration: float = 10.) -> list[dict]:
    return [dict(id=i + 1, start=i * duration, end=(i + 1) * duration, text=f"Topic {i + 1}.", words=[])
            for i in range(count)]


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict(os.environ, {}, clear=True), patch.object(config, "PROJECT_ROOT", self.root), patch.object(config, "_cached_env_file", return_value={}):
            self.settings = config.Settings.load()

    def audio(self, duration: float = 45., *, rate: int = 8000) -> Path:
        path = self.root / "audio.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            stream.writeframes(b"\0\0" * round(duration * rate))
        return path


class EvaluatorTests(Fixture):
    def test_normalization_keeps_real_word_times_and_stable_ids(self) -> None:
        result = normalize_sentences([dict(start=0, end=10, text="Hello world. Next.", words=[
            dict(start=.5, end=1., word="Hello"), dict(start=2., end=3., word="world."),
            dict(start=6., end=8., word=" Next.")])])
        self.assertEqual([(s["id"], s["start"], s["end"], s["text"]) for s in result],
                         [(1, .5, 3., "Hello world."), (2, 6., 8., "Next.")])
        self.assertEqual(normalize_sentences([dict(start=0, end=10, text="No word times.")])[0]["words"], [])

    def test_incomplete_or_malformed_word_alignment_falls_back_without_losing_text(self) -> None:
        for words in ([dict(start=0., end=1., word="Only")], [dict(word="Only")], "missing"):
            result = normalize_sentences([dict(start=0., end=10., text="Only part is aligned.", words=words)])
            self.assertEqual(result[0]["text"], "Only part is aligned.")
            self.assertEqual(result[0]["words"], [])

    def test_rejects_invalid_timestamps_and_duplicate_input_ids(self) -> None:
        for value in (float("nan"), float("inf"), True, "0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_sentences([dict(start=value, end=10, text="Bad")])
        with self.assertRaises(ValueError):
            LLMClipEvaluator(self.settings).extract_highlights([sentences()[0], sentences()[0]])

    def test_repairs_dependent_opening_and_rejects_bad_candidates(self) -> None:
        items = sentences(8)
        items[1]["text"] = "但是这个细节很重要。"
        invalid = [selection(999), selection(4, 1), selection(score=True), selection(score=101),
                   selection(1, 8), selection(1, 2), {**selection(), "title": " "}]
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [selection(2, 4), *invalid]}) as request:
            evaluator = LLMClipEvaluator(self.settings)
            clips = evaluator.extract_highlights(items)
        self.assertEqual(len(clips), 1)
        self.assertEqual((clips[0]["start_id"], clips[0]["start"], clips[0]["end"]), (1, 0., 40.))
        self.assertEqual(evaluator.rejected_count, len(invalid))
        self.assertIn('"id": 1', request.call_args.kwargs["user"])

    def test_long_windows_cover_a_75_second_boundary_clip(self) -> None:
        data = sentences(120, 15)
        windows = LLMClipEvaluator(self.settings).windows(data)
        self.assertEqual(len(windows), 2)
        self.assertGreaterEqual(windows[0][-1]["end"] - windows[1][0]["start"], 90)
        # This interval crosses 20min and would be lost by non-overlapping chunks.
        self.assertTrue(any({79, 80, 81, 82, 83} <= {s["id"] for s in window} for window in windows))
        self.assertTrue(all(window[-1]["end"] - window[0]["start"] <= 1200 for window in windows))

    def test_character_budget_advances_without_losing_sentences(self) -> None:
        settings = replace(self.settings, highlight_request_chars=2000)
        data = [{**s, "text": "字" * 500} for s in sentences(15)]
        windows = LLMClipEvaluator(settings).windows(data)
        self.assertEqual({s["id"] for window in windows for s in window}, set(range(1, 16)))
        self.assertEqual(len({window[0]["id"] for window in windows}), len(windows))
        self.assertTrue(all(len(window) <= 3 for window in windows))

    def test_temporal_nms_uses_strict_iou_and_higher_score(self) -> None:
        a, b, c = ({"start": 0., "end": 60., "hook_score": 70},
                   {"start": 10., "end": 70., "hook_score": 90},
                   {"start": 90., "end": 130., "hook_score": 80})
        self.assertEqual(temporal_nms([a, b, c]), [b, c])
        self.assertEqual(len(temporal_nms([a, {**a, "start": 20., "end": 80.}])), 2)  # IoU == .5

    def test_only_transient_requests_retry_and_cancel_interrupts_backoff(self) -> None:
        evaluator = LLMClipEvaluator(self.settings)
        transient = ProviderRequestError("test", "request", "rate_limited", "Retry", http_status=429)
        with patch.object(llm_evaluator, "call_structured_llm", side_effect=[transient, {"clips": []}]) as call, patch.object(llm_evaluator.time, "sleep"):
            self.assertEqual(evaluator.extract_highlights(sentences()), [])
            self.assertEqual(call.call_count, 2)
        permanent = ProviderRequestError("test", "request", "credentials_invalid", "No", http_status=401)
        with patch.object(llm_evaluator, "call_structured_llm", side_effect=permanent) as call, self.assertRaises(ProviderRequestError):
            evaluator.extract_highlights(sentences())
        self.assertEqual(call.call_count, 1)
        evaluator.control_callback = Mock(side_effect=[None, None, "paused"])
        with patch.object(llm_evaluator, "call_structured_llm", side_effect=transient), self.assertRaises(QueueControlRequested):
            evaluator.extract_highlights(sentences())


class TransportTests(Fixture):
    def test_explicit_chat_endpoint_accepts_root_object_on_loopback_without_key(self) -> None:
        settings = replace(self.settings, llm_openai_base_url="http://127.0.0.1:11434/v1", llm_model="test-local")
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"clips": []}'}}]}).encode())
        with patch.object(llm_tools.urllib.request, "urlopen", return_value=response) as call:
            payload = llm_tools.call_structured_llm(settings, system="JSON", user="Test only", schema=llm_evaluator._schema(), schema_name="test")
        request = call.call_args.args[0]
        self.assertEqual(payload, {"clips": []})
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertFalse(request.has_header("Authorization"))
        self.assertEqual(json.loads(request.data)["response_format"]["type"], "json_schema")

    def test_json_object_mode_still_repairs_schema_invalid_output(self) -> None:
        settings = replace(self.settings, llm_openai_base_url="http://localhost:11434/v1", llm_model="test", llm_response_format="json_object")
        responses = [io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
                     for content in ('```json\n{"wrong": []}\n```', '{"clips": []}')]
        with patch.object(llm_tools.urllib.request, "urlopen", side_effect=responses) as call:
            result = llm_tools.call_structured_llm(settings, system="JSON", user="Test", schema=llm_evaluator._schema(), schema_name="test")
        self.assertEqual(result, {"clips": []})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(json.loads(call.call_args.args[0].data)["response_format"], {"type": "json_object"})

    def test_insecure_or_credential_embedded_endpoints_never_send_a_request(self) -> None:
        for base in ("http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=x"):
            settings = replace(self.settings, llm_openai_base_url=base, llm_model="test")
            with patch.object(llm_tools.urllib.request, "urlopen") as call, self.assertRaises(ProviderRequestError):
                llm_tools.call_structured_llm(settings, system="JSON", user="Test", schema={}, schema_name="test")
            call.assert_not_called()


class AcousticAndSubtitleTests(Fixture):
    def test_directional_valleys_and_loud_signal_fallback(self) -> None:
        path = self.root / "stereo.wav"
        rate = 8000
        with wave.open(str(path), "wb") as stream:
            stream.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            frames = bytearray()
            for i in range(rate):
                value = 0 if .36 <= i / rate <= .42 or .58 <= i / rate <= .64 else 10000
                frames.extend(struct.pack("<hh", value, -value))
            stream.writeframes(frames)
        self.assertTrue(.36 <= snap_to_silence_valley(str(path), .5, True) <= .42)
        self.assertTrue(.58 <= snap_to_silence_valley(str(path), .5, False) <= .64)
        self.assertEqual(snap_to_silence_valley(str(path), .2, True), .2)  # Antiphase != silence.
        self.assertEqual(snap_to_silence_valley(str(self.root / "missing.wav"), .5, True), .5)

    def test_pause_compression_and_word_mapping_share_one_timeline(self) -> None:
        path = self.audio()
        speech = [dict(start=0., end=40., text="One. Two.", words=[
            dict(start=0., end=9., text="One."), dict(start=16., end=40., text="Two.")])]
        edit = build_highlight_edit({**selection(), "start": 0., "end": 40.}, speech,
                                    [dict(start=10., end=15.)], path, source_duration=45.)
        self.assertAlmostEqual(edit["duration"], 35.2)
        self.assertAlmostEqual(map_interval(16., 40., edit["spans"])[0][0], 11.2)
        self.assertAlmostEqual(sum(s["end"] - s["start"] for s in edit["spans"]), edit["duration"])
        graph = build_filter_complex_with_crossfade(edit["spans"])
        self.assertEqual(graph.count("afade=t=in"), 2)
        self.assertNotIn("acrossfade=", graph)

    def test_speech_is_not_deleted_even_when_silencedetect_calls_it_silent(self) -> None:
        path = self.audio()
        data = [dict(start=0., end=40., text="Quiet but spoken", words=[])]
        edit = build_highlight_edit({**selection(), "start": 0., "end": 40.}, data,
                                    [dict(start=10., end=30.)], path, source_duration=45.)
        self.assertEqual(edit["duration"], 40.)
        self.assertEqual(len(edit["spans"]), 1)

    def test_rejects_underlength_after_compression_and_neighbor_word_snaps(self) -> None:
        path = self.audio()
        with self.assertRaisesRegex(ValueError, "30-75"):
            build_highlight_edit({**selection(), "start": 0., "end": 32.}, [],
                                 [dict(start=10., end=20.)], path, source_duration=45.)
        data = [dict(start=.95, end=1., text="Previous.", words=[]), dict(start=1., end=40., text="Selected.", words=[])]
        with patch("video_automation.highlight_edits.snap_to_silence_valley", side_effect=[.8, 40.]):
            edit = build_highlight_edit({**selection(), "start": 1., "end": 40.}, data, [], path, source_duration=45.)
        self.assertEqual(edit["start"], 1.)

    def test_karaoke_preserves_real_gap_and_escapes_ass_injection(self) -> None:
        spans = [dict(start=10., end=50., output_start=0., output_end=40.)]
        data = [dict(start=11., end=15., text="a b", words=[dict(start=11., end=12., text="A"), dict(start=12.3, end=13., text=r"{\pos(0,0)}B")]),
                dict(start=20., end=22., text="Fallback sentence", words=[])]
        output = self.root / "captions.ass"
        self.assertEqual(write_highlight_ass(self.settings, data, spans, output), "mixed")
        document = output.read_text(encoding="utf-8")
        self.assertIn(r"{\kf100}A{\k30}", document)
        self.assertNotIn(r"{\pos(0,0)}", document)
        self.assertIn("0:00:10.00,0:00:12.00", document)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required for synthetic smoke test")
    def test_real_ffmpeg_many_splices_keep_av_duration_aligned(self) -> None:
        source, output = self.root / "synthetic.mp4", self.root / "edited.mp4"
        result = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=6",
                                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        spans = [dict(start=i * .5, end=i * .5 + 7 / 30) for i in range(10)]
        result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-filter_complex",
                                 build_filter_complex_with_crossfade(spans), "-map", "[outv]", "-map", "[outa]",
                                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(output)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        payload = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)], timeout=10))
        durations = [float(s["duration"]) for s in payload["streams"] if s["codec_type"] in {"audio", "video"}]
        self.assertEqual(len(durations), 2)
        self.assertTrue(all(abs(duration - 70 / 30) <= 1 / 30 for duration in durations), durations)
        self.assertLessEqual(abs(durations[0] - durations[1]), 1 / 30)


class PipelineAndCacheTests(Fixture):
    def fixture_pipeline(self) -> UnattendedHighlights:
        self.audio(85.)
        source = self.root / "source.mp4"
        source.touch()
        write_json_atomic(self.root / "transcript.json", {"segments": sentences(8)})
        write_json_atomic(self.root / "silence.json", {"silences": []})
        write_json_atomic(self.root / "manifest.json", {"duration_seconds": 85., "streams": [{"codec_type": "video", "width": 160, "height": 90}]})
        pipeline = UnattendedHighlights(self.settings, self.root, source)
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": [selection(), selection(5, 8)]}):
            pipeline.evaluate()
        pipeline.plan()
        return pipeline

    def test_caches_invalidate_when_transcript_changes(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch.object(llm_evaluator, "call_structured_llm") as call:
            pipeline.evaluate()
        call.assert_not_called()
        write_json_atomic(self.root / "transcript.json", {"segments": sentences(5)})
        with self.assertRaisesRegex(ValueError, "changed"):
            pipeline.plan()

    def test_partial_failure_continues_and_completed_outputs_reuse(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch("video_automation.unattended_highlights.generate_vertical_crop_plan"), patch("video_automation.unattended_highlights.render_highlight_edit", side_effect=[RuntimeError("first failed"), None]) as renderer:
            report = pipeline.render()
        self.assertEqual(renderer.call_count, 2)
        self.assertEqual(report["status"], "partial")
        self.assertEqual([c["status"] for c in report["clips"]], ["failed", "done"])
        with patch("video_automation.unattended_highlights.generate_vertical_crop_plan"), patch("video_automation.unattended_highlights.render_highlight_edit") as renderer, patch("video_automation.unattended_highlights.valid_highlight_output", return_value=True):
            report = pipeline.render()
        self.assertEqual(report["status"], "done")
        self.assertEqual(renderer.call_count, 1)

    def test_cancel_is_not_swallowed_as_a_failed_clip(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch("video_automation.unattended_highlights.generate_vertical_crop_plan"), patch("video_automation.unattended_highlights.render_highlight_edit", side_effect=QueueControlRequested("paused")), self.assertRaises(QueueControlRequested):
            pipeline.render()
        self.assertEqual(read_json_file(pipeline.directory / "index.json")["status"], "paused")

    def test_no_candidates_and_all_failed_are_distinct(self) -> None:
        pipeline = self.fixture_pipeline()
        with patch("video_automation.unattended_highlights.generate_vertical_crop_plan"), patch("video_automation.unattended_highlights.render_highlight_edit", side_effect=RuntimeError("render failure")), self.assertRaisesRegex(RuntimeError, "All automatic"):
            pipeline.render()
        self.assertEqual(read_json_file(pipeline.directory / "index.json")["status"], "failed")
        pipeline.force = True
        with patch.object(llm_evaluator, "call_structured_llm", return_value={"clips": []}):
            pipeline.evaluate()
        pipeline.plan()
        self.assertEqual(pipeline.render()["status"], "no_qualifying_clips")

    def test_api_and_cli_opt_in_do_not_change_the_global_default(self) -> None:
        changed, options = api._queued_process_config(self.settings, {"unattended_highlights": True})
        self.assertTrue(changed.unattended_highlights_enabled)
        self.assertFalse(self.settings.unattended_highlights_enabled)
        with self.assertRaises(ValueError):
            api._queued_process_config(self.settings, {"unattended_highlights": "false"})
        with patch.object(worker.Settings, "load", return_value=self.settings), patch.object(worker, "bootstrap_dirs"), patch.object(worker, "configure_root_logger"), patch.object(worker, "process_file") as process:
            worker.main(["--once", "synthetic.mp4", "--unattended-highlights"])
        self.assertTrue(process.call_args.args[0].unattended_highlights_enabled)

    def test_automatic_pipeline_enables_only_its_own_branch(self) -> None:
        job = SimpleNamespace(job_dir=self.root, source_path=self.root / "source.mp4", status="queued",
                              set_status=Mock(), fail=Mock(), cancel=Mock())
        options = dict(force=False, detect_silence_enabled=False, detect_freeze_enabled=True, detect_scenes_enabled=True,
                       render_review_enabled=True, render_final_enabled=True, vertical_enabled=True, burn_subtitles_enabled=True,
                       plan_crop_enabled=True, plan_uvr_enabled=True, skip_transcribe=False, progress_enabled=False)
        with patch.object(pipeline_executor, "configure_job_logger"), patch.object(pipeline_executor, "close_job_logger"), patch.object(pipeline_executor, "StageRunRepository"), patch.object(pipeline_executor, "run_pipeline") as run:
            pipeline_executor.process_job(replace(self.settings, unattended_highlights_enabled=True), job, **options)
        job.fail.assert_not_called()
        stages = {s.name for s in run.call_args.args[2] if s.enabled}
        self.assertTrue({"probe", "extract_audio", "transcribe", "detect_silence", "evaluate_highlights", "plan_highlights", "render_highlights"} <= stages)
        self.assertFalse({"plan_cuts", "refine_cuts", "render_final", "render_review", "render_platform_variants"} & stages)
        job.set_status.assert_called_once_with("done")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe required for integration")
    def test_real_automatic_pipeline_from_synthetic_source_to_vertical_mp4(self) -> None:
        from video_automation.jobs import create_job

        source = self.root / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=navy:size=160x90:rate=30:duration=41",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=41", "-c:v", "libx264",
                        "-preset", "ultrafast", "-c:a", "aac", str(source)], capture_output=True, check=True, timeout=30)
        settings = replace(self.settings, unattended_highlights_enabled=True, highlight_llm_checker_enabled=True,
                           highlight_graph_enabled=True,
                           source_integrity_scan_enabled=False,
                           ffmpeg_path=Path(shutil.which("ffmpeg")), ffprobe_path=Path(shutil.which("ffprobe")),
                           render_x264_preset="ultrafast", high_quality_audio_enabled=False, whisper_backend="faster-whisper")
        job = create_job(settings, source)

        def transcribe(settings, audio_path, job_dir, **kwargs):
            data = sentences()
            for sentence in data:
                sentence["words"] = [{"start": sentence["start"], "end": sentence["end"], "word": sentence["text"]}]
            write_json_atomic(job_dir / "transcript.json", {"segments": data})

        def structured_response(*args, **kwargs):
            if kwargs["schema_name"] == "highlight_review":
                return {"results": [{"candidate_id": "candidate-1", "status": "pass",
                                     "reason": "The complete synthetic transcript has a setup and conclusion.", "evidence_ids": [1, 4]}]}
            return {"clips": [selection()]}

        with patch.object(pipeline_executor, "transcribe_audio", side_effect=transcribe), patch.object(llm_evaluator, "call_structured_llm", side_effect=structured_response), patch.object(render, "probe_nvenc_encoder", return_value={"available": False, "detail": "synthetic software fallback test"}):
            pipeline_executor.process_job(settings, job, force=False, detect_silence_enabled=False,
                                           detect_freeze_enabled=False, detect_scenes_enabled=False, render_review_enabled=False,
                                           render_final_enabled=False, vertical_enabled=False, burn_subtitles_enabled=False,
                                           plan_crop_enabled=False, plan_uvr_enabled=False, skip_transcribe=False, progress_enabled=False)
        report = read_json_file(job.job_dir / "auto_clips" / "index.json")
        self.assertEqual(job.status, "done", {"error": job.error, "report": report})
        self.assertEqual(report["status"], "done")
        self.assertEqual(report["review_status"], "complete")
        self.assertEqual(report["generation_status"], "complete")
        self.assertEqual(read_json_file(job.job_dir / report["generation_file"])["completed_nodes"], 3)
        self.assertEqual(read_json_file(job.job_dir / report["review_file"])["passed_count"], 1)
        output = job.job_dir / report["clips"][0]["file"]
        self.assertTrue(render.valid_highlight_output(settings, output, 40.))
        self.assertFalse((job.job_dir / "cuts.json").exists())
        self.assertFalse((job.job_dir / "final.mp4").exists())
        self.assertEqual(report["clips"][0]["subtitle_mode"], "word_karaoke")


class OutputSafetyTests(Fixture):
    def test_missing_audio_or_truncated_duration_never_counts_as_success(self) -> None:
        output = self.root / "output.mp4"
        output.write_bytes(b"test fixture")
        video = dict(codec_type="video", width=1080, height=1920, duration="40")
        audio = dict(codec_type="audio", duration="40")
        for streams, expected in (([video], False), ([video, {**audio, "duration": "1"}], False),
                                  ([{**video, "width": 160}, audio], False), ([video, audio], True)):
            response = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": streams}))
            with patch.object(render.subprocess, "run", return_value=response):
                self.assertEqual(render.valid_highlight_output(self.settings, output, 40.), expected)

    def test_failed_nvenc_falls_back_without_overwriting_previous_completed_output(self) -> None:
        previous = self.root / "final.mp4"
        previous.write_bytes(b"previous completed output")
        (self.root / "subtitles.ass").touch()
        edit = dict(fps=30, duration=40., spans=[dict(start=0., end=40.)])
        with patch.object(render, "effective_render_settings", side_effect=lambda settings: (settings, None)), patch.object(render, "_run_ffmpeg_with_resource_gate", return_value=SimpleNamespace(returncode=1, stderr="failed")) as execute, self.assertRaises(RuntimeError):
            render.render_highlight_edit(self.settings, self.root / "synthetic.mp4", self.root, edit)
        self.assertEqual(previous.read_bytes(), b"previous completed output")
        self.assertEqual([call.args[0].render_video_encoder for call in execute.call_args_list], ["h264_nvenc", "libx264"])


if __name__ == "__main__":
    unittest.main()
