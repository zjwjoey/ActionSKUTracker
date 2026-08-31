param(
    [string]$TaskName = "ActionSKUTracker-Daily",
    [string]$ProjectRoot = "F:\ActionSKUTracker",
    [string]$At = "03:30",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

# Task Scheduler registration changes Windows state and must be explicit.  The
# script is intentionally idempotent: re-running it replaces only this named
# task and never touches product data or the SQLite database.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "ADMIN_REQUIRED: open an elevated PowerShell before registering the task"
}

$wrapper = Join-Path $PSScriptRoot "run_production_daily.ps1"
if (-not (Test-Path -LiteralPath $wrapper)) { throw "WRAPPER_MISSING: $wrapper" }
if (-not (Test-Path -LiteralPath $ProjectRoot)) { throw "PROJECT_ROOT_MISSING: $ProjectRoot" }

try { $triggerTime = [DateTime]::ParseExact($At, "HH:mm", $null) }
catch { throw "INVALID_TIME: use HH:mm, for example 03:30" }

$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" -ProjectRoot `"$ProjectRoot`""
$trigger = New-ScheduledTaskTrigger -Daily -At $triggerTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Description "ActionSKUTracker SQLite PRIMARY daily production run"

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Write-Output ("REGISTERED task={0} time={1} project_root={2}" -f $TaskName, $At, $ProjectRoot)

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output ("STARTED task={0}; inspect with Get-ScheduledTaskInfo -TaskName {0}" -f $TaskName)
}
