param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$tools = @(
    @{ Name = "ffmpeg"; Portable = "ffmpeg.exe"; Required = $true },
    @{ Name = "ffprobe"; Portable = "ffprobe.exe"; Required = $true },
    @{ Name = "audiowaveform"; Portable = "audiowaveform.exe"; Required = $false }
)

$bin = Join-Path $Root "tools\bin"

foreach ($tool in $tools) {
    $portablePath = Join-Path $bin $tool.Portable
    $pathCommand = Get-Command $tool.Name -ErrorAction SilentlyContinue
    $status = if (Test-Path -LiteralPath $portablePath) {
        "portable"
    } elseif ($pathCommand) {
        "PATH"
    } elseif ($tool.Required) {
        "missing"
    } else {
        "optional missing"
    }
    $source = if (Test-Path -LiteralPath $portablePath) {
        $portablePath
    } elseif ($pathCommand) {
        $pathCommand.Source
    } else {
        ""
    }
    [PSCustomObject]@{
        Tool = $tool.Name
        Status = $status
        Source = $source
    }
}
