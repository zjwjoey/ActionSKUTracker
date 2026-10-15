# Backup operations

Run from the approved checkout with `PYTHONPATH=src`:

```powershell
python -m action_tracker.operations.backup_cli --help
python -m action_tracker.operations.backup_cli --config C:\SecureOps\backup.json status
python -m action_tracker.operations.backup_cli --config C:\SecureOps\backup.json history
python -m action_tracker.operations.backup_cli --config C:\SecureOps\backup.json usage
```

The dedicated JSON configuration uses contract `BACKUP_OPERATIONS_CONFIG_V1`.
It contains `backend` (BackendConfig), `policy` (OperationPolicy), an independent
absolute `operations_root`, the **actual existing** `writer_lock_directory`,
explicit asset `roots`, and protected production/code roots. Credential fields
and unknown configuration keys are rejected. Both backend and policy `enabled`
default to false. Configured source roots and operations roots must be disjoint,
except the explicitly owned `operations_root/captures/<run_id>` area.

## Manual operations

- `repository-create --authorization FILE`: scoped operator creation approval;
  local target must be new. Cloud approval additionally binds provider, bucket,
  endpoint, region, exact prefix, expiry and a fresh empty-prefix observation.
  Save the returned repository ID in `backend.expected_repository_id` before
  later connections; a replaced repository cannot silently be accepted.
- `plan --request FILE --output FILE`: explicit selected asset request; planning
  is read-only to the source. Request fields follow `make_plan` except roots and
  repository, which come from the operations configuration.
- `capture-core --run-id ID --output FILE`: uses configured `capture.source`,
  `capture.projections`, `code_version`, `recovery_owner` and `retention`, calls
  existing P1 Backup API under the shared writer lock and creates a portable
  plan envelope. It never copies the live SQLite file through filesystem APIs.
- `now --plan FILE [--cloud-authorization FILE]`: explicit backup or Resume;
  duplicate successful jobs return their existing snapshot receipt.
- `verify --receipt FILE`: full Kopia file verification.
- `restore-preview --receipt FILE --target NEW_ROOT`, then `restore` with the
  same arguments and `--report NEW_FILE`: isolated restore and application QA.
- `cancel --backup-set-id ID --requested-by NAME`: stop at the next stage/copy
  checkpoint; an in-flight CLI is bounded by the shared deadline.
- `resume-cancelled --backup-set-id ID --requested-by NAME`: retains the old
  cancellation record and permits another bounded attempt.
- `reconcile-job-lock --backup-set-id ID --expected-lock-sha256 HASH
  --requested-by NAME`: explicitly archive and remove only this module's own
  job lock after proving its owner process is dead and its exact content and
  plan binding still match. It never reclaims the production writer lock.
- `release-owned-stage --backup-set-id ID --restore-report FILE`: discard only
  this module's newly staged copies after independent restore revalidation.
- `retention-preview --receipt FILE ...`: daily/weekly/monthly retention review;
  no automatic expiry, unpin, repository deletion or maintenance command.

Local password environment: `ACTION_KOPIA_LOCAL_PASSWORD`. OSS credentials:
`ACTION_KOPIA_OSS_PASSWORD`, `ACTION_KOPIA_OSS_ACCESS_KEY`,
`ACTION_KOPIA_OSS_SECRET_KEY`, optional `ACTION_KOPIA_OSS_SESSION_TOKEN`.
COS and generic S3 use equivalent `COS`/`S3` prefixes. A DPAPI/secure-store bridge
may populate the process environment without displaying its values.

## Scheduling and production activation

`SchedulePolicy` supports daily/local time, one-time scheduling and explicit
manual/task-completed/before-important-operation events. `schedule-export`
creates Windows Task Scheduler XML with IgnoreNew and bounded restart policy;
it does **not** register or enable the task. Event integrations use the `event`
command and an event ID/commit ID. No existing daily-run hook is changed.

`scheduled` prepares CORE through P1 and keys a daily job deterministically;
retry/Resume reuses that day's plan. A changed same-day source requires a new
explicit event instead of mutating a completed recovery point. Both automatic
commands require schedule enablement and a separate `BACKUP_ACTIVATION_V1`
operator receipt binding the configuration hash and a verified restore report.
An enabled XML export also requires its explicit production acceptance record.

Before enabling: inspect local/remote acceptance; choose protected/source
roots, capacity budgets and retention; confirm the real shared lock directory;
secure repository password/receipts; review the disabled XML; obtain production
enable authorization; then register using the existing deployment process.
This change performs none of those production activation actions.

## Cloud business authorization

`cloud_business_upload_authorized` alone is insufficient. An external approval
must bind the exact plan SHA-256 and repository ID, or a
`CLOUD_BACKUP_AUTHORIZATION_V1` recurring scope must bind the repository config,
full operations configuration, actual source-selection digest, profile, asset
kinds, logical-byte ceiling, approving person, instruction reference and expiry.
The recurring scope is `FIRST_AND_RECURRING_BUSINESS_BACKUP`; this is an explicit
first-upload authorization, not something generated by the scheduler. Configure
its path as `cloud_authorization_path`. No approval is bundled with this code.

Source-selection digests use actual canonical source locations (or the exact
PRIMARY/projection source paths of an owned P1 capture), not just root labels.
Moving a restore to another disk never implicitly expands a cloud-upload grant.

## Capture accumulation and local usage gate

The Storage CLI serializes CORE capture admission separately from the existing
shared production writer lock. A new run is blocked with
`PRIOR_CAPTURE_REQUIRES_DISPOSITION` while any previous nonempty capture directory
remains. The same run can resume. Unknown capture contents are retained and block
new captures too. This prevents recurring tasks from accumulating full database
copies up to the scratch quota; it does not authorize deleting a capture.

Before another run, the operator must close Resume/consumer responsibilities,
bind a verified independent recovery point and obtain the existing Cleanup
Lifecycle's per-file approval/observation/rollback/purge gates for the previous
capture. Do not manually erase it to bypass this gate. `release-owned-stage`
releases only stage copies and does not release captures or restore targets.
Unattended recurring CORE operation remains pending this disposition procedure
and its separately approved operating policy; this change does not enable it.

`usage` preserves `staged_bytes` and separately reports `capture_bytes`,
`session_bytes` (all sessions beneath the configured operations root), and their
sum `total_scratch_bytes`. Scratch alerts use that sum. Backend cache metrics
describe the current backend session only and must not be added again. External
restore directories and external tools' caches require their own inventory;
repository object bytes and billable traffic remain separate measurements.
