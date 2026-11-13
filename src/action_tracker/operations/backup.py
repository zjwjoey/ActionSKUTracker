"""Existing Operations backup entry, immutable and memory bounded."""
from __future__ import annotations
from pathlib import Path
from .storage_foundation import verified_backup


def backup_sqlite(source: Path, destination: Path, *, run_id: str,
                  code_version: str | None = None, pool=None) -> dict:
    from .task_backup import current_task, manager_for
    active=current_task(source) or manager_for(source)
    if active is not None:return active[0].ensure()
    if pool is not None:
        return pool.backup(source,task_id=run_id,code_version=code_version)
    return verified_backup(source, destination, run_id=run_id, code_version=code_version)
