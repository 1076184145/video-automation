param(
    [string]$Version = "0.1.0",
    [switch]$BuildDesktop,
    [switch]$CleanDesktop,
    [string]$InnoCompiler = $env:INNO_SETUP_COMPILER
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BundleDir = Join-Path $Root "dist\VideoAutomationLite"
$BundleExe = Join-Path $BundleDir "VideoAutomationLite.exe"
$Script = Join-Path $Root "installer\VideoAutomationLite.iss"

function Find-InnoCompiler {
    param([string]$Configured)

    $candidates = @()
    if ($Configured) {
        $candidates += $Configured
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    $fromPath = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    return ""
}

Set-Location $Root

if ($BuildDesktop -or -not (Test-Path -LiteralPath $BundleExe)) {
    $desktopArgs = @("-Lite")
    if ($CleanDesktop) {
        $desktopArgs += "-Clean"
    }
    & (Join-Path $Root "tools\build_desktop.ps1") @desktopArgs
}

if (-not (Test-Path -LiteralPath $BundleExe)) {
    throw "Desktop bundle not found: $BundleExe"
}
if (-not (Test-Path -LiteralPath $Script)) {
    throw "Installer script not found: $Script"
}

$Compiler = Find-InnoCompiler -Configured $InnoCompiler
if (-not $Compiler) {
    throw "Inno Setup compiler not found. Install Inno Setup 6 or set INNO_SETUP_COMPILER to ISCC.exe."
}

try {
    $env:VIDEO_AUTOMATION_INSTALLER_VERSION = $Version
    & $Compiler $Script
} finally {
    Remove-Item Env:\VIDEO_AUTOMATION_INSTALLER_VERSION -ErrorAction SilentlyContinue
}

$installer = Join-Path $Root "dist\installers\VideoAutomationLite-Setup-$Version.exe"
Write-Host ""
Write-Host "Installer created:" -ForegroundColor Green
Write-Host "  $installer"
