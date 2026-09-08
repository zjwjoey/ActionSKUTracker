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
    global_status = row.get("review_status")
    global_freshness = row.get("freshness_status")
    for field_name in LOCALIZATION_FIELDS:
        field_status = row.get(f"{field_name}_review_status") or global_status
        field_freshness = row.get(f"{field_name}_freshness_status") or global_freshness
        field_hash = row.get(f"{field_name}_source_hash") or row.get("source_hash")
        db.execute(
            """INSERT INTO localization_field_provenance
            (official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(official_sku,language,field_name) DO UPDATE SET
              value=excluded.value,source=excluded.source,review_status=excluded.review_status,
              source_hash=excluded.source_hash,updated_at=excluded.updated_at,
              applied_commit_id=excluded.applied_commit_id,approved_by=excluded.approved_by,
              approved_at=excluded.approved_at,freshness_status=excluded.freshness_status""",
            (sku, language, field_name, row.get(field_name), row.get(f"{field_name}_source") or row.get("source"),
             field_status, field_hash, now, row.get("applied_commit_id") or commit_id,
             row.get("approved_by"), row.get("approved_at"), field_freshness),
        )
