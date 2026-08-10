# 手动备份正式 Master 到 runtime/backups（带时间戳）
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "runtime\master\Action_Master.xlsx"
$bakDir = Join-Path $root "runtime\backups"
New-Item -ItemType Directory -Force -Path $bakDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dst = Join-Path $bakDir "Action_Master_$stamp.xlsx"
Copy-Item $src $dst
Write-Host "已备份: $dst"
