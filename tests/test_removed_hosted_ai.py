from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HostedAiRemovalTests(unittest.TestCase):
    def test_hosted_ai_usage_and_waitlist_surfaces_are_removed(self) -> None:
        checks = {
            ROOT / "web" / "js" / "app.js": (
                "#/usage",
                "renderUsage",
                "hosted-ai-waitlist",
            ),
            ROOT / "web" / "js" / "api.js": (
                "joinHostedAiWaitlist",
                "/usage/waitlist",
            ),
            ROOT / "video_automation" / "api.py": (
                "/usage/waitlist",
                "_join_ai_waitlist",
                "_normalize_waitlist_entry",
                "_append_waitlist_entry",
                "ai_waitlist.json",
                "usage_waitlist",
            ),
            ROOT / "web" / "css" / "style.css": (
                ".waitlist-form",
            ),
            ROOT / "web" / "js" / "i18n.js": (
                '"nav.usage"',
                '"usage.title"',
                '"usage.official_title"',
                '"usage.join_waitlist"',
            ),
            ROOT / "web" / "js" / "i18n-zh.js": (
                '"nav.usage"',
                '"usage.title"',
                '"usage.official_title"',
                '"usage.join_waitlist"',
            ),
            ROOT / "web" / "js" / "i18n-en.js": (
                '"nav.usage"',
                '"usage.title"',
                '"usage.official_title"',
                '"usage.join_waitlist"',
            ),
        }

        leftovers: list[str] = []
        for path, markers in checks.items():
            text = path.read_text(encoding="utf-8")
            leftovers.extend(
                f"{path.relative_to(ROOT)} contains {marker}"
                for marker in markers
                if marker in text
            )

        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
