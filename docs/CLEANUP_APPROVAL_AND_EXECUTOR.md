# Storage Cleanup V1

This branch adds an operator-only executor above P2.1. It does not deploy to
production, modify daily-run, enable an automatic cleanup job, or grant historical
file approval. Historical review and execution remain separate.

## Contracts and state

`cleanup_executor.review_candidates` reuses P2.1 and reports ACTIVE_REQUIRED,
RECOVERY_REQUIRED, ARCHIVE_REQUIRED, CLEANUP_REVIEW_REQUIRED, CLEANUP_ELIGIBLE or
UNKNOWN_BLOCKED. Only `LEGACY_CLEANUP_EVIDENCE_V1` can enter an execution plan.
The executor requires current identity, single hardlink, full SHA-256, database
integrity/FKs/identity where applicable, task/run ownership, explicit no-Resume,
released retention/recovery responsibility, independently available recovery proof
and fresh complete reference coverage. An approval cannot replace these gates.
Certificate booleans alone are insufficient: `closure_documents` binds current
file IDs and SHA-256 for task event history, `RETENTION_DISPOSITION_V1` and
`RECOVERY_RELEASE_V1`. Task completion is rederived with P2.1's exact run/path,
dated terminal/no-Resume validator. Responsibility decisions bind this exact
candidate, owner/run, named reviewer, review time, reason, applicable retention
policy and independent alternative recovery. The proof and recovery files are
held open and rehashed through mutation, preventing a recovery disappearing
between initial checks and deletion on Windows.

`CLEANUP_ACTION_PLAN_V1` binds every path, file ID, volume, size, modification time,
hash, task/database/recovery/retention certificate, complete reference evidence,
protected roots, quarantine location and minimum observation interval. Canonical
SHA-256 seals the entire record. JSON publication uses an exclusive atomic link
from a fully flushed temporary file; existing files are never replaced.

Approval requests contain `decision=PENDING` and null approver/timestamps. A human
or authorized external approval service supplies `CLEANUP_APPROVAL_V1`, with exact
plan hash/action/asset IDs, approver, authorization source, approval time and expiry.
Execution additionally takes the approval digest via an independent operator
argument. These are auditable local operator assertions, **not cryptographic human
identity authentication**. The account controlling execution must protect approval
files and verify that the named approval was actually granted. This program never
creates an APPROVED record. No bulk yes/automatic-approval option exists.

`HISTORICAL_ASSET_DISPOSITION_V1` records a **present-day** owner's decision to retain,
archive or release task ownership. It preserves the unknown historical task status,
does not create a historical SUCCESS, and never grants cleanup eligibility or fills
unknown technical reference counts with zero. P2.1 technical gates remain mandatory.

## Reference closure

`capture_reference_scope` inventories explicit consumer directories for all eight
P2.1 domains (configuration, manifests, database records, runtime logs, recovery
lists, review/apply, active tasks, code consumers). Its output is incomplete by
default. A responsible reviewer must substantiate scope completeness and resolve
dynamic/excluded sources. Directory snapshots include file ID, size, mtime and full
hash; additions/removals/changes block execution for renewed review. Files are
bounded to 10,000 and 64 MiB per directory. Unsupported binary consumers block;
they require a trusted materialized read-only consumer export and its dependency
boundary, not an assertion that unsupported files have no references. The module
does not pretend selected folders prove all references on a machine.

Reference inventory age defaults to one hour. Purge after a longer observation
window needs fresh evidence via `purge-preview --references`. At execution the
inventory is compared and paths are searched again. Unknown remains UNKNOWN.

## Isolation, quarantine, rollback, purge

