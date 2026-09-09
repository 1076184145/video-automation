from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .crop import generate_vertical_crop_plan
from .highlight_checker import CHECKER_VERSION, LLMHighlightChecker
from .highlight_edits import build_highlight_edit
from .highlight_graph import GRAPH_VERSION, HighlightGraph, merge_candidate_pool, select_reviewed_candidates
from .io_utils import read_json_file, write_json_atomic
from .llm_evaluator import LLMClipEvaluator, check_control, normalize_sentences, review_candidate_limit, temporal_nms
from .render import render_highlight_edit, valid_highlight_output
from .runtime_config import snapshot_runtime_settings
from .subtitles import write_highlight_ass
from .task_queue import QueueControlRequested


SCHEMA_VERSION = 3


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


class UnattendedHighlights:
    """Durable, explicitly opted-in pipeline; never touches legacy cuts/final MP4."""
    def __init__(self, settings: Settings, job_dir: Path, source_path: Path, *, force: bool = False,
                 control_callback: Callable[[], str | None] | None = None) -> None:
        self.settings, self.job_dir, self.source_path = settings, job_dir, source_path
        self.force, self.control_callback = force, control_callback
        self.directory = job_dir / "auto_clips"

    def _fingerprint(self) -> str:
        source = self.source_path.stat()
        artifacts = {name: hashlib.sha256((self.job_dir / name).read_bytes()).hexdigest()
                     for name in ("transcript.json", "silence.json", "manifest.json")}
        audio = (self.job_dir / "audio.wav").stat()
        return _digest({"schema": SCHEMA_VERSION, "source": [str(self.source_path.resolve()), source.st_size, source.st_mtime_ns],
                        "audio": [audio.st_size, audio.st_mtime_ns], "artifacts": artifacts,
                        "settings": snapshot_runtime_settings(self.settings)["revision"]})

    def evaluate(self) -> dict[str, Any]:
        check_control(self.control_callback)
        self.directory.mkdir(parents=True, exist_ok=True)
        fingerprint = self._fingerprint()
        cached = read_json_file(self.directory / "candidates.json") or {}
        if (not self.force and cached.get("fingerprint") == fingerprint and self._review_is_current(cached)
                and self._generation_is_current(cached) and cached.get("generation_status") != "partial"):
            return cached
        transcript = read_json_file(self.job_dir / "transcript.json") or {}
        sentences = normalize_sentences(transcript.get("segments") or [])
        if not sentences:
            raise ValueError("Unattended highlights require a nonempty transcript; disable skip_transcribe.")
        generation_started = time.monotonic()
        generation = {"generation_status": "single", "pool_omitted_count": 0}
        reusable = (not self.force and cached.get("fingerprint") == fingerprint and "raw_candidates" in cached
                    and self._generation_is_current(cached) and cached.get("generation_status") != "partial")
        if reusable:
            candidates = cached["raw_candidates"]
            rejected_count = cached["rejected_candidates"]
            generation = {k: cached[k] for k in ("generation_status", "generation_digest", "generation_elapsed_seconds", "pool_omitted_count") if k in cached}
        elif self.settings.highlight_graph_enabled:
            # A forced/partial re-evaluation must not leave an older selection
            # available while a new graph is running or has failed.
            pending = {"schema_version": SCHEMA_VERSION, "fingerprint": fingerprint, "sentences": sentences,
                       "candidates": [], "generation_status": "running", "review_status": "pending"}
            write_json_atomic(self.directory / "candidates.json", pending)
            previous = read_json_file(self.directory / "generation.json") or {}
            if self.force or previous.get("fingerprint") != fingerprint or previous.get("graph_version") != GRAPH_VERSION:
                previous = {}

            def save_generation(report: dict[str, Any]) -> None:
                write_json_atomic(self.directory / "generation.json", {**report, "fingerprint": fingerprint})

            try:
                generated = HighlightGraph(self.settings, control_callback=self.control_callback).generate(
                    sentences, previous=previous, on_update=save_generation,
                )
            except QueueControlRequested as exc:
                pending["generation_status"] = exc.action
                write_json_atomic(self.directory / "candidates.json", pending)
                raise
            except Exception:
                pending["generation_status"] = "failed"
                write_json_atomic(self.directory / "candidates.json", pending)
                raise
            candidates, rejected_count = generated["candidates"], generated["rejected_count"]
            generation = {"generation_status": generated["status"], "pool_omitted_count": generated["pool_omitted_count"],
                          "generation_digest": _digest({**generated, "fingerprint": fingerprint})}
        else:
            evaluator = LLMClipEvaluator(self.settings, control_callback=self.control_callback)
            if self.settings.highlight_llm_checker_enabled:
                candidates, omitted = merge_candidate_pool(evaluator.extract_candidates(sentences), review_candidate_limit(self.settings))
                generation["pool_omitted_count"] = omitted
            else:
                candidates = evaluator.extract_highlights(sentences)
            rejected_count = evaluator.rejected_count
        if not reusable:
            generation["generation_elapsed_seconds"] = round(time.monotonic() - generation_started, 3)
        source_stat = self.source_path.stat()
        payload = {"schema_version": SCHEMA_VERSION, "fingerprint": fingerprint, "sentences": sentences,
                   "candidates": candidates, "raw_candidates": candidates, "rejected_candidates": rejected_count,
                   "review_status": "disabled", **generation,
                   "source_signature": _digest([str(self.source_path.resolve()), source_stat.st_size, source_stat.st_mtime_ns, sentences])}
        check_control(self.control_callback)
        approved = {f"candidate-{i + 1}" for i in range(len(candidates))}
        if self.settings.highlight_llm_checker_enabled:
            # Publish a blocking checkpoint before reviewing (including --force),
            # so a failed review cannot expose a previous run's approved selection.
            payload.update(raw_candidates=candidates, candidates=[], review_status="pending")
            write_json_atomic(self.directory / "candidates.json", payload)
            input_digest = self._review_input_digest(payload)
            previous = read_json_file(self.directory / "review.json") or {}
            if self.force or previous.get("input_digest") != input_digest or previous.get("checker_version") != CHECKER_VERSION:
                previous = {}

            def save_review(report: dict[str, Any]) -> None:
                write_json_atomic(self.directory / "review.json", {**report, "input_digest": input_digest})

            try:
                report = LLMHighlightChecker(self.settings, control_callback=self.control_callback).review(
                    candidates, sentences, previous=previous, on_update=save_review,
                )
            except QueueControlRequested as exc:
                payload["review_status"] = exc.action
                write_json_atomic(self.directory / "candidates.json", payload)
                raise
            except Exception:
                payload["review_status"] = "failed"
                write_json_atomic(self.directory / "candidates.json", payload)
                raise
            approved = {item["candidate_id"] for item in report["results"] if item["status"] == "pass"}
            payload.update(review_status="complete", review_digest=_digest({**report, "input_digest": input_digest}))
        payload["candidates"], payload["selection"] = select_reviewed_candidates(candidates, approved, self.settings.highlight_max_clips)
        check_control(self.control_callback)
        write_json_atomic(self.directory / "candidates.json", payload)
        return payload

    @staticmethod
    def _review_input_digest(payload: dict[str, Any]) -> str:
        return _digest({"fingerprint": payload["fingerprint"], "checker_version": CHECKER_VERSION,
                        "sentences": payload["sentences"], "candidates": payload.get("raw_candidates", [])})

    def _review_is_current(self, payload: dict[str, Any]) -> bool:
        if not self.settings.highlight_llm_checker_enabled:
            return True
        report = read_json_file(self.directory / "review.json") or {}
        return (payload.get("review_status") == "complete" and report.get("status") == "complete"
                and report.get("checker_version") == CHECKER_VERSION
                and report.get("input_digest") == self._review_input_digest(payload)
                and payload.get("review_digest") == _digest(report))

    def _generation_is_current(self, payload: dict[str, Any]) -> bool:
        if not self.settings.highlight_graph_enabled:
            return True
        report = read_json_file(self.directory / "generation.json") or {}
        return (payload.get("generation_status") in {"complete", "partial"}
                and report.get("status") == payload["generation_status"]
                and report.get("graph_version") == GRAPH_VERSION
                and report.get("fingerprint") == payload.get("fingerprint")
                and payload.get("generation_digest") == _digest(report))

    def plan(self) -> dict[str, Any]:
        check_control(self.control_callback)
        candidates = self._current("candidates.json")
        cached = read_json_file(self.directory / "edits.json") or {}
        candidate_digest = _digest(candidates)
        if not self.force and cached.get("candidate_digest") == candidate_digest:
            return cached
        manifest = read_json_file(self.job_dir / "manifest.json") or {}
        duration = float(manifest.get("duration_seconds") or 0)
        silences = (read_json_file(self.job_dir / "silence.json") or {}).get("silences") or []
        edits, rejected = [], []
        for candidate in candidates["candidates"]:
            check_control(self.control_callback)
            try:
                edits.append(build_highlight_edit(candidate, candidates["sentences"], silences,
                                                  self.job_dir / "audio.wav", source_duration=duration, fps=30))
            except ValueError as exc:
                rejected.append({"start_id": candidate["start_id"], "end_id": candidate["end_id"], "reason": str(exc)})
        payload = {"schema_version": SCHEMA_VERSION, "fingerprint": candidates["fingerprint"],
                   "candidate_digest": candidate_digest, "edits": temporal_nms(edits), "rejected": rejected}
        write_json_atomic(self.directory / "edits.json", payload)
        return payload

    def _current(self, filename: str) -> dict[str, Any]:
        payload = read_json_file(self.directory / filename) or {}
        if payload.get("fingerprint") != self._fingerprint():
            raise ValueError("Highlight inputs/configuration changed or planning is missing; rerun evaluate_highlights and plan_highlights.")
        if filename == "candidates.json" and not self._review_is_current(payload):
            raise ValueError("Highlight review is incomplete or changed; rerun evaluate_highlights before planning/rendering.")
        if filename == "candidates.json" and not self._generation_is_current(payload):
            raise ValueError("Highlight generation is incomplete or changed; rerun evaluate_highlights before planning/rendering.")
        return payload

    def render(self, *, progress_callback: Callable[[float], None] | None = None,
               resource_wait_callback: Callable[[], None] | None = None,
               resource_acquired_callback: Callable[[], None] | None = None) -> dict[str, Any]:
        plan = self._current("edits.json")
        candidates = self._current("candidates.json")
        if plan["candidate_digest"] != _digest(candidates):
            raise ValueError("Candidate selection changed; rerun plan_highlights.")
        report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "fingerprint": plan["fingerprint"],
                                  "status": "running", "clips": [], "rejected": plan["rejected"],
                                  "review_status": candidates.get("review_status", "disabled"),
                                  "generation_status": candidates.get("generation_status", "single"),
                                  "candidate_digest": _digest(candidates),
                                  "pool_omitted_count": candidates.get("pool_omitted_count", 0)}
        if self.settings.highlight_llm_checker_enabled:
            report["review_file"] = "auto_clips/review.json"
        if self.settings.highlight_graph_enabled:
            report["generation_file"] = "auto_clips/generation.json"
        report_path = self.directory / "index.json"
        write_json_atomic(report_path, report)
        manifest = read_json_file(self.job_dir / "manifest.json") or {}
        for index, edit in enumerate(plan["edits"]):
            try:
                check_control(self.control_callback)
            except QueueControlRequested as exc:
                report["status"] = exc.action
                write_json_atomic(report_path, report)
                raise
            revision = _digest({"edit": edit, "fingerprint": plan["fingerprint"]})
            # Stable generated names; LLM titles never enter filesystem paths.
            clip_id = f"{index + 1:02d}-{revision[:16]}"
            clip_dir = self.directory / clip_id
            clip_dir.mkdir(parents=True, exist_ok=True)
            status = {"id": clip_id, "title": edit["title"], "hook_score": edit["hook_score"],
                      "duration": edit["duration"], "status": "running", "revision": revision}
            report["clips"].append(status)
            write_json_atomic(report_path, report)
            previous = read_json_file(clip_dir / "result.json") or {}

            def clip_progress(percent: float) -> None:
                if progress_callback:
                    progress_callback((index * 100 + max(0., min(100., percent))) / len(plan["edits"]))

            try:
                if (not self.force and previous.get("revision") == revision and previous.get("status") == "done"
                        and valid_highlight_output(self.settings, clip_dir / "final.mp4", edit["duration"])):
                    status.update(previous)
                else:
                    write_json_atomic(clip_dir / "manifest.json", manifest)
                    write_json_atomic(clip_dir / "edit.json", edit)
                    generate_vertical_crop_plan(replace(self.settings, vertical_mode="crop", crop_anchor_x=.5, crop_anchor_y=.5), clip_dir, force=True)
                    status["subtitle_mode"] = write_highlight_ass(self.settings, candidates["sentences"], edit["spans"], clip_dir / "subtitles.ass")
                    render_highlight_edit(self.settings, self.source_path, clip_dir, edit,
                                          progress_callback=clip_progress, resource_wait_callback=resource_wait_callback,
                                          resource_acquired_callback=resource_acquired_callback, control_callback=self.control_callback)
                    status.update(status="done", file=f"auto_clips/{clip_id}/final.mp4")
                check_control(self.control_callback)
            except QueueControlRequested as exc:
                status["status"] = exc.action
                report["status"] = exc.action
                write_json_atomic(clip_dir / "result.json", status)
                write_json_atomic(report_path, report)
                raise
            except Exception as exc:
                status.update(status="failed", error=str(exc)[:1200])
            write_json_atomic(clip_dir / "result.json", status)
            write_json_atomic(report_path, report)
            clip_progress(100.)
        completed = sum(item["status"] == "done" for item in report["clips"])
        report["status"] = ("no_qualifying_clips" if not report["clips"] else
                            "done" if completed == len(report["clips"]) else "partial" if completed else "failed")
        if report["generation_status"] == "partial" and report["status"] in {"done", "no_qualifying_clips"}:
            report["status"] = "partial"
        write_json_atomic(report_path, report)
        if report["status"] == "failed":
            raise RuntimeError("All automatic clips failed to render; see auto_clips/index.json.")
        return report
