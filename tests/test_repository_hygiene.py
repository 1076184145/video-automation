from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".iss",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PUBLIC_DIRECTORIES = (
    ".github",
    "docs",
    "examples",
    "installer",
    "native",
    "tests",
    "tools",
    "video_automation",
    "web",
)
EXCLUDED_PARTS = {
    ".git",
    ".playwright-cli",
    ".pytest_cache",
    ".venv",
    ".workbuddy",
    "__pycache__",
    "build",
    "config",
    "dist",
    "docs/superpowers",
    "logs",
    "logs-runtime",
    "output",
    "processing",
    "venv",
}
REMOVED_DOWNLOADER_PATTERN = re.compile("yt" + r"[-_.]?" + "dlp", re.IGNORECASE)
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return any(
        relative == excluded or relative.startswith(f"{excluded}/")
        for excluded in EXCLUDED_PARTS
    )


def _iter_public_text_files():
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not _is_excluded(path):
            yield path
    for directory_name in PUBLIC_DIRECTORIES:
        directory = ROOT / directory_name
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not _is_excluded(path):
                yield path


def _iter_json_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_json_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_json_strings(item)


class RepositoryHygieneTests(unittest.TestCase):
    def test_beginner_readmes_are_concise(self) -> None:
        line_counts = {
            filename: len((ROOT / filename).read_text(encoding="utf-8").splitlines())
            for filename in ("README.md", "README.zh-CN.md")
        }
        self.assertEqual({name: count for name, count in line_counts.items() if count > 240}, {})

    def test_readme_local_links_exist(self) -> None:
        missing = []
        for filename in ("README.md", "README.zh-CN.md"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(text):
                target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                relative_target = target.split("#", 1)[0]
                if relative_target and not (ROOT / relative_target).exists():
                    missing.append(f"{filename}: {relative_target}")
        self.assertEqual(missing, [])

    def test_removed_downloader_has_no_public_references(self) -> None:
        matches = []
        for path in _iter_public_text_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            if REMOVED_DOWNLOADER_PATTERN.search(text):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_internal_review_reports_are_not_public(self) -> None:
        forbidden = [
            ROOT / "docs" / "reviews" / "FRONTEND_OPTIMIZATION_REPORT.md",
            ROOT / "docs" / "reviews" / "UI_DESIGN_REVIEW.md",
        ]
        self.assertEqual([str(path.relative_to(ROOT)) for path in forbidden if path.exists()], [])

    def test_readmes_do_not_link_internal_review_notes(self) -> None:
        references = []
        internal_review_reference = "docs" + "/reviews"
        for filename in ("README.md", "README.zh-CN.md"):
            if internal_review_reference in (ROOT / filename).read_text(encoding="utf-8"):
                references.append(filename)
        self.assertEqual(references, [])

    def test_batch_example_uses_portable_placeholder_paths(self) -> None:
        payload = json.loads((ROOT / "examples/batch.example.json").read_text(encoding="utf-8"))
        absolute_paths = [value for value in _iter_json_strings(payload) if WINDOWS_ABSOLUTE_PATH.match(value)]
        self.assertEqual(absolute_paths, [])


if __name__ == "__main__":
    unittest.main()
