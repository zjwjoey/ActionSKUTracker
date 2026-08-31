# Operations Runbook V2

Daily data update remains `production-run` (compatible with P7/P8). Export is
on demand and reads the latest committed SQLite facts. Use the validated
PowerShell wrapper with `-ProjectRoot F:\ActionSKUTracker` when source and
production runtime are separate.

Before additive schema migration: create a SQLite Backup API backup, migrate,
run `db-validate-production`, inspect parity and only then use the production
database. Never drop or rewrite product/lifecycle/history tables.
## Windows Scheduler registration

The repository includes `scripts/register_action_tracker_task.ps1`. Run it
from an elevated PowerShell, for example:

```powershell
Set-Location F:\ActionSKUTracker_ops
.\scripts\register_action_tracker_task.ps1 -ProjectRoot F:\ActionSKUTracker -At 03:30
```

The script is idempotent and registers one daily task that calls
`run_production_daily.ps1`. It does not collect data while registering. Add
`-RunNow` only when an operator explicitly wants to start the registered task.

After registration, perform a Shadow check by inspecting the task action and
running one operator-approved invocation. Record the task name, action,
return code, wrapper run id and report path. A missing task or an elevation
failure is an operational follow-up, not a product-data change.
