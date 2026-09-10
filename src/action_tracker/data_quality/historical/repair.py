from __future__ import annotations

"""Auditable historical repair routing and fixture verification.

Audit issues are not automatically equivalent to executable field updates.
This module separates routing, review, fixture-only verification and formal
correction preparation.  It never writes official facts in a PRIMARY database.
"""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from ...database.connection import connect
from ...database.immutable_patches import (
    ImmutablePatchError,
    create_localization_patch,
    validate_patch_approval_actor,
)
from ...services.normalization import normalize_official_text
from ..repository import DataQualityRepository, now_utc
from .audit import audit_history


class RepairError(RuntimeError):
    pass


def validate_repair_apply_actor(actor: str) -> str:
    """Validate the human actor that applies an already reviewed fixture batch.

    Approval and application are separate authority boundaries.  V1 only
    permits a named human actor here; service/system/model identities must not
    be able to apply a historical repair batch.
    """
    normalized = str(actor or "").strip()
    if normalized.startswith("human:") and normalized[len("human:"):].strip():
        return normalized
    raise RepairError("REPAIR_APPLIER_NOT_AUTHORIZED")


ROUTE_DIRECT = "DIRECT_FIELD_CORRECTION"
ROUTE_TEXT = "OFFICIAL_TEXT_CORRECTION"
ROUTE_PATCH = "LOCALIZATION_PATCH"
ROUTE_CATEGORY = "CATEGORY_BACKLOG"
ROUTE_IDENTITY = "IDENTITY_REVIEW"
ROUTE_ARCHIVE = "ARCHIVE_UNRESOLVED"
ROUTE_NONE = "NO_AUTOMATIC_REPAIR"

_APPLYABLE = frozenset({ROUTE_DIRECT, ROUTE_TEXT, ROUTE_PATCH})
_LOCALIZATION_FIELDS = frozenset({"name", "cat1", "cat2", "spec", "description", "details"})


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _latest_commit(db: sqlite3.Connection) -> str | None:
    try:
        row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row else None


def _route(issue: Mapping[str, Any], proposed: Any) -> str:
    issue_type = str(issue.get("issue_type") or "")
    field = str(issue.get("field_name") or "")
    if issue_type == "INVALID_ORIGINAL_PRICE":
        return ROUTE_DIRECT
    if issue_type in {"HTML_CONTAMINATION", "UI_TEXT_CONTAMINATION"}:
        return ROUTE_TEXT if proposed is not None else ROUTE_NONE
    if issue_type == "CATEGORY_MISSING":
        return ROUTE_CATEGORY
    if issue_type == "UNRESOLVED_HISTORICAL_IDENTITY":
        return ROUTE_IDENTITY
    if issue_type in {"ORPHAN_PRICE_HISTORY", "ORPHAN_EVENT_HISTORY"}:
        return ROUTE_ARCHIVE
    if issue_type == "PROMOTION_FIELD_CONTAMINATION":
        # No generic promotion-field target is safe to invent.  A future
        # official correction adapter may route a proven field explicitly.
        return ROUTE_NONE
    if field in _LOCALIZATION_FIELDS and proposed is not None:
        return ROUTE_PATCH
    return ROUTE_NONE


def _clean_candidate(issue: Mapping[str, Any]) -> Any:
    value = issue.get("current_value")
    issue_type = str(issue.get("issue_type") or "")
    if issue_type == "INVALID_ORIGINAL_PRICE":
        return None
    if value is None:
        return None
    if issue_type in {"HTML_CONTAMINATION", "UI_TEXT_CONTAMINATION"}:
        field = str(issue.get("field_name") or "")
        cleaned = normalize_official_text(value, field=field)
        if cleaned is None and str(value).strip().casefold() in {"añadir a tus favoritos", "leer más", "descripción"}:
            return ""
        if cleaned is None:
            return None
        return cleaned if cleaned != str(value) else None
    return None


def _queue_category_backlog(db_path: Path, issue: Mapping[str, Any], *, actor: str) -> str | None:
    source_hash = str(issue.get("source_hash") or "").strip()
    sku = str(issue.get("official_sku") or "").strip()
    evidence = issue.get("evidence_json") or issue.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            evidence = {}
    if not sku or not source_hash:
        return None
    queue_id = _digest(f"{issue.get('issue_id')}|{source_hash}")[:32]
    from ...database.category_backlog import enqueue_category_missing
    try:
        enqueue_category_missing(
            db_path, queue_id=queue_id, official_sku=sku,
            cat1_es=str(evidence.get("cat1") or ""), cat2_es=str(evidence.get("cat2") or ""),
            source_hash=source_hash, actor=actor,
        )
    except sqlite3.IntegrityError:
        pass
    return queue_id


