param(
    [Parameter(Position = 0)]
    [ValidateSet("refresh", "review")]
    [string]$Step = "refresh",

    [string]$Root = (Join-Path $PSScriptRoot "LMS_Files"),
    [string]$RunDir = "",
    [string]$Also = "",
    [switch]$Yes,
    [switch]$IncludePast,
    [switch]$KeepUndated,
    [switch]$KeepSuperseded
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Python {
    param([string[]]$PythonArgs)
    Write-Host ""
    Write-Host ("> python " + ($PythonArgs -join " ")) -ForegroundColor Cyan
    & python @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("Python step failed: " + ($PythonArgs -join " "))
    }
}

if ($Step -eq "refresh") {
    Invoke-Python @("lms_fetch.py")

    $downloadArgs = @("lms_download.py", $Root)
    if ($Yes) {
        $downloadArgs += "--yes"
    }
    Invoke-Python $downloadArgs

    $runName = Get-Date -Format "yyyyMMdd-HHmmss"
    $RunDir = Join-Path $PSScriptRoot (Join-Path "runs" $runName)
    if (Test-Path -LiteralPath $RunDir) {
        throw "Run folder already exists: $RunDir"
    }
    New-Item -ItemType Directory -Path $RunDir | Out-Null

    $bundleArgs = @("lms_bundle.py", "--output-dir", $RunDir)
    if ($Also) {
        $bundleArgs += @("--also", $Also)
    }
    Invoke-Python $bundleArgs

    Write-Host ""
    Write-Host ("For each bundle in " + $RunDir + ", open a new Claude.ai chat, attach the JSON file, paste lms_prompt.txt, and save the reply in that same folder as claude_output_01.json, claude_output_02.json, and so on.") -ForegroundColor Yellow
    Write-Host "When replies are ready, preview and export locally with:" -ForegroundColor Yellow
    Write-Host ("  .\run_lms.ps1 review -RunDir " + [char]34 + $RunDir + [char]34) -ForegroundColor Yellow
}
else {
    if (-not $RunDir) {
        throw "Review needs -RunDir pointing to a generated run folder."
    }
    if ([IO.Path]::IsPathRooted($RunDir)) {
        $resolvedRunDir = [IO.Path]::GetFullPath($RunDir)
    }
    else {
        $resolvedRunDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $RunDir))
    }
    $reviewArgs = @("lms_review.py", $resolvedRunDir)
    if ($KeepSuperseded) {
        $reviewArgs += "--keep-superseded"
    }
    if ($IncludePast) {
        $reviewArgs += "--include-past"
    }
    if ($KeepUndated) {
        $reviewArgs += "--keep-undated"
    }
    Invoke-Python $reviewArgs
}
