from __future__ import annotations

import argparse
import itertools
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_automation import api, config, pipeline_executor, profiles, worker
from video_automation.io_utils import write_json_atomic
from video_automation.pipeline_spec import PIPELINE_STAGE_SPECS
from video_automation.task_queue import QueueControlRequested


FLAGS = (
    "detect_silence", "detect_freeze", "detect_scenes", "render_review",
    "render_final", "vertical", "burn_subtitles", "plan_crop", "plan_uvr",
)
PROCESS_DEFAULTS = {
    "force": False,
    **{f"{flag}_enabled": False for flag in FLAGS},
    "skip_transcribe": False,
    "progress_enabled": False,
}


class BackendFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # No real project settings, credentials, media or runtime state are used.
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(config, "PROJECT_ROOT", self.root),
            patch.object(config, "_cached_env_file", return_value={}),
        ):
            self.settings = config.Settings.load()
        self.job = SimpleNamespace(
            job_dir=self.root / "job", source_path=self.root / "source.mp4",
            status="queued", error=None,
            set_status=Mock(), fail=Mock(), cancel=Mock(),
        )


class WorkerOptionContracts(BackendFixture):
    def test_cli_modes_forward_every_flag_and_preserve_return_codes(self) -> None:
        cases = (
            (["--resume"], "resume_jobs", (), 0),
            (["--batch", "batch.json"], "process_batch", (Path("batch.json"),), 7),
            (["--once", "source.mp4"], "process_file", (Path("source.mp4"),), 0),
            ([], "watch", (), 0),
        )
        flag_names = (*FLAGS, "force", "skip_transcribe", "progress")
        for mode_args, target, positional, expected_result in cases:
            for selected in ((), *[(name,) for name in flag_names], flag_names):
                with (
                    self.subTest(mode=target, selected=selected),
                    patch.object(worker.Settings, "load", return_value=self.settings),
                    patch.object(worker, "bootstrap_dirs"),
                    patch.object(worker, "configure_root_logger"),
                    patch.object(worker, target, return_value=7) as execute,
                ):
                    argv = [*mode_args, *[f"--{name.replace('_', '-')}" for name in selected]]
                    result = worker.main(argv)
                    expected = dict(PROCESS_DEFAULTS)
                    for name in selected:
                        key = f"{name}_enabled" if name in FLAGS else name
                        expected["progress_enabled" if name == "progress" else key] = True
                    execute.assert_called_once_with(self.settings, *positional, **expected)
                    self.assertEqual(result, expected_result)

    def test_cli_mode_priority_and_non_processing_early_returns(self) -> None:
        with (
            patch.object(worker.Settings, "load", return_value=self.settings),
            patch.object(worker, "bootstrap_dirs"),
            patch.object(worker, "configure_root_logger"),
            patch.object(worker, "health_check", return_value=3) as health,
            patch.object(worker, "resume_jobs") as resume,
            patch.object(worker, "process_batch", return_value=2) as batch,
            patch.object(worker, "process_file") as once,
        ):
            arguments = ["--resume", "--batch", "batch.json", "--once", "source.mp4"]
            self.assertEqual(worker.main(["--health", "--json", *arguments]), 3)
            health.assert_called_once_with(self.settings, as_json=True)
            resume.assert_not_called()
            self.assertEqual(worker.main(arguments), 0)
            resume.assert_called_once()
            batch.assert_not_called()
            self.assertEqual(worker.main(arguments[1:]), 2)
            batch.assert_called_once()
            once.assert_not_called()

    def test_profiles_enable_flags_without_disabling_explicit_options(self) -> None:
        for profile in ("fast", "analysis", "douyin", "bilibili", "youtube-shorts", "unknown", None):
            for initial in (False, True):
                with self.subTest(profile=profile, initial=initial):
                    args = argparse.Namespace(profile=profile, **dict.fromkeys(FLAGS, initial))
                    options = {f"{name}_enabled": initial for name in FLAGS}
                    before = dict(options)
                    result = profiles.apply_profile_flags(options, profile)
                    worker._apply_profile_to_args(args)
                    expected = profiles.profile_flags(profile)
                    for name in FLAGS:
                        self.assertEqual(getattr(args, name), initial or bool(expected.get(name)))
                        self.assertEqual(result[f"{name}_enabled"], getattr(args, name))
                    self.assertEqual(options, before)
                    self.assertIsNot(result, options)

    def test_batch_item_values_override_cli_and_file_defaults_even_when_false(self) -> None:
        batch_path = self.root / "batch.json"
        missing = object()
        values = (missing, False, True, None, 0, "false")
        for name in ("force", *FLAGS, "skip_transcribe"):
            key = f"{name}_enabled" if name in FLAGS else name
            for cli, file_default, item_value in itertools.product((False, True), values, values):
                with self.subTest(option=name, cli=cli, default=file_default, item=item_value):
                    payload = {"files": ["relative.mp4", {"source_path": "override.mp4"}]}
                    if file_default is not missing:
                        payload[name] = file_default
                    if item_value is not missing:
                        payload["files"][1][name] = item_value
                    write_json_atomic(batch_path, payload)
                    defaults = {k: v for k, v in PROCESS_DEFAULTS.items() if k != "progress_enabled"}
                    defaults[key] = cli
                    items = worker.load_batch_items(batch_path, **defaults)
                    inherited = cli or (bool(file_default) if file_default is not missing else False)
                    self.assertEqual(getattr(items[0], key), inherited)
                    self.assertEqual(getattr(items[1], key), inherited if item_value is missing else bool(item_value))
                    self.assertEqual(items[0].source_path, self.root / "relative.mp4")
                    expected = dict(PROCESS_DEFAULTS, source_path=self.root / "override.mp4")
                    expected.pop("progress_enabled")
                    expected[key] = inherited if item_value is missing else bool(item_value)
                    self.assertEqual(asdict(items[1]), expected)

    def test_batch_array_paths_and_validation_errors(self) -> None:
        batch_path = self.root / "batch.json"
        options = {k: v for k, v in PROCESS_DEFAULTS.items() if k != "progress_enabled"}
        absolute = self.root / "absolute.mp4"
        write_json_atomic(batch_path, [str(absolute), {"path": "preferred.mp4", "source_path": "other.mp4"}])
        items = worker.load_batch_items(batch_path, **options)
        self.assertEqual([item.source_path for item in items], [absolute, self.root / "preferred.mp4"])
        for payload, message in (
            ({}, "batch file must be a JSON array or an object with a files array"),
            ([42], "batch items must be strings or objects"),
            ([{}], "batch item is missing path"),
        ):
            with self.subTest(payload=payload):
                write_json_atomic(batch_path, payload)
                with self.assertRaises(RuntimeError) as raised:
                    worker.load_batch_items(batch_path, **options)
                self.assertEqual(str(raised.exception), message)
        with self.assertRaises(RuntimeError) as raised:
            worker.load_batch_items(self.root / "missing.json", **options)
        self.assertIsInstance(raised.exception.__cause__, OSError)

    def test_watch_fallback_only_handles_import_errors_and_keeps_options(self) -> None:
        for error in (None, ImportError("missing"), ValueError("bad input")):
            with (
                self.subTest(error=error),
                patch.object(worker, "watch_with_watchdog", side_effect=error) as watchdog,
                patch.object(worker, "watch_with_polling") as polling,
                patch.object(worker.logging, "info") as log,
            ):
                options = dict.fromkeys(PROCESS_DEFAULTS, True)
                if isinstance(error, ValueError):
                    with self.assertRaises(ValueError) as raised:
                        worker.watch(self.settings, **options)
                    self.assertIs(raised.exception, error)
                else:
                    worker.watch(self.settings, **options)
                watchdog.assert_called_once_with(self.settings, **options)
                if isinstance(error, ImportError):
                    polling.assert_called_once_with(self.settings, **options)
                    log.assert_called_once_with("watchdog is unavailable; falling back to polling")
                else:
                    polling.assert_not_called()
                    log.assert_not_called()


