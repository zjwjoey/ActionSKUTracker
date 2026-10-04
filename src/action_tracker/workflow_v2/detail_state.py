"""Independent detail freshness state; never derives freshness from last_seen."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..database.connection import connect


def mark_detail_success(path: Path, sku: str, *, run_id: str, source_hash: str, at: str | None = None) -> None:
    stamp = at or datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        db.execute("""INSERT INTO product_detail_state(official_sku,detail_last_success_at,detail_last_success_run_id,detail_source_hash,detail_status,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(official_sku) DO UPDATE SET detail_last_success_at=excluded.detail_last_success_at,
            detail_last_success_run_id=excluded.detail_last_success_run_id,detail_source_hash=excluded.detail_source_hash,
            detail_status=excluded.detail_status,updated_at=excluded.updated_at""", (str(sku), stamp, run_id, source_hash, "COMPLETE", stamp))


def mark_detail_pending(path: Path, sku: str, *, status: str = "PENDING", at: str | None = None) -> None:
    stamp = at or datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        db.execute("""INSERT INTO product_detail_state(official_sku,detail_status,updated_at) VALUES(?,?,?)
            ON CONFLICT(official_sku) DO UPDATE SET detail_status=excluded.detail_status,updated_at=excluded.updated_at""", (str(sku), status, stamp))


def detail_is_stale(path: Path, sku: str, *, now: datetime | None = None, max_age_days: int = 7, source_hash: str | None = None) -> bool:
    with connect(path) as db:
        row = db.execute("SELECT detail_last_success_at,detail_source_hash,detail_status FROM product_detail_state WHERE official_sku=?", (str(sku),)).fetchone()
    if not row or str(row[2] or "").upper() != "COMPLETE" or not row[0]: return True
    if source_hash and str(row[1] or "") != str(source_hash): return True
    try: last = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError: return True
    current = now or datetime.now(timezone.utc)
    if last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
    return current - last > timedelta(days=max(0, int(max_age_days)))
