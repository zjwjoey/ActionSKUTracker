param(
    [string]$Date = "",
    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

$arguments = @("-m", "action_tracker", "production-run")
if ($Date) { $arguments += @("--date", $Date) }
if ($Resume) { $arguments += "--resume" }
if ($DryRun) { $arguments += "--dry-run" }

& python @arguments
$exitCode = $LASTEXITCODE
exit $exitCode
