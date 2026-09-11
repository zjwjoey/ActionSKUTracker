"""Field-level localization projections shared by every production writer.

The compatibility ``product_localizations`` row is intentionally kept for
older readers, but it is not sufficient for auditability: a detail update may
change only ``description`` and ``details``.  These helpers update the
field-level projections without fanning a row-level status over unrelated
fields.  They work with both the original ``localization_fields`` table and
the newer canonical ``localization_field_provenance`` table so old databases
can be repaired in place.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping


LOCALIZATION_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def sync_localization_field_provenance(
    db: sqlite3.Connection,
    row: Mapping[str, Any],
    *,
    commit_id: str,
    now: str,
) -> None:
    """Upsert current field values and provenance for one localization row.

    A caller may provide a partial row.  Fields omitted from ``row`` retain
    their existing value and provenance; explicit ``None`` still means the
    field is being cleared.  This distinction prevents a detail-only import
    from accidentally blanking name/category/spec values.
    """
    sku = str(row.get("official_sku") or row.get("sku") or "").strip()
    language = str(row.get("language") or "zh").strip()
    if not sku or language not in {"es", "zh"}:
        raise ValueError("LOCALIZATION_PROVENANCE_IDENTITY_MISSING")

    tables = [table for table in ("localization_fields", "localization_field_provenance")
              if _table_exists(db, table)]
    if not tables:
        return

    for field_name in LOCALIZATION_FIELDS:
        existing = None
        for table in tables:
            cols = _columns(db, table)
            extra = [name for name in ("approved_by", "approved_at", "freshness_status") if name in cols]
            selected = "value,source,review_status,source_hash,updated_at,applied_commit_id" + (
                "," + ",".join(extra) if extra else ""
            )
            existing = db.execute(
                f"SELECT {selected} FROM {table} WHERE official_sku=? AND language=? AND field_name=?",
                (sku, language, field_name),
            ).fetchone()
            if existing is not None:
                break

        explicit_value = field_name in row
        old_value = existing[0] if existing else None
        value = row.get(field_name) if explicit_value else old_value
        source = row.get(f"{field_name}_source") if f"{field_name}_source" in row else (existing[1] if existing else row.get("source"))
        status = row.get(f"{field_name}_review_status") if f"{field_name}_review_status" in row else (existing[2] if existing else row.get("review_status"))
        field_hash = row.get(f"{field_name}_source_hash") if f"{field_name}_source_hash" in row else (existing[3] if existing else row.get("source_hash"))
        updated_at = row.get(f"{field_name}_updated_at") if f"{field_name}_updated_at" in row else (existing[4] if existing else now)
        applied_commit = row.get(f"{field_name}_applied_commit_id") if f"{field_name}_applied_commit_id" in row else (existing[5] if existing else row.get("applied_commit_id") or commit_id)
        extras = {}
        if existing is not None:
            names = ["approved_by", "approved_at", "freshness_status"]
            for idx, name in enumerate(names, start=6):
                if idx < len(existing):
                    extras[name] = existing[idx]
        extras.update({name: row.get(f"{field_name}_{name}") for name in ("approved_by", "approved_at", "freshness_status")
                       if f"{field_name}_{name}" in row})

        for table in tables:
            cols = _columns(db, table)
            base = [sku, language, field_name, value, source, status, field_hash,
                    updated_at or now, applied_commit or commit_id]
            if {"approved_by", "approved_at", "freshness_status"} <= cols:
                db.execute(
                    f"""INSERT INTO {table}
                    (official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id,approved_by,approved_at,freshness_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(official_sku,language,field_name) DO UPDATE SET
                      value=excluded.value,source=excluded.source,review_status=excluded.review_status,
                      source_hash=excluded.source_hash,updated_at=excluded.updated_at,
                      applied_commit_id=excluded.applied_commit_id,approved_by=excluded.approved_by,
                      approved_at=excluded.approved_at,freshness_status=excluded.freshness_status""",
                    tuple(base) + (extras.get("approved_by"), extras.get("approved_at"), extras.get("freshness_status")),
                )
            else:
                db.execute(
                    f"""INSERT INTO {table}
                    (official_sku,language,field_name,value,source,review_status,source_hash,updated_at,applied_commit_id)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(official_sku,language,field_name) DO UPDATE SET
                      value=excluded.value,source=excluded.source,review_status=excluded.review_status,
                      source_hash=excluded.source_hash,updated_at=excluded.updated_at,
                      applied_commit_id=excluded.applied_commit_id""",
                    tuple(base),
                )
