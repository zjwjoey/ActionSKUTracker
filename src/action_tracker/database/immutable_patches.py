"""Append-only raw/normalized facts and immutable localization patches.

The active PRIMARY database and older closure fixtures use slightly different
column names. This module is the compatibility adapter: callers use one
contract while the writer inspects the existing schema.
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
    "PATCH_APPLIED": {"PATCH_REVOKED"},
    "PATCH_REVOKED": set(),
}


class ImmutablePatchError(ValueError):
    pass


def _columns(db, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def canonical_fact_version_table(db) -> str:
    """Return the sole authoritative fact-version table for this database."""
    tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "product_fact_versions" in tables:
        return "product_fact_versions"
    if "source_fact_versions" in tables:
        return "source_fact_versions"
    raise ImmutablePatchError("FACT_VERSION_TABLE_MISSING")


def record_source_fact_versions(
    db_path: Path, *, run_id: str, official_sku: str, source_name: str,
    facts: Mapping[str, Mapping[str, Any]], observed_at: str | None = None,
) -> int:
    """Append raw/normalized facts to the active canonical version store."""
    if not run_id or not official_sku or not source_name:
        raise ImmutablePatchError("SOURCE_FACT_IDENTITY_MISSING")
    now = _now(); inserted = 0
    with connect(Path(db_path)) as db:
        _migrate_compatible(Path(db_path)); table = canonical_fact_version_table(db)
        if table == "product_fact_versions":
            raw_payload = {field: _text(values.get("raw")) for field, values in facts.items()}
            normalized_payload = {field: _text(values.get("normalized")) for field, values in facts.items()}
            raw_hash = _digest(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            normalized_hash = _digest(json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            fact_id = _digest("|".join((run_id, official_sku, raw_hash, normalized_hash)))
            before = db.total_changes
            db.execute(
                """INSERT OR IGNORE INTO product_fact_versions
                (fact_id,official_sku,run_id,raw_fact_json,normalized_fact_json,raw_fact_hash,normalized_fact_hash,
                 raw_fact_available,normalization_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (fact_id, official_sku, run_id, json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
                 json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True), raw_hash, normalized_hash,
                 1, source_name, now),
            )
            inserted += int(db.total_changes > before)
        else:
            for field_name, values in facts.items():
                if not field_name: raise ImmutablePatchError("SOURCE_FACT_FIELD_MISSING")
                raw = _text(values.get("raw")); normalized = _text(values.get("normalized"))
                raw_hash = _digest(raw); normalized_hash = _digest(normalized)
                fact_id = _digest("|".join((run_id, official_sku, field_name, raw_hash, normalized_hash)))
                before = db.total_changes
                db.execute(
                    """INSERT OR IGNORE INTO source_fact_versions
                    (fact_id,run_id,official_sku,field_name,raw_value,normalized_value,raw_hash,normalized_hash,source_name,observed_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (fact_id, run_id, official_sku, field_name, raw, normalized, raw_hash, normalized_hash, source_name, observed_at, now),
                )
                inserted += int(db.total_changes > before)
    return inserted


def create_localization_patch(
    db_path: Path, *, patch_id: str, official_sku: str, language: str, field_name: str,
    old_value: str | None, new_value: str, source_hash: str, source_allowlist: Iterable[str] = (),
    created_by: str, evidence: Mapping[str, Any] | None = None, parent_patch_id: str | None = None,
    reason: str | None = None,
) -> str:
    """Create exactly one SKU/language/field patch and PATCH_CREATED event."""
    if field_name not in LOCALIZATION_FIELDS: raise ImmutablePatchError("PATCH_FIELD_NOT_ALLOWED")
    if not patch_id or not official_sku or not language or not source_hash or not created_by:
        raise ImmutablePatchError("PATCH_IDENTITY_MISSING")
    if _text(old_value) == _text(new_value): raise ImmutablePatchError("PATCH_NO_VALUE_CHANGE")
    allowlist = tuple(sorted({str(item).strip() for item in source_allowlist if str(item).strip()})); now = _now()
    with connect(Path(db_path)) as db:
        _migrate_compatible(Path(db_path)); columns = _columns(db, "localization_patches")
        values: dict[str, Any] = {"patch_id": patch_id, "official_sku": official_sku, "language": language,
            "field_name": field_name, "old_value": old_value, "new_value": new_value,
            "source_hash": source_hash, "created_by": created_by}
        if "source_allowlist" in columns:
            if not allowlist: raise ImmutablePatchError("PATCH_SOURCE_ALLOWLIST_MISSING")
            values["source_allowlist"] = json.dumps(allowlist, ensure_ascii=False)
        if "reason" in columns: values["reason"] = str(reason or "localization_patch")
        if "parent_patch_id" in columns: values["parent_patch_id"] = parent_patch_id
        if "revision" in columns:
            previous = db.execute("SELECT COALESCE(MAX(revision),0) FROM localization_patches WHERE official_sku=? AND language=? AND field_name=?", (official_sku, language, field_name)).fetchone()[0]
            values["revision"] = int(previous or 0) + 1
        if "created_at" in columns: values["created_at"] = now
        try:
            db.execute(f"INSERT INTO localization_patches({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
        except Exception as exc: raise ImmutablePatchError("PATCH_ALREADY_EXISTS") from exc
        _append_event(db, patch_id, "PATCH_CREATED", created_by, evidence or {}, now)
    return patch_id


def append_patch_event(db_path: Path, *, patch_id: str, event_type: str, actor: str, evidence: Mapping[str, Any] | None = None) -> str:
    """Append the next valid event; existing patch/event rows are immutable."""
    if event_type not in PATCH_EVENTS or event_type == "PATCH_CREATED": raise ImmutablePatchError("PATCH_EVENT_NOT_ALLOWED")
    now = _now(); event_id = _digest("|".join((patch_id, event_type, actor, now, json.dumps(evidence or {}, sort_keys=True))))
    with connect(Path(db_path)) as db:
        _migrate_compatible(Path(db_path))
        if not db.execute("SELECT 1 FROM localization_patches WHERE patch_id=?", (patch_id,)).fetchone(): raise ImmutablePatchError("PATCH_NOT_FOUND")
        current = _latest_event(db, patch_id)
        transitions = _TRANSITIONS
        # Revoke is a business lifecycle transition, not a schema feature.
        # Both the legacy and active PRIMARY shapes must support the same
        # append-only APPLIED -> REVOKED contract.
        if event_type not in transitions.get(current[0] if current else None, set()): raise ImmutablePatchError("PATCH_INVALID_TRANSITION")
        _append_event(db, patch_id, event_type, actor, evidence or {}, now, event_id=event_id)
    return event_id


def validate_patch_apply(
    db_path: Path, *, patch_id: str, current_source_hash: str, source_name: str,
    current_value: str | None = None, expected_base_commit_id: str | None = None,
) -> dict[str, Any]:
    """Validate an approved patch without mutating any production field."""
    with connect(Path(db_path)) as db:
        _migrate_compatible(Path(db_path))
        return _validate_patch_apply_in_connection(
            db, patch_id=patch_id, current_source_hash=current_source_hash,
            source_name=source_name, current_value=current_value,
            expected_base_commit_id=expected_base_commit_id,
        )


def _approval_event_record(db, patch_id: str) -> dict[str, Any] | None:
    """Read the latest approval event with actor and timestamp preserved."""
    columns = _columns(db, "localization_patch_events")
    time_column = "occurred_at" if "occurred_at" in columns else "created_at"
    evidence_column = "event_json" if "event_json" in columns else "evidence_json"
    row = db.execute(
        f"SELECT event_type,actor,{evidence_column},{time_column} "
        "FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1",
        (patch_id,),
    ).fetchone()
    if not row:
        return None
    return {"event_type": row[0], "actor": row[1], "evidence": row[2], "occurred_at": row[3]}


def _validate_patch_apply_in_connection(
    db,
    *,
    patch_id: str,
    current_source_hash: str,
    source_name: str,
    current_value: str | None = None,
    expected_base_commit_id: str | None = None,
) -> dict[str, Any]:
    """Single transaction-safe validation contract used by read/apply paths."""
    columns = _columns(db, "localization_patches")
    select = ["official_sku", "language", "field_name", "old_value", "new_value", "source_hash"]
    if "source_allowlist" in columns:
        select.append("source_allowlist")
    row = db.execute(
        f"SELECT {','.join(select)} FROM localization_patches WHERE patch_id=?", (patch_id,)
    ).fetchone()
    if not row:
        raise ImmutablePatchError("PATCH_NOT_FOUND")
    patch = dict(zip(select, row))
    approval = _approval_event_record(db, patch_id)
    if not approval or approval["event_type"] != "PATCH_APPROVED":
        raise ImmutablePatchError("PATCH_NOT_APPROVED")
    try:
        evidence = json.loads(approval["evidence"] or "{}")
    except json.JSONDecodeError as exc:
        raise ImmutablePatchError("PATCH_APPROVAL_EVIDENCE_INVALID") from exc
    if str(patch.get("source_hash") or "") != str(current_source_hash or ""):
        raise ImmutablePatchError("PATCH_SOURCE_HASH_MISMATCH")
    if "source_allowlist" in columns:
        try:
            allowlist = set(json.loads(patch.get("source_allowlist") or "[]"))
        except json.JSONDecodeError as exc:
            raise ImmutablePatchError("PATCH_SOURCE_ALLOWLIST_INVALID") from exc
        if not allowlist or source_name not in allowlist:
            raise ImmutablePatchError("PATCH_SOURCE_NOT_ALLOWED")
    else:
        # Active PRIMARY databases created before the allowlist column use an
        # explicit legacy adapter policy: approval must name the source and
        # the caller must validate against that exact source.  Missing source
        # evidence is never treated as "allow all".
        approved_source = str(evidence.get("source_name") or "").strip()
        if not approved_source or approved_source != str(source_name or "").strip():
            raise ImmutablePatchError("PATCH_LEGACY_SOURCE_NOT_ALLOWED")
    if evidence.get("field_name") and evidence.get("field_name") != patch["field_name"]:
        raise ImmutablePatchError("PATCH_APPROVAL_FIELD_MISMATCH")
    if expected_base_commit_id and evidence.get("base_commit_id") and evidence.get("base_commit_id") != expected_base_commit_id:
        raise ImmutablePatchError("STALE_LOCALIZATION_APPLY_BUNDLE")
    if current_value is not None and _text(current_value) != _text(patch.get("old_value")):
        raise ImmutablePatchError("PATCH_BASE_VALUE_MISMATCH")
    return {
        "patch_id": patch_id, "official_sku": patch["official_sku"],
        "language": patch["language"], "field_name": patch["field_name"],
        "old_value": patch.get("old_value"), "new_value": patch.get("new_value"),
        "source_hash": patch.get("source_hash"), "status": "APPROVED",
        "approval_actor": str(approval.get("actor") or ""),
        "approval_at": approval.get("occurred_at"),
        "approval_evidence": evidence,
    }


def patch_status(db_path: Path, patch_id: str) -> str | None:
    with connect(Path(db_path)) as db:
        _migrate_compatible(Path(db_path)); row = _latest_event(db, patch_id)
    return str(row[0]) if row else None


def _latest_event(db, patch_id: str):
    columns = _columns(db, "localization_patch_events")
    time_column = "occurred_at" if "occurred_at" in columns else "created_at"
    evidence_column = "event_json" if "event_json" in columns else "evidence_json"
    # Event rows are append-only.  Use insertion order as the authoritative
    # sequence: timestamps from older and newer writers may have different
    # precision (seconds versus microseconds), so lexical ordering by the
    # timestamp can otherwise report PATCH_APPLIED after a later revoke.
    return db.execute(f"SELECT event_type,{evidence_column} FROM localization_patch_events WHERE patch_id=? ORDER BY rowid DESC LIMIT 1", (patch_id,)).fetchone()


def _append_event(db, patch_id: str, event_type: str, actor: str, evidence: Mapping[str, Any], created_at: str, *, event_id: str | None = None) -> None:
    columns = _columns(db, "localization_patch_events")
    evidence_column = "event_json" if "event_json" in columns else "evidence_json"; time_column = "occurred_at" if "occurred_at" in columns else "created_at"
    values: dict[str, Any] = {"event_id": event_id or _digest("|".join((patch_id, event_type, actor, created_at))), "patch_id": patch_id,
        "event_type": event_type, "actor": actor, evidence_column: json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True), time_column: created_at}
    if "reason" in columns: values["reason"] = str(evidence.get("reason") or event_type)
    db.execute(f"INSERT INTO localization_patch_events({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))


def _now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")
def _text(value: Any) -> str: return "" if value is None else str(value)
def _digest(value: str) -> str: return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrate_compatible(path: Path) -> None:
    """Migrate using the role already recorded by an existing database."""
    role = "SHADOW"
    if path.exists():
        try:
            with connect(path) as db:
                row = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
                if row:
                    role = str(row[0])
        except Exception:
            pass
    migrate_v2(path, role=role)
