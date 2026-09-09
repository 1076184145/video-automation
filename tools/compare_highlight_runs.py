"""Read-only comparison of explicitly supplied local unattended-highlight reports.

Does not run LLMs, render media, traverse job directories, or upload anything.
Editorial quality, token usage and billing cannot be inferred from model scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object.")
    return payload


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def summarize_run(directory: Path) -> dict[str, Any]:
    selection = _read(directory / "candidates.json")
    if not selection or not selection.get("fingerprint"):
        raise ValueError("A run must contain candidates.json with an input fingerprint.")
    generation = _read(directory / "generation.json")
    review = _read(directory / "review.json")
    rendered = _read(directory / "index.json")
    if selection.get("generation_digest") != _digest(generation):
        generation = {}
    if selection.get("review_digest") != _digest(review):
        review = {}
    if (rendered.get("fingerprint") != selection["fingerprint"]
            or rendered.get("candidate_digest") != _digest(selection)):
        rendered = {}
    clips = selection.get("candidates", [])
    nodes = generation.get("nodes", [])
    results = review.get("results", [])
    return {
        "source_signature": selection.get("source_signature"),
        "generation_status": selection.get("generation_status", "unknown"),
        "review_status": selection.get("review_status", "unknown"),
        "render_status": rendered.get("status", "unavailable_or_stale"),
        "candidate_pool_count": len(selection.get("raw_candidates", [])),
        "pool_omitted_count": selection.get("pool_omitted_count", 0),
        "selected_count": len(clips),
        "selected_source_seconds": round(sum(c["end"] - c["start"] for c in clips), 3),
        "generation_elapsed_seconds_last_attempt": selection.get("generation_elapsed_seconds"),
        "review_elapsed_seconds_last_attempt": review.get("elapsed_seconds"),
        "analysis_nodes_completed": sum(n.get("status") == "complete" for n in nodes),
        "analysis_nodes_failed": sum(n.get("status") == "failed" for n in nodes),
        "review_passed_count": sum(r.get("status") == "pass" for r in results),
        "review_rejected_count": sum(r.get("status") == "reject" for r in results),
        "rendered_count": sum(c.get("status") == "done" for c in rendered.get("clips", [])),
        "measured_token_usage": None,
        "measured_cost": None,
        "measured_editorial_quality": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, metavar="LABEL=AUTO_CLIPS_DIR")
    args = parser.parse_args(argv)
    runs = {}
    for value in args.run:
        label, separator, directory = value.partition("=")
        if not separator or not label.strip() or not directory or label in runs:
            parser.error("Each --run requires a unique LABEL=AUTO_CLIPS_DIR.")
        try:
            runs[label] = summarize_run(Path(directory))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"Cannot summarize {label}: {type(exc).__name__} (check report files).")
    signatures = [run["source_signature"] for run in runs.values()]
    same_input = bool(signatures) and all(signatures) and len(set(signatures)) == 1
    print(json.dumps({"same_input": bool(same_input), "runs": runs,
                      "notes": ["Node/batch counts are not HTTP request/token/billing counts.",
                                "Times describe the last attempt; cached/resumed runs are not cold benchmarks.",
                                "Blind human review is required to compare usefulness, completeness and manual edits."]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
