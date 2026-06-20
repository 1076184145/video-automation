#!/usr/bin/env python3
"""Download Inter and JetBrains Mono from Google Fonts for offline use.

Run from the repository root:

    python tools/download_fonts.py

The script will:
- fetch the Google Fonts CSS for the configured families/weights,
- download the referenced woff2 files into a temporary directory (de-duplicated by URL),
- rewrite the @font-face rules to point at /static/fonts/<file>,
- insert/update those rules at the top of web/css/style.css,
- write a README and license files for the bundled fonts.

All downloads happen in a temporary directory first. Only after every font file
has been downloaded successfully does the script replace the existing web/fonts/
content. If anything fails, the existing fonts and CSS are left untouched.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import textwrap
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = PROJECT_ROOT / "web" / "fonts"
STYLE_CSS = PROJECT_ROOT / "web" / "css" / "style.css"

FONTS_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Inter:wght@400;500;600;700;800"
    "&family=JetBrains+Mono:wght@400;500;700"
    "&display=swap"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

LICENSES = {
    "Inter": {
        "url": "https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt",
        "filename": "Inter-LICENSE.txt",
    },
    "JetBrains Mono": {
        "url": "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/OFL.txt",
        "filename": "JetBrainsMono-OFL.txt",
    },
}


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def slugify_family(name: str) -> str:
    return name.lower().replace(" ", "-")


def _rollback_swap(original_style: str, new_filenames: set[str], backup_dir: Path) -> None:
    """Restore the previous CSS and font files if the swap stage fails.

    Old fonts are copied (not moved) to the backup directory, so they remain in
    `web/fonts/` until the new files are successfully moved into place. If a
    rollback is needed we remove any new/partial files and copy the backups back.
    """
    try:
        # Restore the original CSS.
        STYLE_CSS.write_text(original_style, encoding="utf-8")
        # Remove any new/partial font files that made it into place.
        for font_file in FONTS_DIR.glob("*.woff2"):
            if font_file.name in new_filenames:
                font_file.unlink()
        # Copy the original font files back (overwriting any partial new files).
        for backup_file in backup_dir.glob("*.woff2"):
            shutil.copy2(str(backup_file), str(FONTS_DIR / backup_file.name))
        print("Rollback completed successfully; existing fonts are untouched.")
    except Exception as rollback_exc:  # noqa: BLE001
        print(f"Rollback failed: {rollback_exc}")
        print(f"Please inspect {backup_dir} and {FONTS_DIR} manually.")


def update_style_css(font_css: str) -> None:
    """Insert or replace the offline-font block at the top of style.css."""
    marker_start = "/* BEGIN OFFLINE FONTS */"
    marker_end = "/* END OFFLINE FONTS */"
    wrapper = f"{marker_start}\n{font_css}\n{marker_end}\n\n"

    original_style = STYLE_CSS.read_text(encoding="utf-8") if STYLE_CSS.exists() else ""
    pattern = re.compile(
        re.escape(marker_start) + ".*?" + re.escape(marker_end) + r"\n*",
        re.DOTALL,
    )
    if pattern.search(original_style):
        new_style = pattern.sub(wrapper, original_style)
    else:
        new_style = wrapper + original_style

    STYLE_CSS.write_text(new_style, encoding="utf-8")
    print(f"Updated {STYLE_CSS.relative_to(PROJECT_ROOT)}")


def main() -> int:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching font CSS from {FONTS_CSS_URL}")
    try:
        css = fetch(FONTS_CSS_URL).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to fetch font CSS: {exc}")
        return 1

    # Extract each @font-face block.
    blocks: list[str] = re.findall(r"@font-face\s*\{.*?\}", css, re.DOTALL)
    if not blocks:
        print("No @font-face blocks found in the downloaded CSS.")
        return 1

    # Download all font files into a temporary directory first.
    download_dir = Path(tempfile.mkdtemp(prefix="fonts-dl-", dir=FONTS_DIR))
    backup_dir = Path(tempfile.mkdtemp(prefix="fonts-bak-", dir=FONTS_DIR))

    try:
        family_counters: dict[str, int] = {}
        url_to_filename: dict[str, str] = {}
        updated_blocks: list[str] = []

        for block in blocks:
            family_match = re.search(r"font-family:\s*['\"]?([^';\"]+)['\"]?", block)
            if not family_match:
                print(f"Skipping block without font-family: {block[:120]!r}")
                continue
            family = family_match.group(1).strip()
            slug = slugify_family(family)

            urls = re.findall(r"url\(([^)]+)\)", block)
            if not urls:
                print(f"Skipping block without src URL: {block[:120]!r}")
                continue

            font_url = urls[0].strip().strip('"\'')

            if font_url in url_to_filename:
                filename = url_to_filename[font_url]
                print(f"Reusing {family} -> {filename}")
            else:
                index = family_counters.get(slug, 0)
                filename = f"{slug}-{index}.woff2"
                family_counters[slug] = index + 1
                url_to_filename[font_url] = filename

                print(f"Downloading {family} -> {filename} ({font_url[:80]}...)")
                font_data = fetch(font_url)
                (download_dir / filename).write_bytes(font_data)

            new_block = re.sub(
                r"url\([^)]+\)",
                f"url('/static/fonts/{filename}')",
                block,
            )
            updated_blocks.append(new_block)

        font_css = "\n".join(updated_blocks)
        new_filenames = set(url_to_filename.values())

        # Preserve the existing CSS so we can roll back if the swap fails.
        original_style = STYLE_CSS.read_text(encoding="utf-8") if STYLE_CSS.exists() else ""

        # Copy existing font files to the backup directory. Using copy2 keeps
        # the originals in place until the swap is fully successful.
        try:
            for old_file in FONTS_DIR.glob("*.woff2"):
                shutil.copy2(str(old_file), str(backup_dir / old_file.name))
        except Exception as exc:  # noqa: BLE001
            print(f"Backup of existing fonts failed ({exc}); no changes made.")
            return 1

        # Move the newly downloaded files into place and update CSS.
        try:
            for new_file in download_dir.glob("*.woff2"):
                shutil.move(str(new_file), str(FONTS_DIR / new_file.name))

            update_style_css(font_css)
        except Exception as exc:  # noqa: BLE001
            print(f"Swap failed ({exc}); rolling back to previous fonts and CSS...")
            _rollback_swap(original_style, new_filenames, backup_dir)
            return 1

    except Exception as exc:  # noqa: BLE001
        print(f"Font download failed, leaving existing fonts untouched: {exc}")
        return 1
    finally:
        # Clean up temporary directories.
        shutil.rmtree(download_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)

    # Write a README for the fonts directory.
    readme = FONTS_DIR / "README.md"
    readme.write_text(
        textwrap.dedent(
            f"""\
            # Bundled Fonts

            These fonts are bundled for offline / desktop use.

            | Font | Weights requested | License |
            |------|-------------------|---------|
            | Inter | 400, 500, 600, 700, 800 | SIL Open Font License 1.1 |
            | JetBrains Mono | 400, 500, 700 | SIL Open Font License 1.1 |

            - Inter: https://github.com/rsms/inter
            - JetBrains Mono: https://github.com/JetBrains/JetBrainsMono

            See `Inter-LICENSE.txt` and `JetBrainsMono-OFL.txt` for the full license text.

            Generated by `tools/download_fonts.py`.
            """
        ),
        encoding="utf-8",
    )
    print(f"Wrote {readme.relative_to(PROJECT_ROOT)}")

    # Download license files.
    for family, info in LICENSES.items():
        try:
            data = fetch(info["url"])
            path = FONTS_DIR / info["filename"]
            path.write_bytes(data)
            print(f"Wrote {path.relative_to(PROJECT_ROOT)}")
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: could not download license for {family}: {exc}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
