# Deployment compatibility entry: execute the active main checkout.
param(
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ($DryRun) {
    $deployment = Get-Content -LiteralPath (Join-Path $root "runtime/deployment/active.json") -Raw | ConvertFrom-Json
    $sourceRoot = [string]$deployment.source_checkout
    if ((& git -C $sourceRoot rev-parse HEAD).Trim() -ne $deployment.code_sha) {
        throw "PRODUCTION_CODE_IDENTITY_MISMATCH"
    }
    $env:PYTHONIOENCODING = "utf-8"
    $env:ACTION_TRACKER_PROJECT_ROOT = $root
    $env:PYTHONPATH = Join-Path $sourceRoot "src"
    python -m action_tracker daily-run --dry-run
} else {
    & (Join-Path $PSScriptRoot "run_production_daily.ps1")
}
exit $LASTEXITCODE
