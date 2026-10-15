# Storage V3 architecture

The business authority remains SQLite PRIMARY. Ordinary daily-run, schemas,
commit contracts, projections, dictionary approval and deployment are unchanged.

1. `storage_foundation.verified_backup` supplies a closed SQLite Backup API
   artifact and P1 identity/consistency evidence. `backup_capture.capture_core`
   optionally prepares that artifact under the existing `daily-run.lock`.
2. `backup_sets` selects explicit assets, validates provenance and P1 evidence,
   rejects path aliases, builds a sealed plan and stages only that selection.
3. `kopia_backend` controls an external Kopia CLI. Repository identity, source
   content tags and pinned snapshot IDs are checked independently of filenames.
4. `backup_operations` journals attempts, enforces budgets/authorizations,
   publishes immutable receipts, and restores to a new isolated root.
5. `backup_cli` and `backup_scheduler` expose opt-in operations. Exporting task
   XML does not register a Windows task. Neither module is imported by daily-run.

Storage Cleanup V1 is a separate branch/module. A successful Kopia restore can
support a recovery evidence record, but never establishes reference closure,
owner retirement, a retention release, or permission to delete an old asset.

## Independent roots and credentials

All roots are explicit configuration, with no fixed drive requirement. Local,
USB and NAS repositories use the filesystem backend; inaccessible media and
unrecognized/replaced repository IDs fail closed. Reparse points, symbolic
links and hardlinks are refused rather than ambiguously followed.

Backend child processes receive an environment allowlist plus only their own
credentials. Private Kopia workspaces use Windows ACL/POSIX permissions. No
credential arguments, persisted Kopia credentials, repository actions, TLS
bypass or automatic maintenance are enabled. Use environment injection or a
separately secured credential bridge, never credential values in configuration.

## Scope and boundaries

`CORE_BACKUP` requires one P1 database, its exact three committed projections,
run evidence and a recovery record. `CURRENT_BUSINESS_BACKUP` is an explicit
asset selection, with SKU/source-version/hash binding for images and source
evidence. `HISTORICAL_ARCHIVE_BACKUP` additionally requires per-asset retention
ownership. UNKNOWN is never automatically excluded or declared disposable.

The snapshot carries its portable plan. The external receipt carries the
repository ID, manifest snapshot ID, root object ID and plan digest. Retain both
receipt and repository password separately; there is no promise of recovery
from a snapshot ID alone. Original paths are evidence, never restore targets.

Defaults are disabled. Production scheduling, first business cloud upload,
historical movement and purge each require their own authorization. Code
delivery is not production deployment. The application does not install a
daemon, migrate another machine, or combine another business PRIMARY.
