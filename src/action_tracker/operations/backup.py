"""SQLite backup API wrapper; never copies a live WAL database byte-for-byte."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_sqlite(source: Path, destination: Path, *, run_id: str, code_version: str | None = None) -> dict[str, str]:
    if not source.exists():
        raise FileNotFoundError(f"DB_MISSING: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    # A backup file existing is not enough evidence for a production preflight.
    # Reopen it and validate both SQLite integrity and the expected database
    # identity before Collection is allowed to start.
    check = sqlite3.connect(destination)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = check.execute("PRAGMA foreign_key_check").fetchall()
        metadata = {
            str(row[0]): str(row[1])
            for row in check.execute("SELECT key,value FROM schema_metadata")
        }
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("BACKUP_VALIDATION_FAILED") from exc
    finally:
        check.close()
    if integrity != "ok":
        raise RuntimeError("BACKUP_INTEGRITY_FAILED")
    if foreign_keys:
        raise RuntimeError("BACKUP_FOREIGN_KEYS_FAILED")
    if metadata.get("schema_family") != "ACTION_SQLITE_DATA" or metadata.get("schema_version") != "2.0.0" or metadata.get("database_role") not in {"PRIMARY", "SHADOW"}:
        raise RuntimeError("BACKUP_METADATA_INVALID")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"run_id": run_id, "source": str(source), "backup_path": str(destination), "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(), "code_version": code_version or "unknown",
            "integrity": "PASS", "foreign_keys": "PASS", "schema_family": metadata["schema_family"],
            "schema_version": metadata["schema_version"], "database_role": metadata["database_role"]}
