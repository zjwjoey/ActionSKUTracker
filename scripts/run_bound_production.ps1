param(
    [ValidateSet('validate','daily-run','export-history','export','export-template1')]
    [string]$Command = 'validate',
    [string]$ProjectRoot = '', [string]$Date = '', [string]$RunId = '',
    [ValidateSet('es','zh')][string]$Language = 'zh',
    [string]$OutputDirectory = '',
    [switch]$WithImages, [switch]$DryRun, [switch]$Resume, [switch]$NoNetwork,
    [ValidateSet('preview','production_release')][string]$ReleaseMode = 'preview'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$dataRoot = if ($ProjectRoot) { [IO.Path]::GetFullPath($ProjectRoot) } else { Split-Path -Parent $PSScriptRoot }
$activePath = Join-Path $dataRoot 'runtime/deployment/active.json'
$active = Get-Content -LiteralPath $activePath -Raw -Encoding UTF8 | ConvertFrom-Json
function FileHash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
if ([IO.Path]::GetFullPath([string]$active.data_root) -ne $dataRoot) { throw 'PRODUCTION_DATA_ROOT_MISMATCH' }
$sourceRoot = [string]$active.source_checkout
if ([string]$active.source_branch -notmatch '^release/[a-z0-9][a-z0-9/_-]*$' -or [string]$active.code_sha -notmatch '^[a-f0-9]{40}$') { throw 'PRODUCTION_RELEASE_IDENTITY_INVALID' }
$env:GIT_OPTIONAL_LOCKS = '0'
$head = (& git -C $sourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne [string]$active.code_sha) { throw 'PRODUCTION_CODE_IDENTITY_MISMATCH' }
$branch = (& git -C $sourceRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne [string]$active.source_branch) { throw 'PRODUCTION_CODE_IDENTITY_MISMATCH' }
$dirty = & git -C $sourceRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'PRODUCTION_RELEASE_WORKTREE_DIRTY' }
if ($null -eq $active.configuration_files) { throw 'PRODUCTION_CONFIG_BINDING_MISSING' }
foreach ($file in $active.configuration_files.PSObject.Properties) {
    $path = Join-Path $dataRoot $file.Name
    if ((FileHash $path) -ne [string]$file.Value) { throw "PRODUCTION_CONFIG_HASH_MISMATCH: $($file.Name)" }
}
$environment = $active.launch_environment
if ((FileHash ([string]$environment.path)) -ne [string]$environment.sha256) { throw 'RELEASE_ENVIRONMENT_HASH_MISMATCH' }
if ((FileHash (Join-Path $dataRoot 'scripts/run_production.ps1')) -ne [string]$active.entrypoint_sha256) { throw 'PRODUCTION_WRAPPER_HASH_MISMATCH' }
. ([string]$environment.path)
$python = [string]$environment.python_executable
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'RELEASE_PYTHON_BINDING_MISMATCH' }
$env:ACTION_TRACKER_PROJECT_ROOT = $dataRoot
$env:PYTHONPATH = Join-Path $sourceRoot 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:ACTION_TRACKER_CONFIG_PROFILE = $null
if ($Command -eq 'validate') {
    @{ validation='PASS'; source_checkout=$sourceRoot; code_sha=$head; source_branch=$branch;
       data_root=$dataRoot; configuration_binding='PASS'; entrypoint=$active.entrypoint } | ConvertTo-Json -Compress
    exit 0
}
if ($OutputDirectory) {
    if ($Command -eq 'daily-run') { throw 'PRODUCTION_OUTPUT_OVERRIDE_EXPORT_ONLY' }
    $env:ACTION_TRACKER_EXPORT_ROOT = [IO.Path]::GetFullPath($OutputDirectory)
} else { $env:ACTION_TRACKER_EXPORT_ROOT = $null }
if ($Command -eq 'daily-run') {
    $argsList = @('-m','action_tracker','data-update')
    if ($Date) { $argsList += @('--date',$Date) }
    if ($RunId) { $argsList += @('--run-id',$RunId) }
    if ($DryRun) { $argsList += '--dry-run' }
    if ($Resume) { $argsList += '--resume' }
    if ($NoNetwork) { $argsList += '--no-network' }
} else {
    if (-not $Date) { throw 'EXPORT_DATE_REQUIRED' }
    $argsList = @('-m','action_tracker',$Command,'--date',$Date)
    if ($Command -eq 'export') { $argsList += @('--lang',$Language) }
    if ($Command -ne 'export-history' -and $ReleaseMode -eq 'production_release') { $argsList += '--research-release' }
    if ($Command -ne 'export-history' -and $RunId) { $argsList += @('--run-id',$RunId) }
    if ($Command -ne 'export-history' -and $WithImages) { $argsList += '--with-images' }
}
Set-Location -LiteralPath $dataRoot
& $python @argsList
exit $LASTEXITCODE
