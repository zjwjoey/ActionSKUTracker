# 兼容旧调用：-DryRun 采集诊断；默认委托正式 Operations 入口。
# 正式计划任务请直接使用 scripts/run_production_daily.ps1。
param(
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = Join-Path $root "src"

if ($DryRun) {
    python -m action_tracker daily-run --dry-run
} else {
    & (Join-Path $PSScriptRoot "run_production_daily.ps1") -ProjectRoot $root
}
exit $LASTEXITCODE
