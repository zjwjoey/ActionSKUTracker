param([string]$RepoRoot = "F:\ActionSKUTracker_main_merge", [string]$DataRoot = "", [string]$Endpoint = "https://ws-5luepildpfsg40gc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", [int]$Limit = 1, [string]$Mode = "smoke", [string]$Field = "")
Set-Location -LiteralPath $RepoRoot
$arguments = @("scripts\run_qwen_mt_local.py", "--repo-root", $RepoRoot, "--endpoint", $Endpoint, "--limit", $Limit, "--mode", $Mode)
if ($Field) { $arguments += @("--field", $Field) }
if ($DataRoot) { $arguments += @("--data-root", $DataRoot) }
& python @arguments
exit $LASTEXITCODE
