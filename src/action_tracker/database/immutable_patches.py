"""Append-only Raw/Normalized Fact and localization Patch primitives.

These helpers are intentionally small and explicit.  They support temporary
SQLite contracts and future Apply code, but do not mutate a production path
unless a caller deliberately supplies that path.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .connection import connect
from .schema import migrate_v2


LOCALIZATION_FIELDS = frozenset({"name", "cat1", "cat2", "spec", "description", "details"})
PATCH_EVENTS = ("PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED", "PATCH_REVOKED")
_TRANSITIONS = {
    None: {"PATCH_CREATED"},
    "PATCH_CREATED": {"PATCH_APPROVED", "PATCH_REVOKED"},
    "PATCH_APPROVED": {"PATCH_APPLIED", "PATCH_REVOKED"},
    "PATCH_APPLIED": set(),
    "PATCH_REVOKED": set(),
}


class ImmutablePatchError(ValueError):
    pass


def record_source_fact_versions(
    db_path: Path,
    *,
    run_id: str,
    official_sku: str,
    source_name: str,
    facts: Mapping[str, Mapping[str, Any]],
    observed_at: str | None = None,
) -> int:
    """Append raw/normalized fact pairs; existing versions are never updated."""
    if not run_id or not official_sku or not source_name:
        raise ImmutablePatchError("SOURCE_FACT_IDENTITY_MISSING")
    now = _now()
    inserted = 0
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        for field_name, values in facts.items():
            if not field_name:
                raise ImmutablePatchError("SOURCE_FACT_FIELD_MISSING")
            raw = _text(values.get("raw"))
            normalized = _text(values.get("normalized"))
            raw_hash = _digest(raw)
            normalized_hash = _digest(normalized)
            fact_id = _digest("|".join((run_id, official_sku, field_name, raw_hash, normalized_hash)))
            before = db.total_changes
            db.execute(
                """INSERT OR IGNORE INTO source_fact_versions
                (fact_id,run_id,official_sku,field_name,raw_value,normalized_value,raw_hash,normalized_hash,source_name,observed_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (fact_id, run_id, official_sku, field_name, raw, normalized, raw_hash, normalized_hash,
                 source_name, observed_at, now),
            )
            inserted += int(db.total_changes > before)
    return inserted


def create_localization_patch(
    db_path: Path,
    *,
    patch_id: str,
    official_sku: str,
    language: str,
    field_name: str,
    old_value: str | None,
    new_value: str,
    source_hash: str,
    source_allowlist: Iterable[str],
    created_by: str,
    evidence: Mapping[str, Any] | None = None,
) -> str:
    """Create a one-SKU/one-field patch and its immutable CREATED event."""
    if field_name not in LOCALIZATION_FIELDS:
        raise ImmutablePatchError("PATCH_FIELD_NOT_ALLOWED")
    if not patch_id or not official_sku or not language or not source_hash or not created_by:
        raise ImmutablePatchError("PATCH_IDENTITY_MISSING")
    if _text(old_value) == _text(new_value):
        raise ImmutablePatchError("PATCH_NO_VALUE_CHANGE")
    allowlist = tuple(sorted({str(item).strip() for item in source_allowlist if str(item).strip()}))
    if not allowlist:
        raise ImmutablePatchError("PATCH_SOURCE_ALLOWLIST_MISSING")
    now = _now()
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        try:
            db.execute(
                """INSERT INTO localization_patches
                (patch_id,official_sku,language,field_name,old_value,new_value,source_hash,source_allowlist,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (patch_id, official_sku, language, field_name, old_value, new_value, source_hash,
                 json.dumps(allowlist, ensure_ascii=False), created_by, now),
            )
        except Exception as exc:
            raise ImmutablePatchError("PATCH_ALREADY_EXISTS") from exc
        _append_event(db, patch_id, "PATCH_CREATED", created_by, evidence or {}, now)
    return patch_id


def append_patch_event(
    db_path: Path,
    *,
    patch_id: str,
    event_type: str,
    actor: str,
    evidence: Mapping[str, Any] | None = None,
) -> str:
    """Append the next valid event; no prior patch/event row is updated."""
    if event_type not in PATCH_EVENTS or event_type == "PATCH_CREATED":
        raise ImmutablePatchError("PATCH_EVENT_NOT_ALLOWED")
    now = _now()
    event_id = _digest("|".join((patch_id, event_type, actor, now, json.dumps(evidence or {}, sort_keys=True))))
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        if not db.execute("SELECT 1 FROM localization_patches WHERE patch_id=?", (patch_id,)).fetchone():
            raise ImmutablePatchError("PATCH_NOT_FOUND")
        current = db.execute(
            "SELECT event_type FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1",
            (patch_id,),
        ).fetchone()
        if event_type not in _TRANSITIONS.get(current[0] if current else None, set()):
            raise ImmutablePatchError("PATCH_INVALID_TRANSITION")
        _append_event(db, patch_id, event_type, actor, evidence or {}, now, event_id=event_id)
    return event_id


def validate_patch_apply(
    db_path: Path,
    *,
    patch_id: str,
    current_source_hash: str,
    source_name: str,
) -> dict[str, Any]:
    """Validate the non-mutating Apply Gate for one approved Patch."""
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        patch = db.execute(
            "SELECT official_sku,language,field_name,source_hash,source_allowlist FROM localization_patches WHERE patch_id=?",
            (patch_id,),
        ).fetchone()
        if not patch:
            raise ImmutablePatchError("PATCH_NOT_FOUND")
        latest = db.execute(
            "SELECT event_type,evidence_json FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1",
            (patch_id,),
        ).fetchone()
    if not latest or latest[0] != "PATCH_APPROVED":
        raise ImmutablePatchError("PATCH_NOT_APPROVED")
    if str(patch[3] or "") != str(current_source_hash or ""):
        raise ImmutablePatchError("PATCH_SOURCE_HASH_MISMATCH")
    try:
        allowlist = set(json.loads(patch[4] or "[]"))
    except json.JSONDecodeError as exc:
        raise ImmutablePatchError("PATCH_SOURCE_ALLOWLIST_INVALID") from exc
    if source_name not in allowlist:
        raise ImmutablePatchError("PATCH_SOURCE_NOT_ALLOWED")
    try:
        evidence = json.loads(latest[1] or "{}")
    except json.JSONDecodeError as exc:
        raise ImmutablePatchError("PATCH_APPROVAL_EVIDENCE_INVALID") from exc
    if evidence.get("field_name") and evidence.get("field_name") != patch[2]:
        raise ImmutablePatchError("PATCH_APPROVAL_FIELD_MISMATCH")
    return {"patch_id": patch_id, "official_sku": patch[0], "language": patch[1], "field_name": patch[2], "status": "APPROVED"}


def patch_status(db_path: Path, patch_id: str) -> str | None:
    with connect(Path(db_path)) as db:
        migrate_v2(Path(db_path))
        row = db.execute(
            "SELECT event_type FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1",
            (patch_id,),
        ).fetchone()
    return str(row[0]) if row else None


def _append_event(db, patch_id: str, event_type: str, actor: str, evidence: Mapping[str, Any], created_at: str, *, event_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO localization_patch_events(event_id,patch_id,event_type,actor,evidence_json,created_at) VALUES(?,?,?,?,?,?)",
        (event_id or _digest("|".join((patch_id, event_type, actor, created_at))), patch_id, event_type, actor,
         json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True), created_at),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
