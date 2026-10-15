# BACKUP_SET_V1

## Plan and receipt

`BACKUP_SET_PLAN_V1` is the immutable selection intent, stored as `plan.json`
in the job and as `backup-set.json` inside its snapshot. It includes:

- backup-set/run IDs, creation time, profile, source root IDs and config version;
- asset IDs, portable relative paths, sizes, SHA-256 and observed source file IDs;
- SKU/source version where applicable and per-archive retention owner;
- verified-database P1 evidence, code version, schema/role/commit and export_sync;
- repository ID/config fingerprint, recovery owner, dependencies and retention.

`BACKUP_SET_V1` is the terminal snapshot receipt. It embeds the plan and its
digest, repository identity, snapshot/root object IDs, upload/verification
status and observed/estimated metrics. `SNAPSHOT_VERIFIED` means Kopia's 100%
file verification passed. It does not mean an independent application restore
was performed. A separate `BACKUP_RESTORE_V1` document records that result and
binds the receipt digest. Reports never mutate a previously published receipt.

Canonical UTF-8 JSON is SHA-256 sealed. Publication fsyncs a newly owned temp
file then atomically links it to an absent final path. Existing records are
reused only if their entire contents match. This is corruption detection and
write-once application behavior, not a cryptographic signature against an
administrator who can replace both data and its hash. Protect the operations
directory and preserve receipts in independent administrative storage.

## State and Resume

The job event stream is an append-only digest chain:
`ATTEMPT_STARTED → STAGED → SNAPSHOT_WRITE_STARTED → SNAPSHOT_CREATED → VERIFIED`.
Failures and cancellations preserve evidence and partial artifacts. Existing
source identities must match the plan before copying. Only known, owned stage
partials can be repaired; unknown files stop Resume.

Image selection reuses P1 `ASSET_INDEX_V1` through `select_images`, requiring an
exact unambiguous SKU, QA PASS and source-version/hash. `image_bindings` creates
a portable `IMAGE_ASSET_BINDINGS_V1` manifest from that explicit selection; it
must be included as `kind=image_manifest`. Plan and restore validate all its
asset/SKU/version/path/hash relations. A full legacy manifest can be retained
separately as run evidence, without using its absolute paths as restore targets.
Content-addressed P1 asset IDs (`sha256:...`) are preserved; separate binding IDs
distinguish SKU/version/path references to identical image content.

Kopia snapshots are pinned and tagged with backup-set ID and source-content
digest. After an ambiguous write outcome, Resume queries the matching snapshot;
if none is provably committed it stops `SNAPSHOT_OUTCOME_UNRESOLVED`. It does not
blindly create a second snapshot. A completed receipt is reused only while its
bound snapshot still exists. A stale/unknown lock is never automatically stolen.

## Restore

Restore requires the receipt, its plan and matching repository. It checks
snapshot availability, planning/download/disk budgets, protected roots and an
absent target. It then verifies every restored path, size and SHA-256, full
SQLite integrity/FK, schema/commit, and the three projection hashes against the
database's export_sync. It emits an explicit portable root mapping and never
switches production. Missing assets are `ASSET_UNAVAILABLE`, never SOURCE_EMPTY.

Owned stage release requires both the matching restore receipt and a currently
available, revalidated independent restored copy. It reverifies the snapshot,
removes only exact staged files and empty directories, and records logical
bytes plus the observed free-volume delta. It grants no source cleanup rights.