def build_repair_candidates(db_path: Path, *, batch_id: str | None = None,
                            created_by: str = "data-quality-audit",
                            base_commit_id: str | None = None,
                            issue_ids: list[str] | None = None) -> dict[str, Any]:
    """Route audited issues and create candidates only for executable actions."""
    path = Path(db_path)
    result = audit_history(path, persist=True)
    repo = DataQualityRepository(path)
    with connect(path) as db:
        current_head = _latest_commit(db)
    base = base_commit_id if base_commit_id is not None else current_head
    issue_set = set(issue_ids or ())
    selected = [item for item in result.issues if not issue_set or item.issue_id in issue_set]
    selected = [item for item in selected if item.status not in {"RESOLVED", "WAIVED", "SUPERSEDED"}]
    batch_key = "|".join([str(base or ""), *sorted(item.issue_id for item in selected)])
    batch = batch_id or f"RB-{_digest(batch_key)[:20]}"
    repo.create_batch(batch_id=batch, base_commit_id=base, created_by=created_by, status="REVIEW")
    counts = {"candidate_count": 0, "routed_category_count": 0, "identity_review_count": 0,
              "archive_review_count": 0, "non_applyable_count": 0}
    for issue in selected:
        old = issue.current_value
        proposed = _clean_candidate(issue.as_dict())
        action = _route(issue.as_dict(), proposed)
        if action == ROUTE_CATEGORY:
            if _queue_category_backlog(path, issue.as_dict(), actor=created_by):
                counts["routed_category_count"] += 1
            else:
                counts["non_applyable_count"] += 1
            continue
        if action == ROUTE_IDENTITY:
            counts["identity_review_count"] += 1
            continue
        if action == ROUTE_ARCHIVE:
            counts["archive_review_count"] += 1
            continue
        if action not in _APPLYABLE or (action != ROUTE_DIRECT and proposed is None):
            counts["non_applyable_count"] += 1
            continue
        candidate_id = _digest(f"{batch}|{issue.issue_id}")
        repo.save_candidate({
            "candidate_id": candidate_id, "repair_batch_id": batch, "issue_id": issue.issue_id,
            "official_sku": issue.official_sku, "field_name": issue.field_name,
            "old_value": None if old is None else str(old),
            "proposed_value": None if proposed is None else str(proposed),
            "evidence": issue.evidence or {}, "source_hash": issue.source_hash,
            "confidence": 0.95 if issue.issue_type == "INVALID_ORIGINAL_PRICE" else 0.7,
            "candidate_status": "REVIEW_REQUIRED", "repair_action": action,
        })
        counts["candidate_count"] += 1
    repo.update_batch(batch, issue_count=len(selected), candidate_count=counts["candidate_count"])
    return {
        "repair_batch_id": batch, "base_commit_id": base, "issue_count": len(selected),
        "status": "REVIEW", **counts,
    }


