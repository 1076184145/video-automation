param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$Force,
    [switch]$SkipFfmpeg,
    [string]$FfmpegZipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath($Root)
$Bin = [System.IO.Path]::GetFullPath((Join-Path $Root "tools\bin"))
$Temp = [System.IO.Path]::GetFullPath((Join-Path $Root "tools\.tmp\desktop-tools"))

if (-not $Bin.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Invalid tools\bin path: $Bin"
}
if (-not $Temp.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Invalid temp path: $Temp"
}

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
New-Item -ItemType Directory -Force -Path $Temp | Out-Null

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Output
    )
    Write-Host "Downloading $Url"
    Invoke-WebRequest `
        -Uri $Url `
        -OutFile $Output `
        -UseBasicParsing `
        -Headers @{ "User-Agent" = "VideoAutomation-DesktopBootstrap" }
}

function Install-Ffmpeg {
    $ffmpegTarget = Join-Path $Bin "ffmpeg.exe"
    $ffprobeTarget = Join-Path $Bin "ffprobe.exe"
    if (-not $Force -and (Test-Path -LiteralPath $ffmpegTarget) -and (Test-Path -LiteralPath $ffprobeTarget)) {
        Write-Host "ffmpeg/ffprobe already exist in tools\bin. Use -Force to replace them." -ForegroundColor Yellow
        return
    }

    $zipPath = Join-Path $Temp "ffmpeg-release-essentials.zip"
    $extractPath = Join-Path $Temp "ffmpeg"
    Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue

    Download-File -Url $FfmpegZipUrl -Output $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

    $ffmpeg = Get-ChildItem -LiteralPath $extractPath -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    $ffprobe = Get-ChildItem -LiteralPath $extractPath -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
    if (-not $ffmpeg -or -not $ffprobe) {
        throw "Could not find ffmpeg.exe and ffprobe.exe in downloaded archive."
    }

    Copy-Item -LiteralPath $ffmpeg.FullName -Destination $ffmpegTarget -Force
    Copy-Item -LiteralPath $ffprobe.FullName -Destination $ffprobeTarget -Force
    Write-Host "Installed ffmpeg.exe and ffprobe.exe into tools\bin." -ForegroundColor Green
}

if (-not $SkipFfmpeg) {
    Install-Ffmpeg
}

Write-Host ""
Write-Host "Current desktop tool resolution:" -ForegroundColor Cyan
& (Join-Path $Root "tools\check_desktop_tools.ps1") -Root $Root
