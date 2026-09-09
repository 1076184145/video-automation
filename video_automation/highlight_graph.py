from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .llm_evaluator import (
    SYSTEM_PROMPT, LLMClipEvaluator, _schema, check_control, request_highlight_json,
    review_candidate_limit, temporal_iou, validate_sentence_segments,
)
from .provider_errors import ProviderRequestError
from .task_queue import QueueControlRequested


GRAPH_VERSION = 1
FOCUSES = {
    "hook": "Focus on a specific useful insight, a strong question, a surprising reversal or a memorable conclusion.",
    "emotion": "Focus on emotion explicitly supported by the words. Do not invent laughter, vocal tone, facial expressions or visual reactions.",
    "conflict": "Focus on an explicit disagreement, a revealed fact, or a challenge with an explained outcome. Do not invent conflict.",
}
PER_NODE_LIMIT = 8


def effective_graph_concurrency(settings: Settings) -> int:
    providers = {settings.llm_provider.strip().lower(), settings.llm_fallback_provider.strip().lower()}
    host = urlparse(settings.llm_openai_base_url).hostname
    # Managed llama-server is locked/--parallel 1. Loopback compatible servers
    # (e.g. Ollama) and a possible local fallback also default to one request.
    if "local" in providers or ("openai" in providers and host in {"localhost", "127.0.0.1", "::1"}):
        return 1
    return max(1, min(3, settings.highlight_graph_concurrency))