class ManagedCommandContracts(BackendFixture):
    def test_regular_commands_preserve_arguments_and_control_event_order(self) -> None:
        cases = (
            ("generate_segments", "generate_platform_segments", {"platforms": " a, ,b "}, {"platforms": ["a", "b"]}),
            ("generate_metadata", "generate_metadata", {}, {"platform": "douyin"}),
            ("generate_highlights", "generate_highlights", {}, {}),
            ("generate_publish_package", "generate_publish_package", {}, {"platforms": None}),
            ("generate_project_export", "generate_project_exports", {"targets": [" a ", 2, ""]}, {"targets": ["a", "2"], "include_clips": False}),
            ("translate_subtitles", "translate_subtitles", {"target_language": "  "}, {"target_language": "zh"}),
        )
        for command, target, payload, expected in cases:
            for action in (None, "paused", "canceled", "error"):
                with self.subTest(command=command, action=action):
                    events = []
                    failure = ValueError("provider failed")

                    def generate(*args, **kwargs):
                        events.append("generate")
                        if action == "error":
                            raise failure

                    def control():
                        events.append("control")
                        return action if events.count("control") == 2 else None

                    with (
                        patch.object(api, target, side_effect=generate) as call,
                        patch.object(api, "_publish_job_dir_event", side_effect=lambda _: events.append("event")),
                    ):
                        if action:
                            error_type = ValueError if action == "error" else QueueControlRequested
                            with self.assertRaises(error_type) as raised:
                                api._execute_managed_job_command(self.settings, self.job, command, payload, control_callback=control)
                            if action == "error":
                                self.assertIs(raised.exception, failure)
                            else:
                                self.assertEqual(raised.exception.action, action)
                        else:
                            self.assertIsNone(api._execute_managed_job_command(self.settings, self.job, command, payload, control_callback=control))
                    call.assert_called_once_with(self.settings, self.job.job_dir, **expected, force=False)
                    expected_events = ["control", "generate"]
                    if action != "error":
                        expected_events.append("control")
                    if action is None:
                        expected_events.append("event")
                    self.assertEqual(events, expected_events)

    def test_covers_notify_before_final_control_check_even_on_failure(self) -> None:
        for error in (None, ValueError("cover failed")):
            with self.subTest(error=error):
                events = []

                def generate(*args, **kwargs):
                    events.append("generate")
                    if error:
                        raise error

                def control():
                    events.append("control")
                    return "paused" if events.count("control") == 2 else None

                with (
                    patch.object(api, "generate_cover_candidates", side_effect=generate) as call,
                    patch.object(api, "_publish_job_dir_event", side_effect=lambda _: events.append("event")),
                    self.assertRaises(ValueError if error else QueueControlRequested),
                ):
                    api._execute_managed_job_command(
                        self.settings, self.job, "generate_covers",
                        {"title": " Title ", "count": "5", "aspects": ["9:16", 1]}, control_callback=control,
                    )
                call.assert_called_once_with(self.settings, self.job.job_dir, title="Title", style="short_video", count=5, aspects=["9:16", "1"])
                self.assertEqual(events, ["control", "generate", "event"] + ([] if error else ["control"]))

    def test_initial_cancel_prevents_all_command_side_effects(self) -> None:
        commands = (
            "generate_covers", "generate_segments", "generate_metadata", "generate_highlights",
            "generate_publish_package", "generate_project_export", "translate_subtitles",
            "render_highlight", "render_translated_subtitles", "unknown",
        )
        for command in commands:
            with (
                self.subTest(command=command),
                patch.object(api, "write_json_atomic") as write,
                patch.object(api, "_publish_job_dir_event") as publish,
                self.assertRaises(QueueControlRequested) as raised,
            ):
                api._execute_managed_job_command(self.settings, None, command, {}, control_callback=lambda: "canceled")
            self.assertEqual(raised.exception.action, "canceled")
            write.assert_not_called()
            publish.assert_not_called()

    def test_render_states_arguments_and_exception_identity(self) -> None:
        for command, target, description in (
            ("render_highlight", "render_highlight_video", "highlight video"),
            ("render_translated_subtitles", "render_final_video", "translated subtitles"),
        ):
            for failure in (None, ValueError("render failed"), QueueControlRequested("paused"), QueueControlRequested("canceled"), KeyboardInterrupt()):
                with self.subTest(command=command, failure=failure):
                    events, writes = [], []
                    control = Mock(return_value=None)

                    def render(*args, **kwargs):
                        events.append("render")
                        kwargs["resource_wait_callback"]()
                        kwargs["resource_acquired_callback"]()
                        if failure is not None:
                            raise failure

                    def write(path, payload):
                        events.append(payload["status"])
                        writes.append((path.name, payload))

                    with (
                        patch.object(api, target, side_effect=render) as call,
                        patch.object(api, "read_json_file", return_value={"vertical": True}),
                        patch.object(api, "write_json_atomic", side_effect=write),
                        patch.object(api, "_publish_job_dir_event", side_effect=lambda _: events.append("event")),
                        patch.object(api, "datetime") as clock,
                    ):
                        clock.now.return_value.isoformat.return_value = "2026-01-01T00:00:00"
                        payload = {"target_language": " en ", "highlight_cut": {"duration_seconds": 12, "selected_clip_count": 2}}
                        if failure is None:
                            api._execute_managed_job_command(self.settings, self.job, command, payload, control_callback=control)
                        else:
                            with self.assertRaises(type(failure)) as raised:
                                api._execute_managed_job_command(self.settings, self.job, command, payload, control_callback=control)
                            self.assertIs(raised.exception, failure)
                    control.assert_called_once_with()
                    call.assert_called_once()
                    self.assertEqual(call.call_args.args, (self.settings, self.job.job_dir, self.job.source_path))
                    kwargs = dict(call.call_args.kwargs)
                    self.assertIs(kwargs.pop("control_callback"), control)
                    self.assertTrue(callable(kwargs.pop("resource_wait_callback")))
                    self.assertTrue(callable(kwargs.pop("resource_acquired_callback")))
                    expected = {"force": True}
                    base = {"started_at": "2026-01-01T00:00:00"}
                    if command == "render_highlight":
                        filename = "highlight_render_status.json"
                        base.update(output="highlight.mp4", duration_seconds=12, selected_clip_count=2)
                    else:
                        filename = "subtitle_translation_render_en.json"
                        base.update(target_language="en", output="final_translated_en.mp4")
                        expected.update(vertical=True, burn_subtitles=True, subtitle_filename="subtitles_translated_en_clipped.ass", output_filename="final_translated_en.mp4")
                    self.assertEqual(kwargs, expected)
                    expected_writes = [
                        {**base, "status": "rendering", "message": f"Rendering {description}."},
                        {**base, "status": "waiting_for_gpu", "message": f"Waiting for GPU to render {description}."},
                        {**base, "status": "rendering", "message": f"GPU available. Rendering {description}."},
                    ]
                    if failure is None:
                        expected_writes.append({**base, "status": "done", "completed_at": "2026-01-01T00:00:00"})
                    elif isinstance(failure, QueueControlRequested):
                        expected_writes.append({**base, "status": "canceled"})
                    elif isinstance(failure, Exception):
                        expected_writes.append({**base, "status": "failed", "error": str(failure)})
                    self.assertEqual(writes, [(filename, value) for value in expected_writes])
                    self.assertEqual(events, ["rendering", "render", "waiting_for_gpu", "rendering", *[value["status"] for value in expected_writes[3:]], "event"])

    def test_render_status_write_failures_still_notify_and_propagate(self) -> None:
        for fail_status in ("rendering", "done"):
            with self.subTest(status=fail_status):
                states = []
                error = OSError("disk unavailable")

                def write(path, payload):
                    states.append(payload["status"])
                    if payload["status"] == fail_status:
                        raise error

                with (
                    patch.object(api, "render_highlight_video") as render,
                    patch.object(api, "write_json_atomic", side_effect=write),
                    patch.object(api, "_publish_job_dir_event") as publish,
                    self.assertRaises(OSError) as raised,
                ):
                    api._execute_managed_job_command(self.settings, self.job, "render_highlight", {}, control_callback=lambda: None)
                self.assertIs(raised.exception, error)
                self.assertEqual(states, ["rendering", *(["done"] if fail_status == "done" else []), "failed"])
                self.assertEqual(render.call_count, int(fail_status == "done"))
                publish.assert_called_once_with(self.job.job_dir)

    def test_translation_preview_read_errors_remain_outside_status_lifecycle(self) -> None:
        with (
            patch.object(api, "read_json_file", side_effect=ValueError("invalid preview")),
            patch.object(api, "write_json_atomic") as write,
            patch.object(api, "_publish_job_dir_event") as publish,
            self.assertRaisesRegex(ValueError, "invalid preview"),
        ):
            api._execute_managed_job_command(self.settings, self.job, "render_translated_subtitles", {}, control_callback=lambda: None)
        write.assert_not_called()
        publish.assert_not_called()


