from __future__ import annotations

"""Auditable historical repair candidates.

Candidate generation is safe and read-only with respect to formal facts.  The
fixture-only apply adapter is intentionally guarded against a PRIMARY role;
production corrections must still go through the existing correction/patch
contract and explicit operator authorization.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
import csv

from ...database.connection import connect
from ..contracts import canonical_json
from ..repository import DataQualityRepository, now_utc
from .audit import audit_history


class RepairError(RuntimeError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _latest_commit(db: sqlite3.Connection) -> str | None:
    try:
        row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row else None


def _clean_candidate(issue: Mapping[str, Any]) -> Any:
    value = issue.get("current_value")
    issue_type = str(issue.get("issue_type") or "")
    if issue_type == "INVALID_ORIGINAL_PRICE":
        return None
    if value is None:
        return None
    text = str(value)
    if issue_type in {"HTML_CONTAMINATION", "UI_TEXT_CONTAMINATION"}:
        # Candidate generation is conservative: strip tags only and remove UI
        # labels when they occupy the whole value.  It never invents content.
        import re
        text = re.sub(r"<[^>]*>", "", text)
        if text.strip().casefold() in {"añadir a tus favoritos", "leer más", "descripción"}:
            return ""
        return text.strip()
    return None


def build_repair_candidates(db_path: Path, *, batch_id: str | None = None,
                            created_by: str = "data-quality-audit",
                            base_commit_id: str | None = None,
                            issue_ids: list[str] | None = None) -> dict[str, Any]:
    """Create idempotent candidates from persisted or newly audited issues."""
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
    count = 0
    for issue in selected:
        old = issue.current_value
        proposed = _clean_candidate(issue.as_dict())
        candidate_id = _digest(f"{batch}|{issue.issue_id}")
        repo.save_candidate({
            "candidate_id": candidate_id, "repair_batch_id": batch, "issue_id": issue.issue_id,
            "official_sku": issue.official_sku, "field_name": issue.field_name,
            "old_value": None if old is None else str(old),
            "proposed_value": None if proposed is None else str(proposed),
            "evidence": issue.evidence or {}, "source_hash": issue.source_hash,
            "confidence": 0.95 if issue.issue_type == "INVALID_ORIGINAL_PRICE" else 0.7,
            "candidate_status": "REVIEW_REQUIRED",
        })
        count += 1
    repo.update_batch(batch, issue_count=len(selected), candidate_count=count)
    return {"repair_batch_id": batch, "base_commit_id": base, "issue_count": len(selected), "candidate_count": count, "status": "REVIEW"}


def write_repair_preview(db_path: Path, batch_id: str, output_path: Path) -> dict[str, Any]:
    """Write a deterministic human-review CSV or JSON preview.

    This is a projection of repair metadata only; it never applies a
    candidate and never becomes a formal fact source.
    """
    repo = DataQualityRepository(Path(db_path))
    candidates = repo.candidates(batch_id)
    with connect(Path(db_path)) as db:
        issue_rows = {
            str(row[0]): dict(row)
            for row in db.execute("SELECT issue_id,issue_type,severity,expected_rule,status FROM data_quality_issues").fetchall()
        }
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        issue = issue_rows.get(str(candidate["issue_id"]), {})
        rows.append({
            "candidate_id": candidate["candidate_id"], "issue_id": candidate["issue_id"],
            "SKU": candidate.get("official_sku"), "field": candidate.get("field_name"),
            "old_value": candidate.get("old_value"), "proposed_value": candidate.get("proposed_value"),
            "issue_type": issue.get("issue_type"), "severity": issue.get("severity"),
            "evidence": candidate.get("evidence_json") or "{}", "source_hash": candidate.get("source_hash"),
            "recommendation": "REVIEW_REQUIRED",
        })
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        fields = list(rows[0].keys()) if rows else ["candidate_id", "issue_id", "SKU", "field", "old_value", "proposed_value", "issue_type", "severity", "evidence", "source_hash", "recommendation"]
        with target.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
    else:
        target.write_text(json.dumps({"repair_batch_id": batch_id, "candidates": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"repair_batch_id": batch_id, "output_path": str(target), "candidate_count": len(rows)}


def approve_candidate(db_path: Path, candidate_id: str, *, reviewer: str,
                       approved: bool = True) -> dict[str, Any]:
    if not reviewer or not reviewer.strip():
        raise RepairError("REVIEWER_REQUIRED")
    repo = DataQualityRepository(Path(db_path))
    status = "APPROVED" if approved else "REJECTED"
    repo.set_candidate_status(candidate_id, status, reviewer=reviewer)
    with connect(Path(db_path)) as db:
        row = db.execute("SELECT repair_batch_id FROM repair_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    if row:
        batch_id = str(row[0])
        with connect(Path(db_path)) as db:
            approved_count = int(db.execute("SELECT COUNT(*) FROM repair_candidates WHERE repair_batch_id=? AND candidate_status='APPROVED'", (batch_id,)).fetchone()[0])
        repo.update_batch(batch_id, approved_count=approved_count)
    return {"candidate_id": candidate_id, "candidate_status": status, "reviewed_by": reviewer}


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
    """Apply only approved candidates to a non-PRIMARY fixture.

    The default is dry-run.  A real PRIMARY is rejected even with ``commit``;
    production correction must use the existing formal correction contract.
    """
    if not commit:
        return {"repair_batch_id": batch_id, "status": "DRY_RUN"}
    if not actor.strip():
        raise RepairError("APPLY_ACTOR_REQUIRED")
    path = Path(db_path)
    repo = DataQualityRepository(path)
    with connect(path) as db:
        _assert_not_primary(db)
        batch = db.execute("SELECT * FROM repair_batches WHERE repair_batch_id=?", (batch_id,)).fetchone()
        if not batch:
            raise RepairError("REPAIR_BATCH_NOT_FOUND")
        columns = [item[1] for item in db.execute("PRAGMA table_info(repair_batches)").fetchall()]
        batch_data = dict(zip(columns, batch))
        head = _latest_commit(db)
        expected = expected_base_commit_id if expected_base_commit_id is not None else batch_data.get("base_commit_id")
        if expected != head:
            raise RepairError("STALE_BASE_COMMIT")
        candidates = [dict(row) for row in db.execute("SELECT * FROM repair_candidates WHERE repair_batch_id=?", (batch_id,)).fetchall()]
        if not candidates:
            raise RepairError("REPAIR_CANDIDATES_MISSING")
        if any(str(row.get("candidate_status")) != "APPROVED" for row in candidates):
            raise RepairError("REPAIR_APPROVAL_REQUIRED")
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = 0
        try:
            for candidate in candidates:
                sku = candidate.get("official_sku")
                field = candidate.get("field_name")
                proposed = candidate.get("proposed_value")
                expected_hash = str(candidate.get("source_hash") or "")
                if expected_hash:
                    current_hash = None
                    if "localization_fields" in tables and sku and field:
                        row = db.execute("SELECT source_hash FROM localization_fields WHERE official_sku=? AND language='es' AND field_name=? ORDER BY updated_at DESC LIMIT 1", (sku, field)).fetchone()
                        current_hash = row[0] if row else None
                    if current_hash is None and "product_localizations" in tables and sku:
                        cols = {str(item[1]) for item in db.execute("PRAGMA table_info(product_localizations)").fetchall()}
                        if "source_hash" in cols:
                            row = db.execute("SELECT source_hash FROM product_localizations WHERE official_sku=? AND language='es' LIMIT 1", (sku,)).fetchone()
                            current_hash = row[0] if row else None
                    if str(current_hash or "") != expected_hash:
                        raise RepairError("SOURCE_HASH_MISMATCH")
                issue_type = db.execute("SELECT issue_type FROM data_quality_issues WHERE issue_id=?", (candidate["issue_id"],)).fetchone()
                issue_type = str(issue_type[0]) if issue_type else ""
                changed = False
                if issue_type == "INVALID_ORIGINAL_PRICE" and "products" in tables and sku:
                    changed = db.execute("UPDATE products SET original_price=?,updated_at=? WHERE official_sku=?", (proposed or None, now_utc(), sku)).rowcount > 0
                elif field and "product_localizations" in tables and sku:
                    allowed = {"name", "cat1", "cat2", "spec", "description", "details"}
                    if field not in allowed:
                        raise RepairError("REPAIR_FIELD_NOT_ALLOWED")
                    changed = db.execute(f"UPDATE product_localizations SET {field}=?,updated_at=? WHERE official_sku=? AND language='es'", (proposed, now_utc(), sku)).rowcount > 0
                    if "localization_fields" in tables:
                        db.execute("UPDATE localization_fields SET value=?,updated_at=? WHERE official_sku=? AND language='es' AND field_name=?", (proposed, now_utc(), sku, field))
                elif field and "localization_fields" in tables and sku:
                    allowed = {"name", "cat1", "cat2", "spec", "description", "details"}
                    if field not in allowed:
                        raise RepairError("REPAIR_FIELD_NOT_ALLOWED")
                    changed = db.execute("UPDATE localization_fields SET value=?,updated_at=? WHERE official_sku=? AND language='es' AND field_name=?", (proposed, now_utc(), sku, field)).rowcount > 0
                if not changed:
                    raise RepairError("REPAIR_TARGET_NOT_FOUND")
                db.execute("UPDATE repair_candidates SET candidate_status='APPLIED',reviewed_by=?,reviewed_at=? WHERE candidate_id=?", (actor, now_utc(), candidate["candidate_id"]))
                db.execute("UPDATE data_quality_issues SET status='RESOLVED',resolution_type='FORMAL_CORRECTION',resolved_at=?,resolved_by=? WHERE issue_id=?", (now_utc(), actor, candidate["issue_id"]))
                applied += 1
            db.execute("UPDATE repair_batches SET status='APPLIED',applied_count=?,result_commit_id=?,verification_status='PENDING' WHERE repair_batch_id=?", (applied, f"REPAIR_{_digest(batch_id + actor)[:16]}", batch_id))
        except Exception:
            db.rollback()
            raise
    return {"repair_batch_id": batch_id, "status": "APPLIED", "applied_count": applied}


def verify_repair_batch(db_path: Path, batch_id: str) -> dict[str, Any]:
    path = Path(db_path)
    repo = DataQualityRepository(path)
    candidates = repo.candidates(batch_id)
    failures: list[str] = []
    with connect(path) as db:
        tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for candidate in candidates:
            if candidate.get("candidate_status") != "APPLIED":
                failures.append(f"NOT_APPLIED:{candidate['candidate_id']}")
                continue
            issue = db.execute("SELECT status FROM data_quality_issues WHERE issue_id=?", (candidate["issue_id"],)).fetchone()
            if not issue or str(issue[0]) != "RESOLVED":
                failures.append(f"ISSUE_NOT_RESOLVED:{candidate['issue_id']}")
                continue
            issue_type_row = db.execute("SELECT issue_type FROM data_quality_issues WHERE issue_id=?", (candidate["issue_id"],)).fetchone()
            issue_type = str(issue_type_row[0]) if issue_type_row else ""
            sku = str(candidate.get("official_sku") or "")
            proposed = candidate.get("proposed_value")
            if issue_type == "INVALID_ORIGINAL_PRICE" and "products" in tables:
                row = db.execute("SELECT original_price FROM products WHERE official_sku=?", (sku,)).fetchone()
                actual = row[0] if row else None
                if actual != (None if proposed in (None, "") else float(proposed)):
                    failures.append(f"VALUE_MISMATCH:{candidate['candidate_id']}")
            elif candidate.get("field_name"):
                field = str(candidate["field_name"])
                actual = None
                if "product_localizations" in tables:
                    columns = {str(item[1]) for item in db.execute("PRAGMA table_info(product_localizations)").fetchall()}
                    if field in columns:
                        row = db.execute(f"SELECT {field} FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
                        actual = row[0] if row else None
                if actual is None and "localization_fields" in tables:
                    row = db.execute("SELECT value FROM localization_fields WHERE official_sku=? AND language='es' AND field_name=?", (sku, field)).fetchone()
                    actual = row[0] if row else None
                if str(actual or "") != str(proposed or ""):
                    failures.append(f"VALUE_MISMATCH:{candidate['candidate_id']}")
    status = "VERIFIED" if not failures and candidates else "FAILED"
    repo.update_batch(batch_id, verification_status=status, status=status)
    return {"repair_batch_id": batch_id, "status": status, "candidate_count": len(candidates), "failures": failures}
