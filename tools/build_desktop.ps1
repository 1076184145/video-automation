param(
    [switch]$InstallDeps,
    [switch]$Clean,
    [switch]$Lite
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root "venv\Scripts\python.exe"
$PyInstaller = Join-Path $Root "venv\Scripts\pyinstaller.exe"
$Spec = Join-Path $Root $(if ($Lite) { "desktop_app_lite.spec" } else { "desktop_app.spec" })
$BundleName = if ($Lite) { "VideoAutomationLite" } else { "VideoAutomation" }

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

Set-Location $Root

if ($InstallDeps) {
    & $Python -m pip install pywebview pyinstaller
}

if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller is not installed. Run: .\tools\build_desktop.ps1 -InstallDeps"
}

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $Root "build") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Root "dist\$BundleName") -Recurse -Force -ErrorAction SilentlyContinue
}

& $PyInstaller $Spec

Write-Host ""
Write-Host "Desktop bundle created:" -ForegroundColor Green
Write-Host "  $(Join-Path $Root "dist\$BundleName\$BundleName.exe")"
Write-Host ""
if ($Lite) {
    Write-Host "Lite build excludes heavy optional ML libraries such as torch/funasr/scipy."
}
Write-Host "Put ffmpeg.exe/ffprobe.exe in tools\bin, or configure paths in the app Settings page/.env."