The operator context supplies a new exclusive `state_root`, explicit
`protected_roots`, and relevant `active_task_markers` (including daily-run's lock).
The context must include every deployed data/code/config root; it is part of the
approved plan. Existing roots cannot be adopted. Windows roots receive ACLs for
the current account and SYSTEM; POSIX roots use mode 0700. The repository's
read-only historical F:/按日期整理 and F:/Action_Master boundaries are additionally
protected on Windows. File-system resolution, same-file checks, file IDs and
reparse detection supplement path checks.

Execution is disabled unless explicitly passed `enabled=True` / `--enable-execution`.
Only same-volume quarantine is supported: the file identity remains unchanged.
Cross-volume quarantine returns a blocker and requires separately verified archive
copy planning. UNC paths, alternate data streams, symlinks, junctions, hardlinked
files, active WAL/SHM/journals, active task markers, changed identity, unavailable
recovery and an existing destination all block. Windows opens the source with
exclusive sharing, hashes the held handle and performs rename/delete through that
same handle. POSIX tests use cooperative flock; this is explicitly reported and
does not claim to prevent noncooperating POSIX writers.

A durable intent precedes each mutation; a separate immutable receipt follows
post-operation checks. Completed operations resume as NO_OP with identity checks.
An interrupted intent blocks with `INTERRUPTED_OPERATION_RECONCILIATION_REQUIRED`;
operators must inspect it and prepare a newly approved action rather than guessing
whether deletion happened. There is no automatic stale-lock reclamation.
Batch exceptions retain explicit PARTIAL results with completed receipt paths and
pending intent paths. CLI checks the output destination before side effects; if a
late report write fails it returns the actual result and exit 3, not a claim that
the operation never happened.

Quarantine requires its own exact approval and returns QUARANTINED. The default
minimum observation is 86,400 seconds. The prior quarantine receipt plus externally
reviewed observation evidence is required for purge; evidence files are hash-bound.
The observation review timestamp itself must follow the full minimum interval;
waiting after an early review does not retroactively make that review valid.
PURGE needs a new plan and new approval, never the quarantine approval. Rollback
also has its own approval, preserves identity and refuses overwrite. It permits
restoration when a new consumer reference is discovered, which is a reason to
restore the original path. Active markers and unsafe original paths still block.

Quarantine/rollback report zero logical deleted bytes. Purge reports logical bytes
deleted separately from actual observed volume free-space delta; the latter may
include unrelated processes and is not claimed to be attributable savings.
No recursive directory deletion API exists.

## Archive API

`archive_assets(items, roots, max_bytes=..., consumer_check=...)` reuses P2's
materialization, capacity gate and Asset Resolver. Copies are exclusively created,
hash-verified and require a caller-specific consumer check returning PASS and the
consumer identity. Independent sealed `ARCHIVE_ASSET_V1` manifests retain bindings.
Source bytes are never moved or deleted. Archive success keeps source exit status
`SEPARATE_REVIEW_AND_APPROVAL_REQUIRED`; cloud success is not deletion permission.

## CLI

Run from the isolated checkout with `PYTHONPATH=src`:

```text
python -m action_tracker.operations.cleanup_cli init --context context.json
python -m action_tracker.operations.cleanup_cli review --candidates candidates.json
python -m action_tracker.operations.cleanup_cli capture-references --scope scope.json --out observed.json
python -m action_tracker.operations.cleanup_cli record-disposition --decision owner-decision.json --out disposition.json
python -m action_tracker.operations.cleanup_cli plan --candidates candidates.json --references approved-scope.json --context context.json --out quarantine-plan.json
python -m action_tracker.operations.cleanup_cli approval-request --plan quarantine-plan.json --out request.json
python -m action_tracker.operations.cleanup_cli execute --plan quarantine-plan.json --approval externally-approved.json --approval-sha256 DIGEST --out quarantine-result.json --enable-execution
python -m action_tracker.operations.cleanup_cli rollback-preview --previous-plan quarantine-plan.json --out rollback-plan.json
python -m action_tracker.operations.cleanup_cli purge-preview --previous-plan quarantine-plan.json --references fresh-scope.json --observation observation.json --out purge-plan.json
```

The last two outputs require their own approval requests and execute calls.
All plans, approvals and execution results belong outside Git. CLI prints explicit
BLOCKED reasons and exits 2 on failure. It never initializes production runtime.

## Tests and production activation

`tests/test_cleanup_executor.py` uses owned synthetic files to exercise real
Windows quarantine, rollback, purge, idempotence, changed references/identity,
expired/mismatched approval, held-open files, sidecars, recovery loss, observation,
archive resolver/consumer checks and interruption blocking. One symlink capability
test is skipped only when Windows refuses creating the test symlink; junction and
reparse detection itself remains fail-closed. P2.1 regressions independently test
strict historical task terminal and restore proof bindings.

No existing historical file is authorized by these tests. Before production use,
review complete protected-root/task-marker configuration and consumer scope, define
retention/observation policy, close per-file technical evidence and obtain exact
file approvals. POSIX noncooperating writer protection and cross-volume quarantine
are intentionally unsupported rather than silently weakened.
