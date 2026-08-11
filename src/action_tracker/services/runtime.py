"""Run-scoped safety primitives: Madrid business date and single-run locking."""
from __future__ import annotations

import json
import os
import atexit
import socket
import sys
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

    def acquire(self, run_id: str, *, command: str = "daily-run") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._reclaim_stale()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"RUN_ALREADY_ACTIVE: {self.path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "command": command, "pid": os.getpid(),
                       "hostname": socket.gethostname(), "started_at": datetime.now().isoformat()}, f)
        self.acquired = True
        atexit.register(self.release)

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _reclaim_stale(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        pid = data.get("pid")
        # A lock belonging to a live local process is never reclaimed merely
        # because a long collection exceeded the configured stale threshold.
        if isinstance(pid, int) and _pid_alive(pid):
            return
        age = datetime.now().timestamp() - self.path.stat().st_mtime
        if age > self.stale_after.total_seconds() or pid is not None:
            self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows does not support POSIX signal 0: os.kill(pid, 0) can
        # terminate the process.  Query the process handle instead.
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
