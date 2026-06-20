# Portable tools

Drop optional command-line tools here when you want the source checkout or desktop bundle to work without editing PATH.

On Windows, run `tools\install_desktop_tools.ps1` from the project root to download the common FFmpeg tools into this folder.

Recognized filenames:

- `ffmpeg.exe`
- `ffprobe.exe`
- `audiowaveform.exe` optional waveform tool

Explicit `.env` values still take priority. If a tool is not found here, Video Automation falls back to the normal command name on PATH.
