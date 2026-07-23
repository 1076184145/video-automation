from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTO_VERSION_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "auto-version.yml"
RELEASE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"


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
        workflow = AUTO_VERSION_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertNotIn("grep '__version__'", workflow)
        self.assertIn("from video_automation import __version__", workflow)
        self.assertIn("Invalid package version", workflow)
        self.assertIsNotNone(re.search(r"current_version=%s\\n", workflow))

    def test_auto_version_explicitly_dispatches_desktop_release(self) -> None:
        workflow = AUTO_VERSION_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertRegex(workflow, r"(?m)^  actions: write$")
        self.assertIn("id: release_tag", workflow)
        self.assertIn("created=true", workflow)
        self.assertIn("steps.release_tag.outputs.created == 'true'", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn(
            'gh workflow run release.yml --ref main -f tag="$RELEASE_TAG"',
            workflow,
        )

    def test_release_workflow_accepts_and_builds_an_explicit_tag(self) -> None:
        workflow = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8-sig")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertRegex(
            workflow,
            r'(?m)^      RELEASE_TAG: \$\{\{ inputs\.tag \|\| github\.ref_name \}\}$',
        )
        self.assertIn("Invalid release tag", workflow)
        self.assertIn("ref: ${{ env.RELEASE_TAG }}", workflow)
        self.assertIn("tag_name: ${{ env.RELEASE_TAG }}", workflow)
        self.assertIn("VideoAutomationLite-${{ env.RELEASE_VERSION }}.zip", workflow)
        self.assertIn(
            "VideoAutomationLite-Setup-${{ env.RELEASE_VERSION }}.exe",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
