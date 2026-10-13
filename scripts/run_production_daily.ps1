param(
    [string]$Date = "",
    [switch]$Resume,
    [switch]$DryRun,
    [string]$ProjectRoot = "",
    [string]$RunId = "",
    [switch]$NoNetwork
)
$ErrorActionPreference = "Stop"
$dataRoot = Split-Path -Parent $PSScriptRoot
if ($ProjectRoot -and [IO.Path]::GetFullPath($ProjectRoot) -ne [IO.Path]::GetFullPath($dataRoot)) {
    throw "PRODUCTION_DATA_ROOT_MISMATCH"
}
$manifestPath = Join-Path $dataRoot "runtime/deployment/active.json"
$deployment = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$sourceRoot = [string]$deployment.source_checkout
$actualHead = (& git -C $sourceRoot rev-parse HEAD).Trim()
$actualBranch = (& git -C $sourceRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $actualHead -ne $deployment.code_sha -or $actualBranch -ne "main") {
    throw "PRODUCTION_CODE_IDENTITY_MISMATCH"
}
if ([IO.Path]::GetFullPath($dataRoot) -ne [IO.Path]::GetFullPath([string]$deployment.data_root)) {
    throw "PRODUCTION_DATA_ROOT_MISMATCH"
}
$parameters = @{ProjectRoot=$dataRoot}
if ($Date) { $parameters.Date = $Date }
if ($Resume) { $parameters.Resume = $true }
if ($DryRun) { $parameters.DryRun = $true }
if ($RunId) { $parameters.RunId = $RunId }
if ($NoNetwork) { $parameters.NoNetwork = $true }
& (Join-Path $sourceRoot "scripts/run_production_daily.ps1") @parameters
exit $LASTEXITCODE
