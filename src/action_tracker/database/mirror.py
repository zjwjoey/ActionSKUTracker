"""Build and validate a SQLite Mirror without touching production Excel."""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .connection import connect
from .migration import SHEET_CONFIG, migrate_master, sha256_file
from .schema import SCHEMA_FAMILY, SCHEMA_VERSION
from .validation import validate_mirror


def _migration_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def _post_promotion_validate(path: Path, migration_id: str) -> dict[str, Any]:
    db = connect(path, read_only=True)
    try:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk = db.execute("PRAGMA foreign_key_check").fetchall()
        metadata = dict(db.execute("SELECT key,value FROM schema_metadata").fetchall())
        checks = {
            "integrity_check": integrity == "ok",
            "foreign_key_check": not fk,
            "schema_family": metadata.get("schema_family") == SCHEMA_FAMILY,
            "schema_version": metadata.get("schema_version") == SCHEMA_VERSION,
            "migration_id": metadata.get("migration_id") == migration_id,
        }
        return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "integrity": integrity, "foreign_key_rows": [dict(row) for row in fk]}
    finally:
        db.close()


def _write_reports(report_dir: Path, migration_report: dict[str, Any], validation: dict[str, Any]) -> None:
    """Persist the final state of both reports after every terminal branch."""
    (report_dir / "migration_report.json").write_text(
        json.dumps(migration_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (report_dir / "validation_report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def build_mirror(master_path: Path, output_path: Path, reports_root: Path | None = None) -> dict[str, Any]:
    """Build staging DB, validate it, and atomically replace the Mirror on PASS."""
    migration_id = _migration_id()
    reports_root = reports_root or output_path.parent / "reports"
    report_dir = reports_root / migration_id
    report_dir.mkdir(parents=True, exist_ok=True)
    staging_path = output_path.parent / "staging" / migration_id / "action_tracker.staging.db"
    source_hash_before = sha256_file(master_path)
    migrated = migrate_master(master_path, staging_path, migration_id)
    validation = validate_mirror(master_path, staging_path)
    source_hash_after = sha256_file(master_path)
    validation["master_hash_before"] = source_hash_before
    validation["master_hash_after"] = source_hash_after
    validation["post_validation_master_hash"] = source_hash_after
    validation["master_unchanged"] = source_hash_before == source_hash_after
    validation["staging_path"] = str(staging_path)
    validation["status"] = "PASS" if validation["status"] == "PASS" and validation["master_unchanged"] else "FAIL"
    source_issue_count = sum(validation.get("source_issue_counts", {}).values())
    validation["verdict"] = (
        "SQLITE MIRROR VALIDATED WITH SOURCE DATA ISSUES" if validation["status"] == "PASS" and source_issue_count
        else "SQLITE MIRROR VALIDATED" if validation["status"] == "PASS"
        else "SQLITE MIRROR REQUIRES FIXES"
    )
    # Record the migration outcome inside the staging DB before it is promoted.
    db = connect(staging_path)
    try:
        db.execute(
            "UPDATE migration_runs SET finished_at=CURRENT_TIMESTAMP,status=?,validation_status=?,report_path=? WHERE migration_id=?",
            ("VALIDATED" if validation["status"] == "PASS" else "FAILED", validation["verdict"], str(report_dir), migration_id),
        )
        db.commit()
    finally:
        db.close()
    migration_report = {
        "migration_id": migration_id,
        "source_master": str(master_path),
        "source_sha256": source_hash_before,
        "staging_db": str(staging_path),
        "output_db": str(output_path),
        "counts": migrated["counts"],
        "validation_status": validation["status"],
        "verdict": validation["verdict"],
        "source_issue_counts": validation.get("source_issue_counts", {}),
        "post_validation_master_hash": validation["post_validation_master_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_reports(report_dir, migration_report, validation)
    sheet_mapping = {
        "08_LONG_TERM_MASTER": {"policy": "MIGRATE_SPLIT_BY_STATUS", "destination": "products + migration_source_issues", "source_rows": validation["source"]["08_LONG_TERM_MASTER"]["rows"], "migrated_rows": migrated["counts"]["products"], "unmatched_rows": validation["source"]["08_LONG_TERM_MASTER"].get("unmatched_count", 0)},
        "01_SKU_ZH_CURRENT": {"policy": "MIGRATE + PARITY_ONLY", "destination": "product_localizations + CURRENT parity", "source_rows": validation["source"]["01_SKU_ZH_CURRENT"]["rows"], "migrated_rows": validation["source"]["01_SKU_ZH_CURRENT"]["rows"]},
        "02_SKU_ES_CURRENT": {"policy": "MIGRATE + PARITY_ONLY", "destination": "products + CURRENT parity", "source_rows": validation["source"]["02_SKU_ES_CURRENT"]["rows"], "migrated_rows": validation["source"]["02_SKU_ES_CURRENT"]["rows"]},
        "03_PRICE_HISTORY": {"policy": "MIGRATE_LOSSLESS", "destination": "price_history + migration_source_issues", "source_rows": validation["source"]["03_PRICE_HISTORY"]["rows"], "migrated_rows": migrated["counts"]["price_history"], "unmatched_rows": validation["source"]["03_PRICE_HISTORY"]["rows"] - migrated["counts"]["price_history"]},
        "04_EVENT_HISTORY": {"policy": "MIGRATE_LOSSLESS", "destination": "events + migration_source_issues", "source_rows": validation["source"]["04_EVENT_HISTORY"]["rows"], "migrated_rows": migrated["counts"]["events"], "unmatched_rows": validation["source"]["04_EVENT_HISTORY"]["rows"] - migrated["counts"]["events"]},
        "05_RUN_LOG": {"policy": "MIGRATE_LOSSLESS", "destination": "runs", "source_rows": validation["source"]["05_RUN_LOG"]["rows"], "migrated_rows": migrated["counts"]["runs"]},
        "06_REVIEW_QUEUE": {"policy": "MIGRATE_PRESERVE_DUPLICATES", "destination": "reviews + migration_source_issues", "source_rows": validation["source"]["06_REVIEW_QUEUE"]["rows"], "migrated_rows": migrated["counts"]["reviews"]},
        "07_APRIL_ARCHIVE": {"policy": "AUDIT_ONLY", "destination": "migration_source_issues", "source_rows": validation["source"]["07_APRIL_ARCHIVE"]["rows"], "migrated_rows": 0},
        "09_APRIL_MATCH_AUDIT": {"policy": "AUDIT_ONLY", "destination": "migration_source_issues", "source_rows": validation["source"]["09_APRIL_MATCH_AUDIT"]["rows"], "migrated_rows": 0},
        "10_SOURCE_SCHEMA": {"policy": "MIGRATE_METADATA", "destination": "schema_metadata + migration_source_issues", "source_rows": validation["source"]["10_SOURCE_SCHEMA"]["rows"], "migrated_rows": 0},
    }
    (report_dir / "mapping_summary.json").write_text(json.dumps({"migration_id": migration_id, "counts": migrated["counts"], "source_issue_counts": validation.get("source_issue_counts", {}), "sheet_mapping": sheet_mapping}, ensure_ascii=False, indent=2), encoding="utf-8")
    if validation["status"] != "PASS":
        return {"migration_id": migration_id, "status": "FAIL", "report_dir": str(report_dir), "staging_db": str(staging_path), "validation": validation}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if output_path.exists():
        backup_dir = output_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{output_path.stem}_{migration_id}.db"
        shutil.copy2(output_path, backup_path)
    final_master_hash = sha256_file(master_path)
    validation["final_master_hash"] = final_master_hash
    validation["checks"]["final_master_hash"] = final_master_hash == source_hash_before
    if final_master_hash != source_hash_before:
        validation["status"] = "FAIL"
        validation["verdict"] = "SQLITE MIRROR REQUIRES FIXES"
        validation["failure_reason"] = "MASTER_CHANGED_BEFORE_MIRROR_PROMOTION"
        failed_db = connect(staging_path)
        try:
            failed_db.execute("UPDATE migration_runs SET finished_at=CURRENT_TIMESTAMP,status='FAILED',validation_status=? WHERE migration_id=?", (validation["verdict"], migration_id))
            failed_db.commit()
        finally:
            failed_db.close()
        migration_report.update({"validation_status": "FAIL", "verdict": validation["verdict"], "final_master_hash": final_master_hash, "failure_reason": validation["failure_reason"], "backup_path": str(backup_path) if backup_path else None})
        migration_report.update({
            "validation_status": "FAIL", "verdict": validation["verdict"],
            "final_master_hash": final_master_hash, "master_hash_before": source_hash_before,
            "master_hash_after": source_hash_after, "failure_reason": validation["failure_reason"],
            "backup_path": str(backup_path) if backup_path else None,
        })
        _write_reports(report_dir, migration_report, validation)
        return {"migration_id": migration_id, "status": "FAIL", "report_dir": str(report_dir), "staging_db": str(staging_path), "backup_path": str(backup_path) if backup_path else None, "validation": validation}
    # Path.replace is the atomic promotion operation on the same volume.  Do
    # not unlink the old target first: if promotion fails, the old Mirror must
    # remain available and the backup above provides an additional recovery copy.
    try:
        staging_path.replace(output_path)
    except OSError as exc:
        validation["status"] = "FAIL"
        validation["verdict"] = "SQLITE MIRROR REQUIRES FIXES"
        validation["replacement_error"] = str(exc)
        validation["rollback_preserved_old_mirror"] = output_path.exists()
        failed_db = connect(staging_path)
        try:
            failed_db.execute(
                "UPDATE migration_runs SET finished_at=CURRENT_TIMESTAMP,status='FAILED',validation_status=? WHERE migration_id=?",
                (validation["verdict"], migration_id),
            )
            failed_db.commit()
        finally:
            failed_db.close()
        migration_report.update({
            "validation_status": "FAIL", "verdict": validation["verdict"],
            "final_master_hash": final_master_hash, "master_hash_before": source_hash_before,
            "master_hash_after": source_hash_after, "replacement_error": str(exc),
            "backup_path": str(backup_path) if backup_path else None,
        })
        _write_reports(report_dir, migration_report, validation)
        return {"migration_id": migration_id, "status": "FAIL", "report_dir": str(report_dir), "staging_db": str(staging_path), "backup_path": str(backup_path) if backup_path else None, "validation": validation}
    post = _post_promotion_validate(output_path, migration_id)
    validation["post_promotion_validation"] = post
    if post["status"] != "PASS":
        restored = False
        if backup_path and Path(backup_path).exists():
            shutil.copy2(backup_path, output_path)
            restored = True
        elif output_path.exists():
            output_path.unlink()
        validation["status"] = "FAIL"
        validation["verdict"] = "SQLITE MIRROR REQUIRES FIXES"
        validation["failure_reason"] = "POST_PROMOTION_VALIDATION_FAILED"
        validation["rollback_restored_old_mirror"] = restored
        migration_report.update({
            "validation_status": "FAIL", "verdict": validation["verdict"],
            "final_master_hash": final_master_hash, "master_hash_before": source_hash_before,
            "master_hash_after": source_hash_after, "failure_reason": validation["failure_reason"],
            "rollback_restored_old_mirror": restored, "backup_path": str(backup_path) if backup_path else None,
            "post_promotion_validation": post,
        })
        _write_reports(report_dir, migration_report, validation)
        return {"migration_id": migration_id, "status": "FAIL", "report_dir": str(report_dir), "output_db": str(output_path), "backup_path": str(backup_path) if backup_path else None, "validation": validation}
    result = {"migration_id": migration_id, "status": "PASS", "report_dir": str(report_dir), "output_db": str(output_path), "backup_path": str(backup_path) if backup_path else None, "validation": validation}
    migration_report.update({
        "output_db": str(output_path), "backup_path": result["backup_path"],
        "validation_status": validation["status"], "verdict": validation["verdict"],
        "master_hash_before": source_hash_before, "master_hash_after": source_hash_after,
        "final_master_hash": final_master_hash, "master_unchanged": validation["master_unchanged"],
        "post_promotion_validation": post,
    })
    _write_reports(report_dir, migration_report, validation)
    return result
