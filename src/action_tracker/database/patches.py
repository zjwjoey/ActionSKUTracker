"""Immutable, field-level localization patch revisions.

Patch rows are facts about a proposed change.  Their lifecycle is represented
only by append-only events; no UPDATE or DELETE is permitted by the SQLite
schema triggers created in :mod:`database.schema`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .connection import connect


PATCH_FIELDS = frozenset({"name", "cat1", "cat2", "spec", "description", "details"})
PATCH_EVENTS = frozenset({"PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED", "PATCH_REVOKED"})
_NEXT_EVENTS = {
    "PATCH_CREATED": {"PATCH_APPROVED", "PATCH_REVOKED"},
    "PATCH_APPROVED": {"PATCH_APPLIED", "PATCH_REVOKED"},
    "PATCH_APPLIED": {"PATCH_REVOKED"},
    "PATCH_REVOKED": set(),
}


class PatchError(ValueError):
    """Invalid or out-of-order immutable patch transition."""


def create_patch(
    db_or_path: Any,
    *,
    official_sku: str,
    language: str,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    source_hash: str,
    reason: str,
    created_by: str,
    parent_patch_id: str | None = None,
    patch_id: str | None = None,
    occurred_at: str | None = None,
) -> str:
    """Create a new revision and its ``PATCH_CREATED`` event."""
    if field_name not in PATCH_FIELDS:
        raise PatchError("PATCH_FIELD_INVALID")
    if language not in {"zh", "es"}:
        raise PatchError("PATCH_LANGUAGE_INVALID")
    if not str(official_sku or "").strip() or not str(source_hash or "").strip():
        raise PatchError("PATCH_IDENTITY_MISSING")
    if not str(reason or "").strip() or not str(created_by or "").strip():
        raise PatchError("PATCH_AUDIT_FIELDS_MISSING")
    stamp = occurred_at or datetime.now(timezone.utc).isoformat()
    patch_id = patch_id or uuid.uuid4().hex

    def _create(db):
        resolved_parent = parent_patch_id
        if resolved_parent:
            parent = db.execute("SELECT official_sku,language,field_name,revision FROM localization_patches WHERE patch_id=?", (resolved_parent,)).fetchone()
            if not parent:
                raise PatchError("PATCH_PARENT_NOT_FOUND")
            if (str(parent[0]), str(parent[1]), str(parent[2])) != (str(official_sku), language, field_name):
                raise PatchError("PATCH_PARENT_FIELD_MISMATCH")
            revision = int(parent[3]) + 1
        else:
            row = db.execute(
                "SELECT patch_id,revision FROM localization_patches WHERE official_sku=? AND language=? AND field_name=? ORDER BY revision DESC LIMIT 1",
                (official_sku, language, field_name),
            ).fetchone()
            resolved_parent = str(row[0]) if row else None
            revision = int(row[1]) + 1 if row else 1
        db.execute(
            """
            INSERT INTO localization_patches(
                patch_id,parent_patch_id,official_sku,language,field_name,old_value,new_value,
                source_hash,reason,created_by,created_at,revision
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (patch_id, resolved_parent, official_sku, language, field_name, old_value, new_value,
             source_hash, reason, created_by, stamp, revision),
        )
        _append_event(db, patch_id, "PATCH_CREATED", created_by, reason, stamp, {"revision": revision})
        return patch_id

    return _with_db(db_or_path, _create)


def append_patch_event(
    db_or_path: Any, patch_id: str, event_type: str, *, actor: str, reason: str = "",
    occurred_at: str | None = None, event_payload: dict[str, Any] | None = None,
) -> str:
    """Append one valid lifecycle event; never mutates the patch row."""
    if event_type not in PATCH_EVENTS or event_type == "PATCH_CREATED":
        raise PatchError("PATCH_EVENT_INVALID")
    if not str(actor or "").strip():
        raise PatchError("PATCH_ACTOR_MISSING")
    stamp = occurred_at or datetime.now(timezone.utc).isoformat()

    def _append(db):
        if not db.execute("SELECT 1 FROM localization_patches WHERE patch_id=?", (patch_id,)).fetchone():
            raise PatchError("PATCH_NOT_FOUND")
        state = patch_state(db, patch_id)
        if event_type not in _NEXT_EVENTS.get(state, set()):
            raise PatchError(f"PATCH_TRANSITION_INVALID:{state}->{event_type}")
        _append_event(db, patch_id, event_type, actor, reason, stamp, event_payload or {})
        return event_type

    return _with_db(db_or_path, _append)


def patch_state(db: Any, patch_id: str) -> str:
    row = db.execute(
        # occurred_at is audit data and may legitimately have the same
        # precision for two transitions.  SQLite rowid preserves append order
        # without making the public event id mutable or time-dependent.
        "SELECT event_type FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1",
        (patch_id,),
    ).fetchone()
    if not row:
        raise PatchError("PATCH_EVENT_MISSING")
    return str(row[0])


def _append_event(db: Any, patch_id: str, event_type: str, actor: str, reason: str, stamp: str, payload: dict[str, Any]) -> None:
    event_id = uuid.uuid4().hex
    event = {"patch_id": patch_id, "event_type": event_type, "actor": actor, "reason": reason, **payload}
    db.execute(
        "INSERT INTO localization_patch_events(event_id,patch_id,event_type,actor,reason,event_json,occurred_at) VALUES(?,?,?,?,?,?,?)",
        (event_id, patch_id, event_type, actor, reason, json.dumps(event, ensure_ascii=False, sort_keys=True), stamp),
    )


def _with_db(db_or_path: Any, callback):
    if hasattr(db_or_path, "execute"):
        return callback(db_or_path)
    with connect(db_or_path) as db:
        return callback(db)
