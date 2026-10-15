# Storage V3.1 operational lifecycle

This is an optional path within Backup V1. Ordinary daily-run, PRIMARY schema,
production deployment and legacy Cleanup qualification are unchanged. Actual
run/CI/merge evidence is delivered outside Git; implementation does not imply
production activation, password escrow, or V3 cloud acceptance.

## New assets and evidence

`CAPTURE_LIFECYCLE_V1` owns only newly created P2 ScratchTask workspaces beneath
`operations_root/capture-workspaces`. A private immutable manifest under
`capture-lifecycle/<run-id>` binds the root owner token, ScratchTask token,
source configuration, run/Backup Set ID, source PRIMARY, repository identity,
protection roots and creation policy. Plans contain the P1 hashes, database
head/schema identities and projection relationships. Append-only sealed events
reuse the existing Backup job-event chain.

Evidence states are CREATED, P1_VERIFIED, SNAPSHOT_COMMITTED,
SNAPSHOT_VERIFIED, RESTORE_VERIFIED, REFERENCES_REVIEWED,
DISPOSITION_PENDING, RETAIN_REQUIRED, DISPOSITION_ELIGIBLE, RETIRED, BLOCKED.
State events require the corresponding completed operation and evidence digest;
snapshot verification never implies independent restore verification.

Capture uses P1's SQLite Backup API with the existing shared writer lock. It
does not copy a live SQLite file. The configured formal PRIMARY at
`F:/ActionSKUTracker/runtime/db/action_tracker.db` requires the formal
`F:/ActionSKUTracker/runtime/state` lock directory. Operations/workspaces are
forbidden beneath the known production roots even if a user omits them from
the protection list. Backup Set, Kopia operations and independent restore
validation are reused, including Integrity/FK/head/export_sync checks.

## Policy and activation

The dedicated Backup CLI config optionally accepts `capture_lifecycle`:

```json
{
  "enabled": false,
  "minimum_retention_seconds": 86400,
  "quarantine_seconds": 86400,
  "keep_latest_capture": true,
  "independent_rollback_required": true
}
```

Defaults retain data and do not enable disposition. A separately provided
`CAPTURE_DISPOSITION_POLICY_V1` grant must explicitly authorize only
`NEW_CAPTURE_WORKSPACES_ONLY`, actions `QUARANTINE` and `PURGE`, the exact root
token/path, source/configuration digest, policy digest, approver/instruction,
expiry, and a validated activation restore report from that same configuration
and managed registry. It also declares the eight complete consumer domains and
zero unresolved dynamic references and zero excluded required sources. Unknown
coverage is never converted to zero. The configuration digest binds existing
operation limits, optional operations policy and backend settings as well as
source paths and protection roots; changing them requires a new review.
The grant's unknown fields are rejected, including accidental credential fields.
Grants contain no passwords or cloud keys.

At the next configured cycle, a valid scoped policy grant may re-evaluate the
previous completed task after its minimum/quarantine time has elapsed. Only an
actual successful retirement releases admission. Missing restore proof, an
unresolved obligation or expired authorization still blocks the new run.

Each disposition freshly inventories all eight domains using Cleanup V1 and
checks actual references before and during mutations. Approved scope explicitly
excludes immutable internal audit records: the post-RETIRED Resume path returns
the completed result without reopening payloads. Snapshot/receipt/restore proof,
registered hashes/identity, minimum retention, independent rollback obligations,
writer activity and cancellation must pass. Snapshot recovery responsibility
remains retained; no snapshot deletion or maintenance is added.

P2 ScratchTask journals are retained as historical creation/completion evidence;
new lifecycle mutation receipts and RETIRED events record the subsequent
disposition. This does not reinterpret any historical asset's recovery flags.

The existing Cleanup V1 exclusive HANDLE/fstat/hash/no-overwrite primitive
performs only exact registered-file quarantine/purge. It is not a recursive
directory deletion facility. Each file has an immutable intent and receipt;
unregistered files, aliases, sidecars, reappearing original paths, changed
references, expired grants and cross-volume quarantine block the task.
Only small manifests/journals/receipts remain; capture, stage and temporary
restored payloads leave the working space after verified approved disposition.

`keep_latest_capture` or independent rollback responsibility deliberately blocks
disposition and the next run. To release a creation policy, a new explicit grant
must bind `release_creation_policy_sha256` as well as the new policy digest.
Renewing an expired in-progress disposition additionally binds
`replaces_grant_sha256`. No policy change or extension is automatically approved.

Historical files and old unmanaged `captures` always require the old P2.1/
Cleanup V1 review and per-file approval; they cannot be adopted by this path.

## Resume, cancellation and failure

One admission lock serializes managed captures, with separate lifecycle and
existing Backup job locks. Same run IDs resume persistent plans; completed
RETIRED runs are no-ops even when the original source has subsequently changed.
Unknown snapshot outcomes use the existing tag/source reconciliation, not a
second blind upload. A complete download that lost its restore report is
revalidated byte-for-byte and against the database before a report is rebuilt.
An incomplete or corrupt download stays BLOCKED; it is not overwritten or
automatically discarded. Operator review/recovery is required for that state.

