from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "auto-version.yml"


class AutoVersionWorkflowTests(unittest.TestCase):
    def test_package_version_command_returns_one_semver_line(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from video_automation import __version__; print(__version__)",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertRegex(result.stdout, r"^\d+\.\d+\.\d+\n$")

    def test_workflow_does_not_grep_every_version_reference(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertNotIn("grep '__version__'", workflow)
        self.assertIn("from video_automation import __version__", workflow)
        self.assertIn("Invalid package version", workflow)
        self.assertIsNotNone(re.search(r"current_version=%s\\n", workflow))


if __name__ == "__main__":
    unittest.main()
