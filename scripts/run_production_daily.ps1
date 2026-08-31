param(
    [string]$Date = "",
    [switch]$Resume,
    [switch]$DryRun,
    [string]$ProjectRoot = "",
    [string]$RunId = "",
    [switch]$NoNetwork
)

$ErrorActionPreference = "Stop"
$SourceRoot = Split-Path -Parent $PSScriptRoot
if (-not $PSBoundParameters.ContainsKey("ProjectRoot") -or [string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = $SourceRoot
}
Set-Location $ProjectRoot
$env:ACTION_TRACKER_PROJECT_ROOT = $ProjectRoot
$env:PYTHONPATH = Join-Path $SourceRoot "src"

$arguments = @("-m", "action_tracker", "data-update")
if ($Date) { $arguments += @("--date", $Date) }
if ($Resume) { $arguments += "--resume" }
if ($DryRun) { $arguments += "--dry-run" }
if ($RunId) { $arguments += @("--run-id", $RunId) }
if ($NoNetwork) { $arguments += "--no-network" }

& python @arguments
$exitCode = $LASTEXITCODE
exit $exitCode