class QueueDispatchContracts(BackendFixture):
    def test_api_options_share_cli_names_without_changing_defaults(self) -> None:
        for selected in ((), *[(name,) for name in FLAGS], FLAGS):
            with self.subTest(selected=selected):
                settings, options = api._queued_process_config(self.settings, dict.fromkeys(selected, True))
                expected = {**PROCESS_DEFAULTS, "whisper_language": None, "selected_stages": None}
                expected.update({f"{name}_enabled": True for name in selected})
                self.assertEqual(settings, self.settings)
                self.assertEqual(options, expected)

    def test_recipe_payload_and_profile_precedence(self) -> None:
        payload = {
            "recipe_id": "saved", "profile": "fast", "source_integrity_scan": True,
            "detect_freeze": False, "render_final": False, "whisper_language": " en ",
            "recipe_stages": ["ignored"],
            "_recipe_snapshot": {
                "options": {"detect_freeze": True, "plan_uvr": True, "whisper_language": "zh"},
                "stages": ["render_final"],
            },
        }
        with patch.object(api, "automation_repository_for") as repository:
            settings, options = api._queued_process_config(self.settings, payload)
        repository.assert_not_called()
        self.assertFalse(options["detect_freeze_enabled"])
        self.assertTrue(options["plan_uvr_enabled"])
        self.assertTrue(options["render_final_enabled"])
        self.assertEqual(options["whisper_language"], "en")
        self.assertEqual(options["selected_stages"], ["render_final"])
        self.assertTrue(settings.source_integrity_scan_enabled)
        self.assertFalse(settings.web_preview_enabled)
        self.assertEqual(payload["recipe_stages"], ["ignored"])

    def test_queue_retry_executes_once_and_overrides_only_retry_options(self) -> None:
        for retry_stage in (None, "render_final", "unknown"):
            with (
                self.subTest(retry=retry_stage),
                patch.object(api, "load_job", return_value=self.job),
                patch.object(api, "ensure_job_capacity"),
            ):
                with (
                    patch.object(api, "process_job") as process,
                    patch.object(api, "_queue_control_action", return_value="paused") as control,
                ):
                    item = {"id": "queue_test", "job_name": "job", "attempt": 2, "retry_stage": retry_stage, "payload": {"force": False}}
                    if retry_stage == "unknown":
                        with self.assertRaisesRegex(RuntimeError, "unsupported retry stage: unknown"):
                            api._execute_queue_item(self.settings, item)
                        process.assert_not_called()
                        continue
                    self.assertIsNone(api._execute_queue_item(self.settings, item))
                    process.assert_called_once()
                    self.assertEqual(process.call_args.args, (self.settings, self.job))
                    options = dict(process.call_args.kwargs)
                    self.assertEqual(options.pop("control_callback")(), "paused")
                    control.assert_called_once_with(self.settings, "queue_test")
                    expected = {**PROCESS_DEFAULTS, "whisper_language": None, "selected_stages": None}
                    if retry_stage:
                        expected.update(force=True, selected_stages=[retry_stage], expand_selected_dependencies=False, completion_status="needs_review")
                    self.assertEqual(options, expected)

    def test_queue_failure_and_managed_command_branch_remain_distinct(self) -> None:
        with (
            patch.object(api, "load_job", return_value=self.job),
            patch.object(api, "ensure_job_capacity"),
            patch.object(api, "process_job") as process,
            patch.object(api, "_execute_managed_job_command") as managed,
        ):
            self.job.status, self.job.error = "failed", "original error"
            with self.assertRaisesRegex(RuntimeError, "original error"):
                api._execute_queue_item(self.settings, {"job_name": "job"})
            process.assert_called_once()
            process.reset_mock()
            api._execute_queue_item(self.settings, {
                "job_name": "job", "retry_stage": "unknown",
                "payload": {"_command": " generate_highlights ", "_command_payload": {"force": True}},
            })
            process.assert_not_called()
            managed.assert_called_once()
            self.assertEqual(managed.call_args.args, (self.settings, self.job, "generate_highlights", {"force": True}))


