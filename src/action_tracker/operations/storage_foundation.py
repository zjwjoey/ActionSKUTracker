"""Storage V1: bounded backups, reference checks and non-destructive previews."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


class StorageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def read_database(path: Path, *, immutable: bool = False):
    path = Path(path).resolve()
    if not path.is_file():
        raise StorageError("DB_MISSING")
    if immutable:
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise StorageError("OFFLINE_COPY_HAS_WAL")
    db = sqlite3.connect(path.as_uri() + "?mode=ro" +
                         ("&immutable=1" if immutable else ""), uri=True, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cache_size=-4096")
        yield db
    finally:
        db.close()


def database_identity(db) -> dict:
    metadata = dict(db.execute("SELECT key,value FROM schema_metadata"))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    head = None
    if "commit_batches" in tables:
        row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' "
                         "ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
        head = row[0] if row else None
    return {"metadata": metadata, "commit_id": head}


def source_signature(path: Path) -> dict:
    """Conservative same-head Resume check, including uncheckpointed WAL writes."""
    result = {}
    for suffix in ("", "-wal"):
        candidate = Path(str(path) + suffix)
        if candidate.is_file() and (not suffix or candidate.stat().st_size):
            stat = candidate.stat()
            result[suffix or "main"] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


@contextmanager
def storage_lock(directory: Path, *, run_id: str):
    """Same writer lock filename; never reclaim stale or unknown owners."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "daily-run.lock"
    token = uuid.uuid4().hex
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise StorageError("RUN_ALREADY_ACTIVE") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "run_id": run_id, "command": "storage-baseline",
                       "storage_token": token, "started_at": datetime.now(timezone.utc).isoformat()}, stream)
        yield
    finally:
        if path.exists():
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                owner = {}
            if owner.get("storage_token") == token:
                path.unlink()


def projection_audit(db_path: Path, projections: dict[str, Path]) -> dict:
    """Use current export_sync, not the hash in an older daily report."""
    if set(projections) != {"master", "known", "offline"}:
        raise StorageError("PROJECTION_SET_INCOMPLETE")
    with read_database(db_path) as db:
        identity = database_identity(db)
        m = identity["metadata"]
        if (m.get("schema_family") != "ACTION_SQLITE_DATA" or m.get("schema_version") != "2.0.0" or
                m.get("database_role") != "PRIMARY"):
            return {"status": "BLOCKED", "identity": identity, "reason": "PRIMARY_IDENTITY_INVALID"}
        if not identity["commit_id"]:
            return {"status": "UNKNOWN", "identity": identity, "reason": "HEAD_MISSING"}
        row = db.execute("SELECT * FROM export_sync WHERE commit_id=?", (identity["commit_id"],)).fetchone()
        sync = dict(row) if row else {}
    checks = {}
    for key, path in projections.items():
        expected = sync.get(key + "_sha256")
        actual = sha256_file(path) if Path(path).is_file() else None
        checks[key] = {"path": str(path), "expected": expected, "actual": actual,
                       "matches": bool(expected) and actual == expected and
                       sync.get(key + "_status") == "SUCCESS"}
    with read_database(db_path) as db:
        final = database_identity(db)
        row = db.execute("SELECT * FROM export_sync WHERE commit_id=?", (identity["commit_id"],)).fetchone()
        final_sync = dict(row) if row else {}
    stable = final == identity and final_sync == sync
    return {"status": "PASS" if stable and sync.get("status") == "SUCCESS" and
            all(r["matches"] for r in checks.values()) else "BLOCKED", "identity": identity,
            "stable": stable, "export_sync": sync, "projections": checks}