def write_repair_preview(db_path: Path, batch_id: str, output_path: Path) -> dict[str, Any]:
    repo = DataQualityRepository(Path(db_path))
    candidates = repo.candidates(batch_id)
    with connect(Path(db_path)) as db:
        issue_rows = {str(row[0]): dict(row) for row in db.execute(
            "SELECT issue_id,issue_type,severity,expected_rule,status FROM data_quality_issues"
        ).fetchall()}
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        issue = issue_rows.get(str(candidate["issue_id"]), {})
        rows.append({
            "candidate_id": candidate["candidate_id"], "issue_id": candidate["issue_id"],
            "SKU": candidate.get("official_sku"), "field": candidate.get("field_name"),
            "old_value": candidate.get("old_value"), "proposed_value": candidate.get("proposed_value"),
            "issue_type": issue.get("issue_type"), "severity": issue.get("severity"),
            "repair_action": candidate.get("repair_action"), "evidence": candidate.get("evidence_json") or "{}",
            "source_hash": candidate.get("source_hash"), "recommendation": "REVIEW_REQUIRED",
        })
    target = Path(output_path); target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        fields = list(rows[0].keys()) if rows else ["candidate_id", "issue_id", "SKU", "field", "old_value", "proposed_value", "issue_type", "severity", "repair_action", "evidence", "source_hash", "recommendation"]
        with target.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    else:
        target.write_text(json.dumps({"repair_batch_id": batch_id, "candidates": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"repair_batch_id": batch_id, "output_path": str(target), "candidate_count": len(rows)}


def approve_candidate(db_path: Path, candidate_id: str, *, reviewer: str,
                      approved: bool = True) -> dict[str, Any]:
    try:
        validated = validate_patch_approval_actor(reviewer)
    except ImmutablePatchError as exc:
        raise RepairError(str(exc)) from exc
    repo = DataQualityRepository(Path(db_path))
    status = "APPROVED" if approved else "REJECTED"
    repo.set_candidate_status(candidate_id, status, reviewer=validated)
    with connect(Path(db_path)) as db:
        row = db.execute("SELECT repair_batch_id FROM repair_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if row:
        batch_id = str(row[0])
        with connect(Path(db_path)) as db:
            approved_count = int(db.execute("SELECT COUNT(*) FROM repair_candidates WHERE repair_batch_id=? AND candidate_status='APPROVED'", (batch_id,)).fetchone()[0])
        repo.update_batch(batch_id, approved_count=approved_count)
    return {"candidate_id": candidate_id, "candidate_status": status, "reviewed_by": validated}


def _assert_not_primary(db: sqlite3.Connection) -> None:
    try:
        role = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
    except sqlite3.OperationalError:
        role = None
    if role and str(role[0]).upper() == "PRIMARY":
        raise RepairError("REAL_PRIMARY_WRITE_FORBIDDEN")


def apply_repair_batch(db_path: Path, batch_id: str, *, commit: bool = False,
                       expected_base_commit_id: str | None = None,
                       actor: str = "") -> dict[str, Any]:
    """Apply approved candidates to a non-PRIMARY fixture only.

    The apply step is deliberately not formal correction: it records
    ``FIXTURE_APPLIED`` and leaves issues unresolved until verification.
    ``result_commit_id`` is always NULL because no formal commit is created.
    """
    if not commit:
        return {"repair_batch_id": batch_id, "status": "DRY_RUN"}
    apply_actor = validate_repair_apply_actor(actor)
    path = Path(db_path); repo = DataQualityRepository(path)
    with connect(path) as db:
        _assert_not_primary(db)
        batch = db.execute("SELECT * FROM repair_batches WHERE repair_batch_id=?", (batch_id,)).fetchone()
        if not batch: raise RepairError("REPAIR_BATCH_NOT_FOUND")
        columns = [item[1] for item in db.execute("PRAGMA table_info(repair_batches)").fetchall()]
        batch_data = dict(zip(columns, batch)); head = _latest_commit(db)
        expected = expected_base_commit_id if expected_base_commit_id is not None else batch_data.get("base_commit_id")
        if expected != head: raise RepairError("STALE_BASE_COMMIT")
        candidates = [dict(row) for row in db.execute("SELECT * FROM repair_candidates WHERE repair_batch_id=?", (batch_id,)).fetchall()]
        if not candidates: raise RepairError("REPAIR_CANDIDATES_MISSING")
        if any(str(row.get("candidate_status")) != "APPROVED" for row in candidates): raise RepairError("REPAIR_APPROVAL_REQUIRED")
        # Check every reviewer before opening the mutation section.  A failed
        # separation check must leave the whole batch untouched.
        for candidate in candidates:
            reviewer = str(candidate.get("reviewed_by") or "").strip()
            if not reviewer:
                raise RepairError("REPAIR_REVIEWER_MISSING")
            if reviewer == apply_actor:
                raise RepairError("REPAIR_REVIEWER_APPLIER_NOT_SEPARATE")
        applied = 0; now = now_utc()
        try:
            tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for candidate in candidates:
                sku = candidate.get("official_sku"); field = candidate.get("field_name")
                proposed = candidate.get("proposed_value")
                action = str(candidate.get("repair_action") or ROUTE_NONE)
                if action not in _APPLYABLE: raise RepairError("REPAIR_ACTION_NOT_APPLYABLE")
                issue_row = db.execute("SELECT issue_type FROM data_quality_issues WHERE issue_id=?", (candidate["issue_id"],)).fetchone()
                issue_type = str(issue_row[0]) if issue_row else ""
                expected_hash = str(candidate.get("source_hash") or "")
                if not expected_hash: raise RepairError("SOURCE_HASH_REQUIRED")
                current_hash = None
                if issue_type == "INVALID_ORIGINAL_PRICE" and "products" in tables:
                    columns_p = {str(item[1]) for item in db.execute("PRAGMA table_info(products)").fetchall()}
                    if "source_hash" in columns_p:
                        row = db.execute("SELECT source_hash FROM products WHERE official_sku=?", (sku,)).fetchone(); current_hash = row[0] if row else None
                if current_hash is None and "localization_fields" in tables and sku and field:
                    row = db.execute("SELECT source_hash FROM localization_fields WHERE official_sku=? AND language='es' AND field_name=? ORDER BY updated_at DESC LIMIT 1", (sku, field)).fetchone(); current_hash = row[0] if row else None
                if current_hash is None and "product_localizations" in tables and sku:
                    cols = {str(item[1]) for item in db.execute("PRAGMA table_info(product_localizations)").fetchall()}
                    if "source_hash" in cols:
                        row = db.execute("SELECT source_hash FROM product_localizations WHERE official_sku=? AND language='es' LIMIT 1", (sku,)).fetchone(); current_hash = row[0] if row else None
                if str(current_hash or "") != expected_hash: raise RepairError("SOURCE_HASH_MISMATCH")
                changed = False
                if issue_type == "INVALID_ORIGINAL_PRICE" and "products" in tables and sku:
                    changed = db.execute("UPDATE products SET original_price=?,updated_at=? WHERE official_sku=?", (proposed or None, now, sku)).rowcount > 0
                elif field and "product_localizations" in tables and sku:
                    if field not in _LOCALIZATION_FIELDS: raise RepairError("REPAIR_FIELD_NOT_ALLOWED")
                    changed = db.execute(f"UPDATE product_localizations SET {field}=?,updated_at=? WHERE official_sku=? AND language='es'", (proposed, now, sku)).rowcount > 0
                    if "localization_fields" in tables:
                        db.execute("UPDATE localization_fields SET value=?,updated_at=? WHERE official_sku=? AND language='es' AND field_name=?", (proposed, now, sku, field))
                elif field and "localization_fields" in tables and sku:
                    if field not in _LOCALIZATION_FIELDS: raise RepairError("REPAIR_FIELD_NOT_ALLOWED")
                    changed = db.execute("UPDATE localization_fields SET value=?,updated_at=? WHERE official_sku=? AND language='es' AND field_name=?", (proposed, now, sku, field)).rowcount > 0
                if not changed: raise RepairError("REPAIR_TARGET_NOT_FOUND")
                db.execute("UPDATE repair_candidates SET candidate_status='APPLIED',applied_by=?,applied_at=? WHERE candidate_id=?", (apply_actor, now, candidate["candidate_id"]))
                applied += 1
            db.execute("UPDATE repair_batches SET status='APPLIED',applied_count=?,result_commit_id=NULL,verification_status='PENDING' WHERE repair_batch_id=?", (applied, batch_id))
        except Exception:
            db.rollback(); raise
    return {"repair_batch_id": batch_id, "status": "FIXTURE_APPLIED", "resolution_type": "FIXTURE_APPLIED", "applied_count": applied, "result_commit_id": None}


def prepare_formal_correction(db_path: Path, batch_id: str, *, created_by: str = "service:data-quality") -> dict[str, Any]:
    """Prepare correction evidence without applying any official fact.

    Localization candidates are represented through immutable PATCH_CREATED
    rows. Official-fact candidates are emitted in a correction bundle for the
    existing future correction adapter. No product or localization value is
    updated here.
    """
    path = Path(db_path); repo = DataQualityRepository(path)
    batch = repo.batch(batch_id)
    if not batch: raise RepairError("REPAIR_BATCH_NOT_FOUND")
    candidates = repo.candidates(batch_id)
    patch_ids: list[str] = []; official: list[dict[str, Any]] = []
    for candidate in candidates:
        # Formal correction preparation is downstream of human review.  A
        # REVIEW_REQUIRED candidate may be previewed, but it must never become
        # an immutable patch or official-fact correction bundle entry.
        if str(candidate.get("candidate_status")) != "APPROVED":
            continue
        action = str(candidate.get("repair_action") or ROUTE_NONE)
        if action == ROUTE_PATCH:
            source_hash = str(candidate.get("source_hash") or "")
            if not source_hash: raise RepairError("SOURCE_HASH_REQUIRED")
            patch_id = "dq_" + _digest(f"{batch_id}|{candidate['candidate_id']}")[:28]
            try:
                create_localization_patch(
                    path, patch_id=patch_id, official_sku=str(candidate.get("official_sku") or ""),
                    language="es", field_name=str(candidate.get("field_name") or ""),
                    old_value=candidate.get("old_value"), new_value=str(candidate.get("proposed_value") or ""),
                    source_hash=source_hash, source_allowlist=("HISTORICAL_AUDIT",),
                    created_by=created_by, evidence={"candidate_id": candidate["candidate_id"], "source_name": "HISTORICAL_AUDIT"},
                    reason="DATA_QUALITY_FORMAL_PREPARE",
                )
            except ImmutablePatchError as exc:
                if "PATCH_ALREADY_EXISTS" not in str(exc): raise RepairError(str(exc)) from exc
            patch_ids.append(patch_id)
        elif action in {ROUTE_DIRECT, ROUTE_TEXT}:
            official.append({"candidate_id": candidate["candidate_id"], "official_sku": candidate.get("official_sku"), "field_name": candidate.get("field_name"), "old_value": candidate.get("old_value"), "new_value": candidate.get("proposed_value"), "source_hash": candidate.get("source_hash")})
    bundle_hash = _digest(json.dumps({"batch_id": batch_id, "patch_ids": patch_ids, "official": official}, sort_keys=True, ensure_ascii=False))
    return {"status": "PREPARED", "repair_batch_id": batch_id, "base_commit_id": batch.get("base_commit_id"), "patch_ids": patch_ids, "official_fact_corrections": official, "correction_bundle_id": "CB_" + bundle_hash[:20], "real_primary_apply": False}


def verify_repair_batch(db_path: Path, batch_id: str, *, verifier: str = "human:verification") -> dict[str, Any]:
    path = Path(db_path); repo = DataQualityRepository(path); candidates = repo.candidates(batch_id)
    failures: list[str] = []; now = now_utc()
    with connect(path) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for candidate in candidates:
            if candidate.get("candidate_status") != "APPLIED":
                failures.append(f"NOT_APPLIED:{candidate['candidate_id']}"); continue
            issue_row = db.execute("SELECT issue_type,status FROM data_quality_issues WHERE issue_id=?", (candidate["issue_id"],)).fetchone()
            if not issue_row:
                failures.append(f"ISSUE_NOT_FOUND:{candidate['issue_id']}"); continue
            issue_type, issue_status = str(issue_row[0]), str(issue_row[1])
            sku = str(candidate.get("official_sku") or ""); proposed = candidate.get("proposed_value")
            actual: Any = None
            if issue_type == "INVALID_ORIGINAL_PRICE" and "products" in tables:
                row = db.execute("SELECT original_price FROM products WHERE official_sku=?", (sku,)).fetchone(); actual = row[0] if row else None
                expected = None if proposed in (None, "") else float(proposed)
            else:
                field = str(candidate.get("field_name") or "")
                if "product_localizations" in tables and field in {str(item[1]) for item in db.execute("PRAGMA table_info(product_localizations)").fetchall()}:
                    row = db.execute(f"SELECT {field} FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone(); actual = row[0] if row else None
                if actual is None and "localization_fields" in tables:
                    row = db.execute("SELECT value FROM localization_fields WHERE official_sku=? AND language='es' AND field_name=?", (sku, field)).fetchone(); actual = row[0] if row else None
                expected = proposed
            if (actual is None and expected is not None) or (actual is not None and str(actual) != str(expected)):
                failures.append(f"VALUE_MISMATCH:{candidate['candidate_id']}")
            else:
                db.execute("UPDATE repair_candidates SET verified_by=?,verified_at=? WHERE candidate_id=?", (verifier, now, candidate["candidate_id"]))
        if failures:
            db.execute("UPDATE data_quality_issues SET status=CASE WHEN status='RESOLVED' THEN 'REVIEW_REQUIRED' ELSE status END,resolution_type=NULL,resolved_at=NULL,resolved_by=NULL WHERE issue_id IN (SELECT issue_id FROM repair_candidates WHERE repair_batch_id=?)", (batch_id,))
            db.execute("UPDATE repair_batches SET status='FAILED',verification_status='FAILED' WHERE repair_batch_id=?", (batch_id,))
            status = "FAILED"
        else:
            db.execute("UPDATE data_quality_issues SET status='RESOLVED',resolution_type='FIXTURE_APPLIED',resolved_at=?,resolved_by=? WHERE issue_id IN (SELECT issue_id FROM repair_candidates WHERE repair_batch_id=?)", (now, verifier, batch_id))
            db.execute("UPDATE repair_batches SET status='VERIFIED',verification_status='VERIFIED' WHERE repair_batch_id=?", (batch_id,))
            status = "VERIFIED"
    return {"repair_batch_id": batch_id, "status": status, "candidate_count": len(candidates), "failures": failures}