class PipelineConstructionContracts(BackendFixture):
    def capture(self, settings=None, **overrides):
        with (
            patch.object(pipeline_executor, "configure_job_logger", return_value=Mock()),
            patch.object(pipeline_executor, "close_job_logger"),
            patch.object(pipeline_executor, "StageRunRepository"),
            patch.object(pipeline_executor, "run_pipeline") as run,
        ):
            result = pipeline_executor.process_job(settings or self.settings, self.job, **{**PROCESS_DEFAULTS, **overrides})
        self.assertIs(result, self.job)
        self.job.fail.assert_not_called()
        run.assert_called_once()
        return run.call_args

    def test_default_stage_order_callbacks_and_dependency_contract(self) -> None:
        call = self.capture()
        stages = call.args[2]
        self.assertEqual([stage.name for stage in stages], list(PIPELINE_STAGE_SPECS))
        self.assertEqual([stage.run.__name__ for stage in stages], [
            "probe_stage", "corruption_stage", "extract_audio_stage", "transcribe_stage",
            "silence_stage", "freeze_stage", "scenes_stage", "cuts_stage", "refine_cuts_stage",
            "crop_stage", "subtitles_stage", "uvr_stage", "render_preview_stage",
            "render_review_stage", "render_final_stage", "render_platform_variants_stage", "render_web_preview_stage",
            "evaluate_highlights_stage", "plan_highlights_stage", "render_highlights_stage",
        ])
        for stage in stages:
            self.assertEqual(stage.status, PIPELINE_STAGE_SPECS[stage.name].status)
            expected = frozenset({"render_review"}) if stage.name == "render_web_preview" else PIPELINE_STAGE_SPECS[stage.name].dependencies
            self.assertEqual(stage.dependencies, expected)
        self.assertEqual(call.args[3].max_parallel_stages, 3)
        self.assertEqual(call.kwargs, {})
        self.job.set_status.assert_called_once_with("needs_review")

    def test_render_crop_and_subtitle_enablement_combinations(self) -> None:
        for final, review, vertical, skip, burn in itertools.product((False, True), repeat=5):
            with self.subTest(final=final, review=review, vertical=vertical, skip=skip, burn=burn):
                call = self.capture(
                    settings=replace(self.settings, web_preview_enabled=True, platform_variants_enabled=True),
                    render_final_enabled=final, render_review_enabled=review, vertical_enabled=vertical,
                    skip_transcribe=skip, burn_subtitles_enabled=burn,
                )
                stages = {stage.name: stage for stage in call.args[2]}
                self.assertEqual(stages["render_final"].enabled, final)
                self.assertEqual(stages["render_review"].enabled, review)
                self.assertEqual(stages["render_web_preview"].enabled, final or review)
                self.assertEqual(stages["render_web_preview"].dependencies, frozenset({"render_final" if final else "render_review"}))
                self.assertEqual(stages["plan_crop"].enabled, vertical)
                self.assertEqual(stages["style_subtitles"].enabled, not skip or burn)
                self.assertTrue(stages["transcribe"].enabled)
                self.job.set_status.assert_called_with("done" if final else "needs_review")

    def test_explicit_reruns_ignore_flags_but_recipes_still_respect_flags(self) -> None:
        control = lambda: None
        for expand in (False, True):
            with self.subTest(expand=expand):
                call = self.capture(selected_stages=["render_final"], expand_selected_dependencies=expand, completion_status="needs_review", control_callback=control)
                selected = {stage.name for stage in call.args[2] if stage.enabled}
                if expand:
                    self.assertEqual(selected, {"probe", "extract_audio", "transcribe", "plan_cuts", "refine_cuts", "style_subtitles", "plan_render"})
                else:
                    self.assertEqual(selected, {"render_final"})
                self.assertEqual(call.kwargs, {"control_callback": control})
                self.job.set_status.assert_called_with("needs_review")

    def test_gpu_claims_and_variant_targets_preserve_short_circuiting(self) -> None:
        for final, variants, targets in itertools.product((False, True), repeat=3):
            with (
                self.subTest(final=final, variants=variants, targets=targets),
                patch.object(pipeline_executor, "transcription_uses_gpu", return_value=True),
                patch.object(pipeline_executor, "rendering_uses_gpu", return_value=True),
                patch.object(pipeline_executor, "platform_variant_targets", return_value=["bilibili"] if targets else []) as target_check,
            ):
                call = self.capture(settings=replace(self.settings, platform_variants_enabled=variants), render_final_enabled=final)
                stages = {stage.name: stage for stage in call.args[2]}
                self.assertEqual(stages["render_platform_variants"].enabled, final and variants and targets)
                self.assertEqual(target_check.call_count, int(final and variants))
                for name, stage in stages.items():
                    self.assertEqual(stage.exclusive_resources, frozenset({"gpu"}) if name in {"transcribe", "render_review", "render_final", "render_platform_variants", "render_web_preview", "render_highlights"} else frozenset())


if __name__ == "__main__":
    unittest.main()
