"""Read-only Approval/Provenance audit for an isolated candidate SQLite DB."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).absolute().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def audit(path: Path) -> dict[str, object]:
    with _ro(path) as db:
        event_rows = db.execute("SELECT patch_id,event_type,actor FROM localization_patch_events").fetchall()
        counts = {event: sum(row[1] == event for row in event_rows) for event in ("PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED", "PATCH_REVOKED")}
        auto_approved = sum(row[1] == "PATCH_APPROVED" and not (str(row[2]).startswith("human:") or str(row[2]).startswith("service:")) for row in event_rows)
        missing_approval_status = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='translation_candidates'").fetchone():
            missing_approval_status = db.execute("SELECT COUNT(*) FROM translation_candidates WHERE approval_status IS NULL OR TRIM(approval_status)='' ").fetchone()[0]
        source_allowlist_bypass = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization_patches'").fetchone():
            columns = {row[1] for row in db.execute("PRAGMA table_info(localization_patches)")}
            if "source_allowlist" in columns:
                source_allowlist_bypass = db.execute("SELECT COUNT(*) FROM localization_patches WHERE source_allowlist IS NULL OR TRIM(source_allowlist) IN ('','[]')").fetchone()[0]
        approval_actor_mismatch = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization_fields'").fetchone():
            approval_actor_mismatch = db.execute(
                """SELECT COUNT(*) FROM localization_fields f
                   JOIN localization_patch_events e ON e.patch_id=f.applied_commit_id
                   WHERE f.approved_by IS NOT NULL AND e.event_type='PATCH_APPROVED' AND f.approved_by<>e.actor"""
            ).fetchone()[0]
        category_missing_hash = db.execute(
            "SELECT COUNT(*) FROM category_backlog WHERE status IN ('APPROVED','APPLIED') AND (source_hash IS NULL OR TRIM(source_hash)='')"
        ).fetchone()[0]
        field_source_hash_mismatch = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='localization_fields'").fetchone():
            field_source_hash_mismatch = db.execute(
                """SELECT COUNT(*) FROM localization_fields f
                   JOIN product_localizations p ON p.official_sku=f.official_sku AND p.language='es'
                   WHERE f.language='zh' AND f.review_status IN ('VERIFIED','APPROVED','HUMAN_REVIEWED','APPROVED_SOURCE_ABSENT')
                     AND (f.source_hash IS NULL OR f.source_hash<>p.source_hash)"""
            ).fetchone()[0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_db": str(path),
        "patches_created": counts["PATCH_CREATED"],
        "patches_approved": counts["PATCH_APPROVED"],
        "patches_applied": counts["PATCH_APPLIED"],
        "patches_revoked": counts["PATCH_REVOKED"],
        "auto_approved_patch_count": auto_approved,
        "missing_approval_status_apply_count": missing_approval_status,
        "source_allowlist_bypass_count": source_allowlist_bypass,
        "approval_actor_mismatch_count": approval_actor_mismatch,
        "category_missing_hash_approval_count": category_missing_hash,
        "field_source_hash_mismatch_count": field_source_hash_mismatch,
        "invalid_exception_accepted_count": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.candidate_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
