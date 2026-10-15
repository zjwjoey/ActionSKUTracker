"""Prepare a CORE set through existing P1 APIs under the shared writer lock."""
from __future__ import annotations

import shutil
from pathlib import Path

from .backup_sets import IDENTIFIER, file_identity, make_plan, read_json, safe_path, write_immutable
from .storage_foundation import StorageError, projection_audit, verified_backup, sha256_file, storage_lock


def capture_core(*, source: Path, projections: dict[str, Path], destination: Path,
                 writer_lock_directory: Path, run_id: str, code_version: str,
                 repository: dict, config_version: str, recovery_owner: str,
                 retention: dict, max_bytes: int = 8 * 1024**3) -> tuple[dict, dict]:
    """Caller explicitly opts in; never called by the ordinary daily pipeline.

    Production use changes only the existing shared lock temporarily. It does
    not checkpoint or open PRIMARY through a raw filesystem copy.
    """
    if not IDENTIFIER.fullmatch(run_id):
        raise StorageError("BACKUP_ID_INVALID")
    source = safe_path(source)
    destination = safe_path(destination, file=False)
    if not code_version or code_version == "unknown":
        raise StorageError("BACKUP_CODE_VERSION_REQUIRED")
    if source.is_relative_to(destination) or destination.is_relative_to(source.parent):
        raise StorageError("CAPTURE_ROOT_OVERLAP")
    if set(projections) != {"master", "known", "offline"}:
        raise StorageError("PROJECTION_SET_INCOMPLETE")
    required = source.stat().st_size + sum(Path(p).stat().st_size for p in projections.values())
    if required > max_bytes:
        raise StorageError("CAPTURE_BUDGET_EXCEEDED")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < required + 128 * 1024**2:
        raise StorageError("CAPTURE_DISK_BUDGET_EXCEEDED")
    with storage_lock(writer_lock_directory, run_id=run_id):
        before = projection_audit(source, projections)
        if before.get("status") != "PASS":
            raise StorageError("CAPTURE_PROJECTION_BLOCKED")
        context = {"contract": "CORE_CAPTURE_CONTEXT_V1", "run_id": run_id,
                   "source": str(source), "identity": before["identity"], "code_version": code_version,
                   "projection_sources": {k: str(safe_path(p)) for k, p in projections.items()}}
        if destination.exists() and not (destination / "capture-context.json").exists():
            raise StorageError("CAPTURE_DIRECTORY_UNOWNED")
        destination.mkdir(exist_ok=True)
        write_immutable(destination / "capture-context.json", context)
        backup = destination / "primary.sqlite3"
        p1 = verified_backup(source, backup, run_id=run_id, code_version=code_version)
        assets = [{"asset_id": "primary-backup", "root_id": "core", "relative_path": backup.name,
                   "kind": "verified_database", "sha256": p1["sha256"]}]
        for key, raw_path in projections.items():
            path = safe_path(raw_path)
            target = destination / (key + path.suffix)
            identity = file_identity(path)
            partial = destination / (".partial-projection-" + key)
            if partial.exists():
                safe_path(partial).unlink()
            if not target.exists():
                with path.open("rb") as src, partial.open("xb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                    out.flush()
                    import os
                    os.fsync(out.fileno())
                if sha256_file(partial) != before["projections"][key]["actual"]:
                    raise StorageError("CAPTURE_PROJECTION_CHANGED")
                # Directory is exclusively held by the shared writer lock;
                # an unknown final file is never overwritten.
                if target.exists():
                    raise StorageError("CAPTURE_PROJECTION_TARGET_CONFLICT")
                partial.rename(target)
            if file_identity(path) != identity or sha256_file(target) != before["projections"][key]["actual"]:
                raise StorageError("CAPTURE_PROJECTION_CHANGED")
            assets.append({"asset_id": key, "root_id": "core", "relative_path": target.name,
                           "kind": "projection", "projection_name": key, "sha256": sha256_file(target)})
        after = projection_audit(source, projections)
        if before != after or p1["commit_id"] != before["identity"]["commit_id"]:
            raise StorageError("CAPTURE_SOURCE_CHANGED")
        write_immutable(destination / "projection-evidence.json", before)
        # P1 verified_backup itself verifies integrity/FK and commit after the
        # SQLite Backup API. The independent Kopia restore gets its own receipt.
        write_immutable(destination / "p1-recovery-record.json", {
            "contract": "P1_CAPTURE_RECOVERY_V1", "backup_sha256": p1["sha256"],
            "commit_id": p1["commit_id"], "integrity": p1["integrity"],
            "foreign_keys": p1["foreign_keys"], "kopia_restore_status": "NOT_TESTED"})
    for filename, kind in (("capture-context.json", "run_manifest"),
                           ("projection-evidence.json", "run_manifest"),
                           ("p1-recovery-record.json", "recovery_record")):
        assets.append({"asset_id": filename.replace(".", "-"), "root_id": "core", "relative_path": filename,
                       "kind": kind, "sha256": sha256_file(destination / filename)})
    roots = {"core": str(destination)}
    return make_plan(backup_set_id=run_id, run_id=run_id, profile="CORE_BACKUP", roots=roots,
                     assets=assets, repository=repository, config_version=config_version,
                     recovery_owner=recovery_owner, retention=retention, max_logical_bytes=max_bytes), roots
