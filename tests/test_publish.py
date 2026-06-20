from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.config import Settings
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.publish import generate_publish_package


class PublishPackageTests(unittest.TestCase):
    def test_publish_package_writes_extension_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir) / "job-with-title"
            job_dir.mkdir()
            (job_dir / "final.mp4").write_bytes(b"fake video")
            write_json_atomic(job_dir / "manifest.json", {"duration_seconds": 42, "width": 1920, "height": 1080})
            write_json_atomic(job_dir / "metadata.json", {
                "titles": ["A useful title"],
                "descriptions": ["A useful description"],
                "tags": ["video", "automation"],
                "hashtags": ["demo"],
            })

            package = generate_publish_package(Settings.load(), job_dir, platforms=["douyin", "bilibili"], force=True)
            extension = read_json_file(job_dir / "publish_extension_manifest.json")

            self.assertEqual(package["publish_extension"]["protocol_version"], 1)
            self.assertIsNotNone(extension)
            self.assertEqual(extension["protocol_version"], 1)
            self.assertEqual([item["platform"] for item in extension["platforms"]], ["douyin", "bilibili"])
            self.assertIn("creator.douyin.com", extension["platforms"][0]["uploader_url"])
            self.assertIn("member.bilibili.com", extension["platforms"][1]["uploader_url"])
            self.assertEqual(extension["platforms"][0]["fields"]["title"], "A useful title")
            self.assertTrue((job_dir / "publish_packages" / "douyin" / "platform_metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
