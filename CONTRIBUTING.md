# Contributing

Thanks for helping improve Video Automation.

## Before You Start

- Open an issue for larger changes so the scope can be discussed first.
- Keep pull requests focused. Small, reviewable changes are much easier to merge.
- Do not commit private files, API keys, `.env`, generated videos, job outputs, logs, or large media assets.
- Use `README.zh-CN.md` as the primary Chinese user guide and keep `README.md` understandable for international users.

## Local Checks

Run the basic checks that match your change:

```powershell
python -m compileall video_automation run_worker.py
python -m unittest discover -s tests
node --check web/js/app.js
```

For frontend changes, also open the local Web UI and manually verify the affected page:

```powershell
python run_worker.py --serve
```

Then visit:

```text
http://127.0.0.1:8765/#/
```

## Pull Request Checklist

- Explain what changed and why.
- Mention any new configuration values.
- Update `README.md` and `README.zh-CN.md` when user-facing behavior changes.
- Add or update tests for core pipeline logic when practical.
- Confirm that no private paths, keys, or generated media files are included.

## Reporting Bugs

When reporting a bug, include:

- Operating system.
- Python version.
- Whether you use the desktop build or source mode.
- FFmpeg/FFprobe availability from the Health page.
- A short description of the input video type and duration.
- The user-facing error message and any relevant non-sensitive log excerpt.

## 简体中文说明

欢迎贡献。提交前请确保不要提交 `.env`、API Key、处理过的视频、`processing/jobs` 产物、日志或其他私人文件。用户可见功能变化请同步更新 `README.md` 和 `README.zh-CN.md`。
