"""Unified source-bound field repair preview/apply/verify/rollback service.

The service is intentionally independent of Excel.  It consumes normalized
records and an owner-approved manifest, then rechecks all hashes immediately
before a SQLite write.  Details patches are pair-scoped and reconstructed
without changing order, duplicate rows, or unrelated text.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..database.connection import connect
from ..database.patches import append_patch_event, create_patch
from ..services.hashing import localization_field_source_hash
from ..products.details_parser import parse_details

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
SOURCE_COLUMNS = {
    "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
    "description": "desc_es", "details": "details_es",
}
TARGET_COLUMNS = {
    "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh",
    "description": "desc_zh", "details": "details_zh",
}
DB_TARGET_COLUMNS = {
    "name": "name", "cat1": "cat1", "cat2": "cat2", "spec": "spec",
    "description": "description", "details": "details",
}
PREVIEW_STATES = frozenset({
    "NO_CHANGE", "WOULD_UPDATE", "BLOCKED_SOURCE_CHANGED", "BLOCKED_TARGET_CHANGED",
    "BLOCKED_POLICY_CHANGED", "BLOCKED_DETAILS_STRUCTURE_CHANGED", "BLOCKED_UNAPPROVED",
})


class RepairError(RuntimeError):
    """Fail-closed repair contract error."""


def value_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def policy_hash(policy: Mapping[str, Any] | str) -> str:
    if isinstance(policy, Mapping):
        raw = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        raw = str(policy)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _source_value(record: Mapping[str, Any], field: str) -> str:
    if field not in FIELDS:
        raise RepairError(f"UNKNOWN_FIELD:{field}")
    return _text(record.get(SOURCE_COLUMNS[field]))


def _target_value(record: Mapping[str, Any], field: str) -> str:
    if field not in FIELDS:
        raise RepairError(f"UNKNOWN_FIELD:{field}")
    return _text(record.get(TARGET_COLUMNS[field]))


def _detail_pair_patch(original: str, row: Mapping[str, Any]) -> tuple[str, str | None]:
    """Replace one target detail pair while preserving all other bytes."""
    pairs = parse_details(original)
    try:
        index = int(row["detail_pair_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairError("DETAIL_PAIR_INDEX_MISSING") from exc
    if index < 0 or index >= len(pairs):
        return original, "DETAIL_PAIR_INDEX_OUT_OF_RANGE"
    pair = pairs[index]
    if _text(row.get("expected_target_key")).strip() and pair.key_es.strip() != _text(row["expected_target_key"]).strip():
        return original, "DETAIL_TARGET_KEY_CHANGED"
    if "expected_target_value" in row and pair.value_es != _text(row.get("expected_target_value")):
        return original, "DETAIL_TARGET_VALUE_CHANGED"
    new_key = _text(row.get("target_key"))
    new_value = _text(row.get("target_value"))
    if not new_key:
        return original, "DETAIL_TARGET_KEY_EMPTY"
    parts = re.split(r"([;；|｜]|\r?\n)", original)
    segment_indexes = [index for index in range(0, len(parts), 2) if parts[index].strip()]
    if len(segment_indexes) != len(pairs):
        return original, "DETAIL_STRUCTURE_CHANGED"
    segment_index = segment_indexes[index]
    segment = parts[segment_index]
    left = segment[: len(segment) - len(segment.lstrip())]
    right = segment[len(segment.rstrip()):]
    body = segment.strip()
    separator = re.search(r"[:：]", body)
    if separator is None:
        rendered = new_key if not new_value else f"{new_key}: {new_value}"
    else:
        old_tail = body[separator.start():]
        match = re.match(r"[:：](\s*)(.*)$", old_tail, re.DOTALL)
        if match is None:
            return original, "DETAIL_STRUCTURE_CHANGED"
        rendered = f"{new_key}{old_tail[0]}{match.group(1)}{new_value}"
    parts[segment_index] = f"{left}{rendered}{right}"
    candidate = "".join(parts)
    new_pairs = parse_details(candidate)
    if len(new_pairs) != len(pairs):
        return original, "DETAIL_STRUCTURE_CHANGED"
    if [item.position for item in new_pairs] != [item.position for item in pairs]:
        return original, "DETAIL_STRUCTURE_CHANGED"
    return candidate, None


def _candidate_value(record: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[str, str | None]:
    field = _text(row.get("field"))
    current = _target_value(record, field)
    if field == "details" and str(row.get("operation") or "REPLACE_DETAILS_PAIR").upper() == "REPLACE_DETAILS_PAIR":
        return _detail_pair_patch(current, row)
    if "reviewed_value" not in row:
        raise RepairError("REVIEWED_VALUE_MISSING")
    return _text(row.get("reviewed_value")), None


def build_preview(
    records: Iterable[Mapping[str, Any]],
    manifest_rows: Iterable[Mapping[str, Any]],
    *,
    expected_policy_hash: str,
) -> list[dict[str, Any]]:
    """Build deterministic field-level preview rows without writing state."""
    by_sku = {str(row.get("sku") or row.get("official_sku") or ""): row for row in records}
    output: list[dict[str, Any]] = []
    for manifest in manifest_rows:
        sku = str(manifest.get("sku") or manifest.get("official_sku") or "").strip()
        field = str(manifest.get("field") or "").strip()
        row: dict[str, Any] = {**manifest, "sku": sku, "field": field}
        if field not in FIELDS or not sku or sku not in by_sku:
            row["status"] = "BLOCKED_TARGET_CHANGED"
            row["reason"] = "SKU_OR_FIELD_MISSING"
            output.append(row)
            continue
        record = by_sku[sku]
        source = _source_value(record, field)
        current = _target_value(record, field)
        current_source_hash = localization_field_source_hash(record, field)
        current_target_hash = value_hash(current)
        row.update({
            "current_source_hash": current_source_hash,
            "current_target_hash": current_target_hash,
            "source_value": source,
            "current_target_value": current,
            "policy_hash": expected_policy_hash,
        })
        if str(manifest.get("policy_manifest_hash") or "") != expected_policy_hash:
            row.update(status="BLOCKED_POLICY_CHANGED", reason="POLICY_HASH_MISMATCH")
            output.append(row)
            continue
        if str(manifest.get("expected_source_hash") or "") != current_source_hash:
            row.update(status="BLOCKED_SOURCE_CHANGED", reason="SOURCE_HASH_MISMATCH")
            output.append(row)
            continue
        if str(manifest.get("expected_target_hash") or "") != current_target_hash:
            row.update(status="BLOCKED_TARGET_CHANGED", reason="TARGET_HASH_MISMATCH")
            output.append(row)
            continue
        if not str(manifest.get("approved_by") or "").strip():
            row.update(status="BLOCKED_UNAPPROVED", reason="OWNER_APPROVAL_MISSING")
            output.append(row)
            continue
        try:
            reviewed, error = _candidate_value(record, manifest)
        except RepairError as exc:
            row.update(status="BLOCKED_DETAILS_STRUCTURE_CHANGED", reason=str(exc))
            output.append(row)
            continue
        if error:
            row.update(status="BLOCKED_DETAILS_STRUCTURE_CHANGED", reason=error)
            output.append(row)
            continue
        row["reviewed_value"] = reviewed
        row["reviewed_hash"] = value_hash(reviewed)
        row["status"] = "NO_CHANGE" if reviewed == current else "WOULD_UPDATE"
        row["reason"] = str(manifest.get("reason") or "OWNER_AUTHORIZED_LOCALIZATION_REPAIR")
        output.append(row)
    return output


def _db_source_record(db: Any, sku: str) -> dict[str, Any]:
    row = db.execute(
        """SELECT p.official_sku, es.name,es.cat1,es.cat2,es.spec,es.description,es.details,
                  zh.name,zh.cat1,zh.cat2,zh.spec,zh.description,zh.details
           FROM products p
           LEFT JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'
           LEFT JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'
           WHERE p.official_sku=?""", (sku,)
    ).fetchone()
    if row is None:
        raise RepairError(f"SKU_NOT_FOUND:{sku}")
    return {
        "sku": row[0], "name_es": row[1] or "", "cat1_es": row[2] or "", "cat2_es": row[3] or "",
        "spec_es": row[4] or "", "desc_es": row[5] or "", "details_es": row[6] or "",
        "name_zh": row[7] or "", "cat1_zh": row[8] or "", "cat2_zh": row[9] or "",
        "spec_zh": row[10] or "", "desc_zh": row[11] or "", "details_zh": row[12] or "",
    }


def apply_preview_to_database(
    db_path: Path,
    preview_rows: Iterable[Mapping[str, Any]],
    *,
    actor: str,
    run_id: str,
    expected_policy_hash: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply only owner-approved preview rows, with an in-transaction recheck."""
    rows = [dict(row) for row in preview_rows]
    if dry_run:
        return {"dry_run": True, "formal_write": False, "applied": 0, "blocked": 0}
    if not actor.strip():
        raise RepairError("APPLY_ACTOR_MISSING")
    updates = [row for row in rows if row.get("status") == "WOULD_UPDATE"]
    blocked = [row for row in rows if row.get("status") not in {"WOULD_UPDATE", "NO_CHANGE"}]
    if blocked:
        raise RepairError("APPLY_PREVIEW_HAS_BLOCKED_ROWS")
    stamp = datetime.now(timezone.utc).isoformat()
    applied = 0
    with connect(Path(db_path)) as db:
        for row in updates:
            if row.get("policy_hash") != expected_policy_hash or row.get("policy_manifest_hash") != expected_policy_hash:
                raise RepairError("POLICY_HASH_MISMATCH_AT_APPLY")
            sku, field = str(row["sku"]), str(row["field"])
            record = _db_source_record(db, sku)
            source_hash = localization_field_source_hash(record, field)
            current = _target_value(record, field)
            if source_hash != row.get("current_source_hash") or value_hash(current) != row.get("current_target_hash"):
                raise RepairError(f"FRESHNESS_CONFLICT:{sku}:{field}")
            reviewed = str(row.get("reviewed_value") or "")
            patch_id = create_patch(
                db, official_sku=sku, language="zh", field_name=field,
                old_value=current, new_value=reviewed, source_hash=source_hash,
                reason=f"repair_service:{run_id}:{row.get('reason') or ''}", created_by=actor,
            )
            append_patch_event(db, patch_id, "PATCH_APPROVED", actor=actor, reason="Owner approved repair preview", event_payload={"run_id": run_id})
            column = DB_TARGET_COLUMNS[field]
            db.execute(f"UPDATE product_localizations SET {column}=?, updated_at=? WHERE official_sku=? AND language='zh'", (reviewed, stamp, sku))
            db.execute("UPDATE localization_fields SET value=?,source=?,review_status=?,source_hash=?,updated_at=?,applied_commit_id=? WHERE official_sku=? AND language='zh' AND field_name=?", (reviewed, "repair_service", "HUMAN_APPROVED", source_hash, stamp, run_id, sku, field))
            db.execute("UPDATE localization_field_provenance SET value=?,source=?,review_status=?,source_hash=?,updated_at=?,applied_commit_id=?,approved_by=?,approved_at=?,freshness_status=? WHERE official_sku=? AND language='zh' AND field_name=?", (reviewed, "repair_service", "HUMAN_APPROVED", source_hash, stamp, run_id, actor, stamp, "CURRENT", sku, field))
            append_patch_event(db, patch_id, "PATCH_APPLIED", actor=actor, reason="Repair preview applied", event_payload={"run_id": run_id})
            applied += 1
    return {"dry_run": False, "formal_write": True, "applied": applied, "blocked": 0, "run_id": run_id}


