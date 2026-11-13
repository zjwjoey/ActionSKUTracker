from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def connect(path: Path):
    """Open a SQLite connection with deterministic commit and close semantics.

    ``sqlite3.Connection``'s context manager commits/rolls back but does not
    close the connection.  That leaked WAL handles on Windows and prevented
    atomic replacement of staged cutover databases.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    from ..operations.task_connection import TaskConnection
    conn = sqlite3.connect(path, factory=TaskConnection)
    conn.backup_path = Path(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.backup_ready = True
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()