Nested backend deadlines preserve the earliest deadline. Capture/restore/
disposition phase boundaries and held-file mutation guards enforce the overall
invocation budget; unknown outcomes remain recoverable. Cancellation is checked
before work and at disposition guards. `resume-cancelled` is explicit.

Interrupted quarantine/purge receipts are reconciled using the bound intent,
file identity and actual source/target state. A missing file without an owned
intent is not evidence of a completed delete. Reconciled absence is labelled
and does not invent a new measured space saving.

`capture-reconcile-lock` accepts only the exact new admission/lifecycle/Backup
job lock, matching hash/token/run and a verifiably dead PID. It archives evidence
before removing that lock. It never reclaims the shared production writer lock.
A crash during P1/Backup while holding the shared writer lock requires the
existing production lock-owner recovery procedure; do not force-delete it.
Unknown/partially published metadata stays BLOCKED for bounded evidence repair,
not automatic asset deletion. Process-restart tests are not actual power-cycle
or machine-reboot tests.

`capture-rollback` can restore quarantined new assets before any purge intent,
never overwrites, and records RETAIN_REQUIRED. It deliberately stops automatic
disposition and subsequent admission pending owner review. After purge, use a
new independent snapshot restore, not a fabricated rollback.

## Operator commands

Run the audited Python from the audited checkout's `src` directory; no
interactive PYTHONPATH is necessary. Dedicated config/protection roots and
credentials must be provisioned for that exact account. No credentials in argv.
For local repositories only, optional `local_password_dpapi_path` reads the
existing current-user DPAPI binary format. The absolute regular single-link
file must be owned by the current SID, with allow ACEs only for that SID and
SYSTEM. The configured private file takes precedence over an interactive
password environment variable. It is decrypted in process, never printed, and
does not provide independent escrow. Cloud providers reject this local source.
Task-account DPAPI/profile availability still requires actual account setup;
the generated-secret test is not a production service-account acceptance.

```text
python -m action_tracker.operations.backup_cli --config CONFIG health --output NEW_STATUS_FILE
python -m action_tracker.operations.backup_cli --config CONFIG capture-cycle --run-id RUN
python -m action_tracker.operations.backup_cli --config CONFIG capture-cycle --run-id RUN --grant GRANT --retire
python -m action_tracker.operations.backup_cli --config CONFIG capture-rollback --run-id RUN --requested-by OWNER
python -m action_tracker.operations.backup_cli --config CONFIG capture-reconcile-lock --run-id RUN --kind lifecycle --expected-lock-sha256 SHA --requested-by OWNER
```

Health is read-only and never contacts Kopia or cloud services. Its immutable
timestamped output is suitable for an external monitor: exit 0 healthy/disabled,
1 warnings, 2 blocked/error. It reports latest snapshot success/failure,
independent restore, all unfinished captures, pending bytes, observed local
storage, capacity/expiry warnings and next-run blockers. Repository live
connectivity is explicitly NOT_PROBED; no actual notification is claimed.
Mutating CLI invocations also publish sealed `BACKUP_INVOCATION_V1` files under
the operations root. Thus authentication/credential failures before a Backup
job exists still appear as the latest failed invocation in health, with an
exit code and sanitized category. Disabled/read-only commands create no such
files. Invalid configuration cannot authorize an arbitrary log root and falls
back to stdout/exit code; inability to persist an otherwise completed command's
status returns exit 2 with the successful result preserved as degraded evidence.

`BACKUP_OPERATIONS_POLICY_V1` is an optional strict metadata policy with
`approved=false` by default: CORE/current success-age expectations, archive
trigger, daily/weekly/monthly retention recommendations, restore-check interval,
repository budget, warning threshold, password/recovery/operations owners and
credential-expiry metadata. Existing OperationPolicy remains the authority for
attempt/time/scratch/upload/download limits. Existing Scheduler controls actual
triggers; the health expectations do not secretly register new schedules.
Managed automatic scheduling additionally requires an approved operations policy
and the existing configuration-bound production activation receipt. Windows XML
export is reused; no task registration or production deployment occurs.

## Password and cloud boundaries

DPAPI current-user storage is not independent disaster recovery. Actual offline
custody, alternate owner retrieval, rotation records and real-repository
cross-account/device recovery remain external PENDING gates. A generated test
password supplied to another isolated process proves only that process scenario.

V3.1 capture lifecycle currently accepts only local filesystem repositories.
OSS Gate remains BLOCKED; no STS requests, Bucket edits, cloud writes or paid
resources are part of this feature. Old OSS PoC evidence remains historical.

`scripts/storage_v31_local_acceptance.py` is LOCAL_ONLY: generated tiny SQLite,
real local Kopia, three process-separated runs, intentional process exit,
exact dead-lock reconciliation, repeated-run idempotence and test-only grants.
It requires a new root, never production input. It records per-round file/byte
inventories and retains generated test password only under private DPAPI storage.