def merge_candidate_pool(candidates: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int]:
    """Merge exact range+title duplicates only; defer temporal NMS until review.

    Different titles for the same range stay separate: a faithful title must not
    disappear just because a higher-scoring, misleading alternative exists.
    """
    merged: dict[tuple[int, int, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (candidate["start_id"], candidate["end_id"], candidate["title"])
        previous = merged.get(key)
        origins = set(candidate.get("sources", [])) | set((previous or {}).get("sources", []))
        if previous is None or candidate["hook_score"] > previous["hook_score"]:
            merged[key] = {**candidate, "sources": sorted(origins)}
        else:
            previous["sources"] = sorted(origins)
    ranked = sorted(merged.values(), key=lambda c: (-c["hook_score"], c["start"], c["end"], c["title"]))
    return ranked[:limit], max(0, len(ranked) - limit)


def select_reviewed_candidates(
    candidates: list[dict[str, Any]], approved_ids: set[str], limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    decisions = []
    ranked = sorted(enumerate(candidates), key=lambda pair: (
        -pair[1]["hook_score"], pair[1]["start"], pair[1]["end"], pair[1]["title"], pair[0],
    ))
    for i, candidate in ranked:
        ident = f"candidate-{i + 1}"
        decision = {"candidate_id": ident, "start_id": candidate["start_id"], "end_id": candidate["end_id"]}
        if ident not in approved_ids:
            decision["status"] = "review_rejected"
        else:
            duplicate = next((kept_id for kept_id, kept in selected if temporal_iou(candidate, kept) > .5), None)
            if duplicate is not None:
                decision.update(status="overlap", kept_candidate_id=duplicate)
            elif len(selected) >= limit:
                decision["status"] = "limit"
            else:
                selected.append((ident, candidate))
                decision["status"] = "selected"
        decisions.append(decision)
    return [candidate for _, candidate in selected], decisions


class HighlightGraph:
    """Bounded fan-out/fan-in over full overlapping sentence windows.

    Only the coordinator writes checkpoints or polls the queue control callback.
    Nodes return immutable results, with a cooperative stop signal for in-flight
    requests. A failed node cannot masquerade as a successful empty response.
    """

    def __init__(self, settings: Settings, *, control_callback: Callable[[], str | None] | None = None) -> None:
        self.settings, self.control_callback = settings, control_callback

    def generate(
        self, sentences: list[dict[str, Any]], *, previous: dict[str, Any] | None = None,
        on_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        report: dict[str, Any] = {"graph_version": GRAPH_VERSION, "status": "running", "nodes": [],
                                  "candidates": [], "error_code": "", "concurrency": effective_graph_concurrency(self.settings)}
        stopping = threading.Event()
        stop_action = "canceled"

        def control() -> str | None:
            return stop_action if stopping.is_set() else None

        def save() -> None:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if on_update is not None:
                on_update(copy.deepcopy(report))

        try:
            check_control(self.control_callback)
            ordered = validate_sentence_segments(sentences)
            evaluator = LLMClipEvaluator(self.settings)
            reserve = len(SYSTEM_PROMPT) + max(map(len, FOCUSES.values())) + 160
            windows = evaluator.windows(ordered, reserved_chars=reserve)
            prompts = [json.dumps({"sentences": [{k: s[k] for k in ("id", "start", "end", "text")} for s in window]}, ensure_ascii=False)
                       for window in windows]
            previous_nodes = (previous or {}).get("nodes") or []
            work: list[tuple[dict[str, Any], list[dict[str, Any]], str]] = []
            for i, window in enumerate(windows):
                for focus in FOCUSES:
                    digest = hashlib.sha256((prompts[i] + focus + str(GRAPH_VERSION)).encode()).hexdigest()
                    node = {"id": f"w{i + 1:03d}-{focus}", "focus": focus, "window": i + 1,
                            "start_id": window[0]["id"], "end_id": window[-1]["id"], "input_digest": digest,
                            "status": "pending", "candidates": [], "rejected_count": 0, "error_code": "",
                            "elapsed_seconds": 0., "prompt_chars": len(prompts[i]) + len(SYSTEM_PROMPT) + len(FOCUSES[focus])}
                    # The caller additionally checks the run's input/config digest.
                    matches = [old for old in previous_nodes if isinstance(old, dict) and old.get("id") == node["id"]
                               and old.get("input_digest") == digest and old.get("status") == "complete"]
                    if len(matches) == 1 and self._valid_checkpoint(matches[0], window, evaluator):
                        node.update(copy.deepcopy(matches[0]), reused=True)
                    else:
                        work.append((node, window, prompts[i]))
                    report["nodes"].append(node)
            save()
            # Do not enqueue hundreds of futures: keep at most concurrency nodes
            # submitted, and refill only after a completed result is persisted.
            executor = ThreadPoolExecutor(max_workers=report["concurrency"], thread_name_prefix="highlight")
            futures: dict[Future, dict[str, Any]] = {}
            cursor = 0
            try:
                while cursor < len(work) or futures:
                    check_control(self.control_callback)
                    while cursor < len(work) and len(futures) < report["concurrency"]:
                        node, window, prompt = work[cursor]
                        node["status"] = "running"
                        futures[executor.submit(self._run_node, node["focus"], window, prompt, control)] = node
                        cursor += 1
                    done, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        node = futures.pop(future)
                        node.update(future.result())
                        save()
                    check_control(self.control_callback)
            except BaseException as exc:
                if isinstance(exc, QueueControlRequested):
                    stop_action = exc.action
                stopping.set()
                for future in futures:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
            complete = [node for node in report["nodes"] if node["status"] == "complete"]
            if report["nodes"] and not complete:
                report.update(status="failed", error_code="all_analyzers_failed")
                raise RuntimeError("All highlight analyzers failed; inspect auto_clips/generation.json and retry.")
            candidates = [{**candidate, "sources": [node["id"]]} for node in complete for candidate in node["candidates"]]
            pool, omitted = merge_candidate_pool(candidates, review_candidate_limit(self.settings))
            report.update(status="complete" if len(complete) == len(report["nodes"]) else "partial",
                          candidates=pool, pool_omitted_count=omitted,
                          rejected_count=sum(node["rejected_count"] for node in report["nodes"]),
                          completed_nodes=len(complete), failed_nodes=len(report["nodes"]) - len(complete))
            save()
            return report
        except QueueControlRequested as exc:
            report["status"] = exc.action
            for node in report["nodes"]:
                if node["status"] in {"pending", "running"}:
                    node["status"] = exc.action
            save()
            raise
        except Exception:
            report.update(status="failed", error_code=report["error_code"] or "generation_failed")
            save()
            raise

    @staticmethod
    def _valid_checkpoint(node: dict[str, Any], window: list[dict[str, Any]], evaluator: LLMClipEvaluator) -> bool:
        values = node.get("candidates")
        return (isinstance(values, list) and len(values) <= PER_NODE_LIMIT
                and type(node.get("rejected_count")) is int and node["rejected_count"] >= 0
                and all(evaluator.validate_candidate(value, window) == value for value in values))

    def _run_node(self, focus: str, window: list[dict[str, Any]], prompt: str,
                  control: Callable[[], str | None]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            schema = _schema()
            schema["properties"]["clips"]["maxItems"] = PER_NODE_LIMIT
            payload = request_highlight_json(
                self.settings, system=f"{SYSTEM_PROMPT}\n{FOCUSES[focus]}\nReturn at most {PER_NODE_LIMIT} candidates.",
                user=prompt, schema=schema, schema_name=f"highlight_{focus}", control_callback=control,
            )
            if not isinstance(payload.get("clips"), list) or len(payload["clips"]) > PER_NODE_LIMIT:
                raise ProviderRequestError("LLM", "highlight analysis", "response_invalid", "Invalid candidate array.")
            evaluator = LLMClipEvaluator(self.settings)
            candidates = [valid for raw in payload["clips"] if (valid := evaluator.validate_candidate(raw, window)) is not None]
            return {"status": "complete", "candidates": candidates, "rejected_count": len(payload["clips"]) - len(candidates),
                    "error_code": "", "elapsed_seconds": round(time.monotonic() - started, 3)}
        except QueueControlRequested:
            raise
        except Exception as exc:
            return {"status": "failed", "candidates": [], "rejected_count": 0,
                    "error_code": exc.code if isinstance(exc, ProviderRequestError) else "analyzer_internal_error",
                    "elapsed_seconds": round(time.monotonic() - started, 3)}
