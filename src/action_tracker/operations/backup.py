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
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {"run_id": run_id, "source": str(source), "backup_path": str(destination), "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(), "code_version": code_version or "unknown"}