def verify_database_apply(db_path: Path, preview_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in preview_rows if row.get("status") == "WOULD_UPDATE"]
    failures: list[str] = []
    with connect(Path(db_path)) as db:
        for row in rows:
            record = _db_source_record(db, str(row["sku"]))
            actual = _target_value(record, str(row["field"]))
            if value_hash(actual) != row.get("reviewed_hash"):
                failures.append(f"{row['sku']}:{row['field']}")
    return {"verified": not failures, "checked": len(rows), "failures": failures}


def rollback_database(db_path: Path, run_id: str, *, actor: str) -> dict[str, Any]:
    """Revoke and restore patches created by one repair run."""
    if not actor.strip():
        raise RepairError("ROLLBACK_ACTOR_MISSING")
    restored = 0
    with connect(Path(db_path)) as db:
        rows = db.execute(
            "SELECT lp.patch_id,lp.official_sku,lp.field_name,lp.old_value,lp.new_value FROM localization_patches lp JOIN localization_patch_events e ON e.patch_id=lp.patch_id WHERE e.event_type='PATCH_APPLIED' AND e.event_json LIKE ? AND NOT EXISTS (SELECT 1 FROM localization_patch_events r WHERE r.patch_id=lp.patch_id AND r.event_type='PATCH_REVOKED')",
            (f'%"run_id": "{run_id}"%',),
        ).fetchall()
        stamp = datetime.now(timezone.utc).isoformat()
        for patch_id, sku, field, old_value, new_value in rows:
            record = _db_source_record(db, str(sku))
            source_hash = localization_field_source_hash(record, str(field))
            if source_hash != str(db.execute("SELECT source_hash FROM localization_patches WHERE patch_id=?", (patch_id,)).fetchone()[0]):
                raise RepairError(f"ROLLBACK_SOURCE_CHANGED:{sku}:{field}")
            column = DB_TARGET_COLUMNS[str(field)]
            db.execute(f"UPDATE product_localizations SET {column}=?, updated_at=? WHERE official_sku=? AND language='zh'", (old_value, stamp, sku))
            db.execute("UPDATE localization_fields SET value=?,review_status=?,updated_at=?,applied_commit_id=? WHERE official_sku=? AND language='zh' AND field_name=?", (old_value, "REVOKED", stamp, run_id, sku, field))
            db.execute("UPDATE localization_field_provenance SET value=?,review_status=?,updated_at=?,approved_by=?,approved_at=?,freshness_status=? WHERE official_sku=? AND language='zh' AND field_name=?", (old_value, "REVOKED", stamp, actor, stamp, "CURRENT", sku, field))
            append_patch_event(db, patch_id, "PATCH_REVOKED", actor=actor, reason=f"Repair rollback:{run_id}", event_payload={"run_id": run_id, "restored_value": old_value})
            restored += 1
    return {"run_id": run_id, "restored": restored, "formal_write": restored > 0}


__all__ = [
    "FIELDS", "PREVIEW_STATES", "RepairError", "apply_preview_to_database", "build_preview",
    "policy_hash", "rollback_database", "value_hash", "verify_database_apply",
]
