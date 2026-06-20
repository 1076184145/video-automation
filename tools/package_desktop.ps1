param(
    [switch]$Lite,
    [switch]$Clean,
    [switch]$InstallDeps,
    [switch]$SkipBuild,
    [string]$Version = (Get-Date -Format "yyyyMMdd-HHmm")
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BundleName = if ($Lite) { "VideoAutomationLite" } else { "VideoAutomation" }
$BundleDir = Join-Path $Root "dist\$BundleName"
$ReleaseDir = Join-Path $Root "dist\releases"
$ZipPath = Join-Path $ReleaseDir "$BundleName-$Version.zip"
$ReadmeSource = Join-Path $Root "docs\DESKTOP_LITE_README.md"
$ReadmeTarget = Join-Path $BundleDir "README_DESKTOP.md"
$StageRoot = Join-Path $Root "build\package-$BundleName-$Version"
$StageBundle = Join-Path $StageRoot $BundleName

Set-Location $Root

if (-not $SkipBuild) {
    $buildArgs = @()
    if ($Lite) {
        $buildArgs += "-Lite"
    }
    if ($Clean) {
        $buildArgs += "-Clean"
    }
    if ($InstallDeps) {
        $buildArgs += "-InstallDeps"
    }
    & (Join-Path $Root "tools\build_desktop.ps1") @buildArgs
}

if (-not (Test-Path -LiteralPath $BundleDir)) {
    throw "Desktop bundle not found: $BundleDir"
}

if (Test-Path -LiteralPath $ReadmeSource) {
    Copy-Item -LiteralPath $ReadmeSource -Destination $ReadmeTarget -Force
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageBundle | Out-Null

$excludedNames = @(".env", "input", "processing", "logs", "logs-runtime", "desktop_app_error.log")
Get-ChildItem -LiteralPath $BundleDir -Force | Where-Object {
    $name = $_.Name
    $excludedNames -notcontains $name -and
        $name -notlike ".env.*" -and
        $name -notlike "*.log"
} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $StageBundle -Recurse -Force
}

Compress-Archive -LiteralPath $StageBundle -DestinationPath $ZipPath -CompressionLevel Optimal
Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Desktop release package created:" -ForegroundColor Green
Write-Host "  $ZipPath"
