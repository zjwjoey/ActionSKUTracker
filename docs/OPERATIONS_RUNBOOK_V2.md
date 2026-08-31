# Operations Runbook V2

Daily data update remains `production-run` / `data-update` (compatible with
P7/P8). Export is on demand and reads the latest committed SQLite facts. Use the validated
PowerShell wrapper with `-ProjectRoot F:\ActionSKUTracker` when source and
production runtime are separate.

## Post-Merge Production Safety

In `SQLITE_PRIMARY`, `daily-run` is a diagnostic runner and defaults to dry-run.
`daily-run --no-dry-run` is rejected with `FORMAL_RUN_REQUIRES_DATA_UPDATE`.
The only formal write entry points are `production-run` and `data-update`; they
run PREFLIGHT, a validated SQLite Backup API backup, the delegated collection,
QA, commit, compatibility-export recovery and an Operations report.

Resume restores the delegated run id, QA result, commit status and commit id
from the persisted COLLECTION allowlist. If a formal DB commit succeeded but
compatibility projection is pending, resume runs only `regenerate_pending_exports`
for that commit. It never recollects or creates a second commit. A newer formal
commit marks older pending projection rows `SUPERSEDED`, which are not retryable.

`status` reports PRIMARY facts first, then the compatibility projection. When
the database and Master differ, it reports `COMPATIBILITY = OUT_OF_SYNC` rather
than presenting the Master count as authoritative. `qa` resolves
`runtime/snapshots/<date>/<run_id>/qa_report.json`; use `qa --run-id <id>` for a
specific run.

Detail recovery in PRIMARY is a field-level correction transaction. It may only
change `name_es`, category, spec, description, details, product URL and image
URL. It cannot change price, badges, status, lifecycle or Presence. Each change
is stored in `detail_corrections`, emits derived content-change evidence and is
then projected from SQLite into compatibility files.

The localhost Workspace rejects POST/PUT/DELETE requests with a non-loopback
Host or Origin. The optional image job accepts only hosts listed in
`images.allowed_hosts`; unexpected hosts fail as `IMAGE_SOURCE_HOST_NOT_ALLOWED`
without affecting product lifecycle.

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

## Recommended GitHub branch protection

Set these repository rules in GitHub (the application does not set them
automatically):

- Require a pull request before changes to `main`.
- Require the CI workflow for both Ubuntu and Windows before merge.
- Block force pushes and branch deletion on `main`.
