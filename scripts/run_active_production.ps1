param(
    [string]$Date = "",
    [switch]$Resume,
    [switch]$DryRun,
    [string]$ProjectRoot = "",
    [string]$RunId = "",
    [switch]$NoNetwork,
    [switch]$ValidateOnly
)
$ErrorActionPreference = "Stop"
# Keep the machine-readable wrapper contract identical on Windows PowerShell
# and PowerShell 7, including Chinese checkout/data paths and error records.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$dataRoot = Split-Path -Parent $PSScriptRoot
if ($ProjectRoot -and [IO.Path]::GetFullPath($ProjectRoot) -ne [IO.Path]::GetFullPath($dataRoot)) {
    throw "PRODUCTION_DATA_ROOT_MISMATCH"
}
$manifestPath = Join-Path $dataRoot "runtime/deployment/active.json"
$deployment = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$sourceRoot = [string]$deployment.source_checkout
$expectedBranch = [string]$deployment.source_branch
if ($expectedBranch -notmatch '^(main|release/[a-z0-9][a-z0-9/_-]*)$' -or
    [string]$deployment.code_sha -notmatch '^[a-f0-9]{40}$') {
    throw "PRODUCTION_RELEASE_IDENTITY_INVALID"
}
$actualHead = (& git -C $sourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "PRODUCTION_CODE_IDENTITY_MISMATCH" }
$actualBranch = (& git -C $sourceRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $actualHead -ne $deployment.code_sha -or $actualBranch -ne $expectedBranch) {
    throw "PRODUCTION_CODE_IDENTITY_MISMATCH"
}
if ($expectedBranch.StartsWith("release/")) {
    $dirty = & git -C $sourceRoot status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw "PRODUCTION_RELEASE_WORKTREE_DIRTY" }
}
if ([IO.Path]::GetFullPath($dataRoot) -ne [IO.Path]::GetFullPath([string]$deployment.data_root)) {
    throw "PRODUCTION_DATA_ROOT_MISMATCH"
}
if ($ValidateOnly) {
    @{ validation="PASS"; source_checkout=$sourceRoot; code_sha=$actualHead;
       source_branch=$actualBranch; data_root=$dataRoot } | ConvertTo-Json -Compress
    exit 0
}
$parameters = @{ProjectRoot=$dataRoot}
if ($Date) { $parameters.Date = $Date }
if ($Resume) { $parameters.Resume = $true }
if ($DryRun) { $parameters.DryRun = $true }
if ($RunId) { $parameters.RunId = $RunId }
if ($NoNetwork) { $parameters.NoNetwork = $true }
& (Join-Path $sourceRoot "scripts/run_production_daily.ps1") @parameters
exit $LASTEXITCODE