def verified_backup(source: Path, destination: Path, *, run_id: str,
                    code_version: str | None = None, max_seconds: float = 180) -> dict:
    """One immutable Backup API artifact; no permanent restore-check duplicate."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise StorageError("DB_MISSING")
    if source == destination:
        raise StorageError("BACKUP_SOURCE_IS_DESTINATION")
    destination.parent.mkdir(parents=True, exist_ok=True)
    asset_path = Path(str(destination) + ".asset.json")
    if destination.exists() and asset_path.is_file():
        try:
            existing = json.loads(asset_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise StorageError("BACKUP_ALREADY_EXISTS") from None
        if (existing.get("contract") == "BACKUP_ASSET_V1" and existing.get("run_id") == run_id and
                existing.get("source") == str(source) and existing.get("integrity") == "PASS" and
                existing.get("foreign_keys") == "PASS" and
                existing.get("code_version") == (code_version or "unknown") and
                existing.get("source_signature") == source_signature(source) and
                sha256_file(destination) == existing.get("sha256")):
            with read_database(source) as db:
                current = database_identity(db)
            if (current["commit_id"] == existing.get("commit_id") and
                    source_signature(source) == existing.get("source_signature")):
                return {**existing, "reused": True}
        raise StorageError("BACKUP_ALREADY_EXISTS")
    if Path(str(destination) + ".asset.json").exists() or Path(str(destination) + ".failure.json").exists():
        raise StorageError("BACKUP_ALREADY_EXISTS")
    wal = Path(str(source) + "-wal")
    needed = source.stat().st_size + (wal.stat().st_size if wal.exists() else 0) + 64 * 1024**2
    if shutil.disk_usage(destination.parent).free < needed:
        raise StorageError("BACKUP_DISK_BUDGET_EXCEEDED")
    try:
        destination.open("xb").close()
    except FileExistsError as exc:
        raise StorageError("BACKUP_ALREADY_EXISTS") from exc
    started = time.monotonic()
    signature = source_signature(source)
    try:
        with read_database(source) as src:
            identity = database_identity(src)
            data_version = src.execute("PRAGMA data_version").fetchone()[0]
            m = identity["metadata"]
            if not (m.get("schema_family") == "ACTION_SQLITE_DATA" and
                    m.get("schema_version") == "2.0.0" and
                    m.get("database_role") in {"PRIMARY", "SHADOW"}):
                raise StorageError("BACKUP_METADATA_INVALID")
            dst = sqlite3.connect(destination)
            try:
                def progress(_status, _remaining, _total):
                    if time.monotonic() - started > max_seconds:
                        raise StorageError("BACKUP_TIME_BUDGET_EXCEEDED")
                src.backup(dst, pages=256, progress=progress, sleep=0.01)
                dst.execute("PRAGMA journal_mode=DELETE")
            finally:
                dst.close()
            after = database_identity(src)
            source_changed = src.execute("PRAGMA data_version").fetchone()[0] != data_version
        with read_database(destination, immutable=True) as check:
            restored_identity = database_identity(check)
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = check.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise StorageError("BACKUP_INTEGRITY_FAILED" if integrity != "ok" else "BACKUP_FOREIGN_KEYS_FAILED")
        if source_changed or identity != after or identity != restored_identity or source_signature(source) != signature:
            raise StorageError("BACKUP_HEAD_CHANGED")
        result = {"run_id": run_id, "source": str(source), "backup_path": str(destination),
                  "sha256": sha256_file(destination), "size_bytes": destination.stat().st_size,
                  "created_at": datetime.now(timezone.utc).isoformat(), "code_version": code_version or "unknown",
                  "integrity": "PASS", "foreign_keys": "PASS",
                  **{k: m[k] for k in ("schema_family", "schema_version", "database_role")},
                  "commit_id": identity["commit_id"], "contract": "BACKUP_ASSET_V1", "owner_task": run_id,
                  "recovery_required": True, "reference_status": "UNKNOWN", "cleanup_authorized": False,
                  "restore_rehearsed": False}
        result["source_signature"] = signature
    except Exception as exc:
        with Path(str(destination) + ".failure.json").open("x", encoding="utf-8") as stream:
            json.dump({"contract": "BACKUP_FAILURE_V1", "run_id": run_id, "backup_path": str(destination),
                       "error_type": type(exc).__name__, "status": "INCOMPLETE_RETAIN_FOR_REVIEW"}, stream)
        raise
    with Path(str(destination) + ".asset.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def rehearse_restore(backup: Path, manifest: dict, output_dir: Path) -> dict:
    backup, output_dir = Path(backup).resolve(), Path(output_dir).resolve()
    if sha256_file(backup) != manifest["sha256"]:
        raise StorageError("BACKUP_HASH_MISMATCH")
    scratch = output_dir / ("restore-scratch-" + uuid.uuid4().hex)
    scratch.mkdir(parents=True, exist_ok=False)
    restored = scratch / "restored.sqlite3"
    try:
        shutil.copy2(backup, restored)  # Closed standalone backup, never the live DB.
        if sha256_file(restored) != manifest["sha256"]:
            raise StorageError("RESTORE_BYTES_DIFFER")
        with read_database(restored, immutable=True) as db:
            identity = database_identity(db)
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            fk = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or fk or identity["commit_id"] != manifest["commit_id"]:
            raise StorageError("RESTORE_VALIDATION_FAILED")
        return {"status": "PASS", "backup_sha256": manifest["sha256"], "commit_id": identity["commit_id"],
                "integrity": "PASS", "foreign_keys": "PASS", "permanent_restore_copy_created": False}
    finally:
        if scratch.resolve().parent != output_dir or not scratch.name.startswith("restore-scratch-"):
            raise StorageError("RESTORE_SCRATCH_BOUNDARY_VIOLATION")
        shutil.rmtree(scratch)


def retention_preview(manifests: list[dict], *, keep_latest: int = 7) -> dict:
    if isinstance(keep_latest, bool) or not isinstance(keep_latest, int) or keep_latest < 1:
        raise StorageError("INVALID_RETENTION_COUNT")
    ordered = sorted(manifests, key=lambda r: r.get("created_at", ""), reverse=True)
    results = []
    group_ordinals = {}
    for item in ordered:
        group = (item.get("source"), item.get("schema_family"), item.get("schema_version"),
                 item.get("database_role"), item.get("purpose", "PRE_RUN_RECOVERY"))
        ordinal = group_ordinals.get(group, 0)
        group_ordinals[group] = ordinal + 1
        verified = (item.get("contract") == "BACKUP_ASSET_V1" and item.get("integrity") == "PASS" and
                    item.get("foreign_keys") == "PASS" and len(str(item.get("sha256", ""))) == 64)
        protected = (ordinal < keep_latest or item.get("recovery_required") is not False or
                     item.get("reference_status") != "NO_REFERENCES" or not verified or
                     item.get("task_completed") is not True or item.get("restore_rehearsed") is not True)
        results.append({"backup_path": item.get("backup_path"),
                        "status": "RETAIN" if protected else "ARCHIVE_REVIEW_CANDIDATE",
                        "size_bytes": item.get("size_bytes"), "delete_allowed": False,
                        "retention_group": list(group)})
    return {"status": "PREVIEW_ONLY", "keep_latest": keep_latest, "assets": results, "released_bytes": 0}


def resolve_asset(asset: dict, roots: dict[str, str]) -> dict:
    root_value = roots.get(asset.get("root_id"))
    if not root_value:
        return {"status": "ASSET_UNAVAILABLE", "reason": "ROOT_UNAVAILABLE"}
    raw = str(asset.get("relative_path", ""))
    relative = PurePosixPath(raw.replace("\\", "/"))
    if (not raw or relative.is_absolute() or PureWindowsPath(raw).drive or ".." in relative.parts or
            any(":" in p for p in relative.parts)):
        raise StorageError("ASSET_PATH_ESCAPE")
    root = Path(root_value).resolve()
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise StorageError("ASSET_PATH_ESCAPE")
    if not path.is_file():
        return {"status": "ASSET_UNAVAILABLE", "path": str(path)}
    expected = asset.get("sha256")
    if expected and sha256_file(path) != expected:
        return {"status": "HASH_MISMATCH", "path": str(path)}
    return {"status": "AVAILABLE", "path": str(path), "asset_id": asset.get("asset_id"),
            "source_version": asset.get("source_version"), "hash_verified": bool(expected),
            "source_quality_promoted": False}


def image_asset_index(manifest_path: Path, data_root: Path) -> dict:
    """Derived manifest only: preserve SKU/source/version bindings and file bytes."""
    root = Path(data_root).resolve()
    assets = []
    with Path(manifest_path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            original = Path(row["master_image_path"])
            resolved = original.resolve()
            if not resolved.is_relative_to(root):
                raise StorageError("IMAGE_MANIFEST_PATH_OUTSIDE_DATA_ROOT")
            content_hash = row.get("master_hash") or None
            assets.append({"asset_id": "sha256:" + content_hash if content_hash else
                           "image:" + row["sku"], "asset_type": "IMAGE_MASTER",
                           "root_id": "data", "relative_path": resolved.relative_to(root).as_posix(),
                           "legacy_path": str(original), "sha256": content_hash,
                           "sku": row["sku"], "canonical_id": row.get("canonical_id"),
                           "source_url": row.get("source_image_url"),
                           "source_version": row.get("source_hash"), "qa_status": row.get("qa_status"),
                           "hash_evidence": "EXISTING_MANIFEST_NOT_NEW_FULL_AUDIT"})
    return {"contract": "ASSET_INDEX_V1", "root_id": "data", "assets": assets,
            "fact_records_modified": False}


def configured_roots(cfg: dict) -> dict[str, str]:
    data = Path(cfg["project_root"]).resolve()
    configured = (cfg.get("storage_foundation") or {}).get("roots") or {}
    roots = {"data": str(data)}
    for name, default in (("code", Path(__file__).resolve().parents[3]),
                          ("archive", None), ("temp", data / "runtime/temp")):
        raw = configured.get(name, default)
        if name == "code" and raw is None:
            raw = default
        if raw is not None:
            value = Path(raw)
            roots[name] = str(value if value.is_absolute() else data / value)
    return roots
