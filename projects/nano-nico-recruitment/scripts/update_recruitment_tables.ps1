Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param(
    [string]$ReportDate,
    [string]$OutputDir = "recruitment_outputs",
    [string]$PythonCommand = "python"
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $repoRoot "recruitment_reports.py"

if (-not (Test-Path $scriptPath)) {
    throw "Could not find recruitment_reports.py at $scriptPath"
}

if (-not $env:NANO_API_TOKEN -and -not $env:NICO_API_TOKEN) {
    throw "Set NANO_API_TOKEN and/or NICO_API_TOKEN before running this script."
}

Write-Host "Running read-only REDCap recruitment refresh (export methods only)."

$arguments = @($scriptPath, "--output-dir", $OutputDir)
if ($ReportDate) {
    $arguments += @("--report-date", $ReportDate)
}

Push-Location $repoRoot
try {
    & $PythonCommand @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Recruitment table refresh failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
