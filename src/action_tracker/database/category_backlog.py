"""Official-evidence-only category backlog queue."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .connection import connect
from .schema import migrate_v2


class CategoryBacklogError(ValueError):
    pass


def enqueue_category_missing(
    db_path: Path,
    *,
    queue_id: str,
    official_sku: str,
    cat1_es: str,
    cat2_es: str,
    suggested_cat2_zh: str = "",
    evidence_url: str = "",
    source_hash: str,
    actor: str = "system",
) -> str:
    if not queue_id or not official_sku or not source_hash:
        raise CategoryBacklogError("CATEGORY_QUEUE_IDENTITY_MISSING")
    now = _now()
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        db.execute(
            """INSERT INTO category_backlog
            (queue_id,official_sku,cat1_es,cat2_es,suggested_cat2_zh,evidence_url,source_hash,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (queue_id, official_sku, cat1_es, cat2_es, suggested_cat2_zh, evidence_url, source_hash, "CATEGORY_MISSING", now),
        )
        _event(db, queue_id, "CATEGORY_MISSING", actor, {"source": "official_category_gap"}, now)
    return queue_id


def decide_category_backlog(
    db_path: Path,
    *,
    queue_id: str,
    decision: str,
    value: str = "",
    actor: str,
    evidence_url: str,
) -> str:
    """Approve/reject only with official main-breadcrumb evidence."""
    if decision not in {"APPROVED", "REJECTED"}:
        raise CategoryBacklogError("CATEGORY_DECISION_INVALID")
    if not actor or not evidence_url or not evidence_url.startswith(("https://", "http://")):
        raise CategoryBacklogError("CATEGORY_OFFICIAL_EVIDENCE_REQUIRED")
    if decision == "APPROVED" and not value.strip():
        raise CategoryBacklogError("CATEGORY_APPROVAL_VALUE_MISSING")
    now = _now()
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        row = db.execute("SELECT status FROM category_backlog WHERE queue_id=?", (queue_id,)).fetchone()
        if not row:
            raise CategoryBacklogError("CATEGORY_QUEUE_NOT_FOUND")
        if row[0] not in {"CATEGORY_MISSING", "REVIEW_REQUIRED"}:
            raise CategoryBacklogError("CATEGORY_QUEUE_NOT_OPEN")
        db.execute(
            "UPDATE category_backlog SET status=?,decision_value=?,decided_by=?,decided_at=?,evidence_url=? WHERE queue_id=?",
            (decision, value, actor, now, evidence_url, queue_id),
        )
        _event(db, queue_id, decision, actor, {"evidence_url": evidence_url, "value": value}, now)
    return decision


def _event(db: sqlite3.Connection, queue_id: str, event_type: str, actor: str, evidence: Mapping[str, Any], now: str) -> None:
    event_id = hashlib.sha256(f"{queue_id}|{event_type}|{actor}|{now}|{json.dumps(dict(evidence), sort_keys=True)}".encode()).hexdigest()
    db.execute(
        "INSERT INTO category_backlog_events(event_id,queue_id,event_type,actor,evidence_json,created_at) VALUES(?,?,?,?,?,?)",
        (event_id, queue_id, event_type, actor, json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True), now),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
