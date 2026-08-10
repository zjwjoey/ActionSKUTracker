# 每日运行（Windows 计划任务可调用）
# 用法:  powershell -ExecutionPolicy Bypass -File scripts\run_daily.ps1 [-DryRun] [-Mode dry|full]
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
    python -m action_tracker daily-run --no-dry-run
}
