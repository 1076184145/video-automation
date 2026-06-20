# Video Automation Lite

This is the lightweight desktop bundle for Video Automation.

## Start

Double-click:

```text
VideoAutomationLite.exe
```

The app starts a local API server and opens the Web control panel in a desktop WebView window.

## First-run tool check

If the Health page reports missing `ffmpeg`, `ffprobe`, or `yt-dlp`, click **Auto-fix Dependencies** in the Health page. The app will run the bundled installer script in the background and show progress in the UI.

You can also check tool resolution manually:

```powershell
.\tools\check_desktop_tools.ps1
```

To download the common Windows tools into the portable `tools\bin` folder, run:

```powershell
.\tools\install_desktop_tools.ps1
```

This installs:

- `ffmpeg.exe`
- `ffprobe.exe`
- `yt-dlp.exe`

Optional tools such as `audiowaveform.exe` and `olived-resolver.exe` can also be placed in `tools\bin`.

## Configuration

Use the in-app Settings page for common paths and API keys. You can also edit `.env` next to the executable.

Secrets are not bundled by default. If you share this folder or zip, check `.env` first.

## Notes

The Lite bundle intentionally excludes heavy optional ML libraries such as torch, FunASR, SciPy, and ModelScope. Use the source checkout or the full desktop build if you need those local ML backends bundled.
