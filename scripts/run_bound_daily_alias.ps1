param([string]$Date='', [switch]$Resume, [switch]$DryRun, [string]$ProjectRoot='',
      [string]$RunId='', [switch]$NoNetwork, [switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$dataRoot=Split-Path -Parent $PSScriptRoot
if ($ProjectRoot -and [IO.Path]::GetFullPath($ProjectRoot) -ne [IO.Path]::GetFullPath($dataRoot)) { throw 'PRODUCTION_DATA_ROOT_MISMATCH' }
$parameters=@{ProjectRoot=$dataRoot;Command='daily-run'}
if ($ValidateOnly) { $parameters.Command='validate' }
if ($Date) { $parameters.Date=$Date }
if ($Resume) { $parameters.Resume=$true }
if ($DryRun) { $parameters.DryRun=$true }
if ($RunId) { $parameters.RunId=$RunId }
if ($NoNetwork) { $parameters.NoNetwork=$true }
& (Join-Path $dataRoot 'scripts/run_production.ps1') @parameters
exit $LASTEXITCODE
