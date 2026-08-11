"""Run-scoped safety primitives: Madrid business date and single-run locking."""
from __future__ import annotations

import json
import os
import atexit
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")


def madrid_now() -> datetime:
    return datetime.now(MADRID)


def observation_date() -> str:
    return madrid_now().date().isoformat()


class RunLock:
    """Atomic cross-process lock; stale locks are safely reclaimed."""

    def __init__(self, directory: Path, *, stale_minutes: int = 180):
        self.path = directory / "daily-run.lock"
        self.stale_after = timedelta(minutes=stale_minutes)
        self.acquired = False

    def acquire(self, run_id: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._reclaim_stale()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"RUN_ALREADY_ACTIVE: {self.path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "started_at": datetime.now().isoformat()}, f)
        self.acquired = True
        atexit.register(self.release)

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _reclaim_stale(self) -> None:
        if not self.path.exists():
            return
        age = datetime.now().timestamp() - self.path.stat().st_mtime
        if age > self.stale_after.total_seconds():
            self.path.unlink(missing_ok=True)
