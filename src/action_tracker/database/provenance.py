"""Field-level localization provenance projection."""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping


LOCALIZATION_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def sync_localization_field_provenance(
    db: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    commit_id: str,
    now: str,
) -> None:
    """Upsert the current field projection from one localization row.

    Patch history remains append-only; this table is the current field-level
    projection used by audits and release gates.
    """
    sku = str(row.get("official_sku") or row.get("sku") or "").strip()
    language = str(row.get("language") or "zh").strip()
    if not sku or not language:
        raise ValueError("LOCALIZATION_PROVENANCE_IDENTITY_MISSING")
    tables = {
        str(item[0])
        for item in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('localization_fields','localization_field_provenance')"
        ).fetchall()
    }
    for field_name in LOCALIZATION_FIELDS:
        # A caller may update one field only.  In that case every other
        # field's value and provenance must remain untouched; aggregate status
        # is never allowed to fan out over all six fields.
        existing = None
        if "localization_fields" in tables:
            existing = db.execute(
                "SELECT value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status "
                "FROM localization_fields WHERE official_sku=? AND language=? AND field_name=?",
                (sku, language, field_name),
            ).fetchone()
        if existing is None and "localization_field_provenance" in tables:
            existing = db.execute(
                "SELECT value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status "
                "FROM localization_field_provenance WHERE official_sku=? AND language=? AND field_name=?",
                (sku, language, field_name),
            ).fetchone()
        explicit_value = field_name in row
        field_value = row.get(field_name) if explicit_value else (existing[0] if existing else None)
        field_source = row.get(f"{field_name}_source") if f"{field_name}_source" in row else (existing[1] if existing else row.get("source"))
        field_status = row.get(f"{field_name}_review_status") if f"{field_name}_review_status" in row else (existing[2] if existing else row.get("review_status"))
        field_hash = row.get(f"{field_name}_source_hash") if f"{field_name}_source_hash" in row else (existing[3] if existing else row.get("source_hash"))
        field_updated_at = row.get(f"{field_name}_updated_at") if f"{field_name}_updated_at" in row else (existing[4] if existing else now)
        field_commit = row.get(f"{field_name}_applied_commit_id") if f"{field_name}_applied_commit_id" in row else (existing[5] if existing else row.get("applied_commit_id") or commit_id)
        approved_by = row.get(f"{field_name}_approved_by") if f"{field_name}_approved_by" in row else (existing[6] if existing else row.get("approved_by"))
        approved_at = row.get(f"{field_name}_approved_at") if f"{field_name}_approved_at" in row else (existing[7] if existing else row.get("approved_at"))
        field_freshness = row.get(f"{field_name}_freshness_status") if f"{field_name}_freshness_status" in row else (existing[8] if existing else row.get("freshness_status"))
        values = (
            sku, language, field_name, field_value, field_source, field_status,
            field_hash, field_updated_at or now, field_commit or commit_id,
        )
        if "localization_fields" in tables:
            db.execute(
                """INSERT INTO localization_fields
                (official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku,language,field_name) DO UPDATE SET
                  value=excluded.value,source=excluded.source,review_status=excluded.review_status,
                  source_hash=excluded.source_hash,updated_at=excluded.updated_at,
                  applied_commit_id=excluded.applied_commit_id,approved_by=excluded.approved_by,
                  approved_at=excluded.approved_at,freshness_status=excluded.freshness_status""",
                values + (approved_by, approved_at, field_freshness),
            )
        if "localization_field_provenance" in tables:
            db.execute(
                """INSERT INTO localization_field_provenance
                (official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(official_sku,language,field_name) DO UPDATE SET
                  value=excluded.value,source=excluded.source,review_status=excluded.review_status,
                  source_hash=excluded.source_hash,updated_at=excluded.updated_at,
                  applied_commit_id=excluded.applied_commit_id,approved_by=excluded.approved_by,
                  approved_at=excluded.approved_at,freshness_status=excluded.freshness_status""",
                values + (approved_by, approved_at, field_freshness),
            )
