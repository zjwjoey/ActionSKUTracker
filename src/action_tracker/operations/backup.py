"""Existing Operations backup entry, immutable and memory bounded."""
from __future__ import annotations
from pathlib import Path
from .storage_foundation import verified_backup


def backup_sqlite(source: Path, destination: Path, *, run_id: str,
                  code_version: str | None = None) -> dict:
    return verified_backup(source, destination, run_id=run_id, code_version=code_version)
