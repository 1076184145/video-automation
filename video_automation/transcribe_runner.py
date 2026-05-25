from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .transcribe import transcribe_audio_faster_whisper, transcribe_audio_funasr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated transcription runner")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--backend", choices=["faster-whisper", "funasr"], default="faster-whisper")
    parser.add_argument("--language", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.language:
        settings = replace(settings, whisper_language=args.language)
    if args.model:
        settings = replace(settings, whisper_model=args.model)

    job_dir = args.job_dir
    output_paths = (job_dir / "transcript.txt", job_dir / "transcript.srt", job_dir / "transcript.json")
    if args.backend == "funasr":
        transcribe_audio_funasr(settings, args.audio, *output_paths)
    else:
        transcribe_audio_faster_whisper(settings, args.audio, *output_paths)
    # Some GPU transcription stacks can crash during Python shutdown on Windows
    # after outputs are already safely written. Exit immediately from this child
    # so the parent worker can keep running and treat the stage as successful.
    os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
