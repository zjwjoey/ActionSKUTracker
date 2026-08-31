"""Read-only SQLite V2 repository used by the PRIMARY read path.

The repository deliberately exposes the same shapes as the frozen Excel/CSV
readers so the monitor does not need a second set of lifecycle decisions.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .connection import connect
from .schema import migrate


class ProductionRepositoryError(RuntimeError):
    pass


def import_baseline(path: Path, records: dict[str, dict[str, Any]], observed_at: str) -> int:
    """Backward-compatible V1 baseline helper used by early migration tooling.

    It is intentionally idempotent and only seeds the legacy ``products``
    table.  New production migrations should use
    :func:`import_legacy_baseline_v2` instead.
    """
    migrate(Path(path))
    now = datetime.now().isoformat(timespec="seconds")
    with connect(Path(path)) as db:
        for sku, record in records.items():
            sku = str(sku).strip()
            if not sku:
                continue
            cid = str(record.get("canonical_id") or f"ACT{sku.zfill(7)}")
            db.execute(
                """INSERT INTO products(canonical_id,official_sku,name_es,name_zh,current_price,original_price,unit_price_raw,raw_badges,status,consecutive_missing,product_url,image_url,first_seen_at,last_seen_at,last_checked_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?, 'ACTIVE',0,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_id) DO UPDATE SET name_es=excluded.name_es,name_zh=excluded.name_zh,current_price=excluded.current_price,original_price=excluded.original_price,unit_price_raw=excluded.unit_price_raw,raw_badges=excluded.raw_badges,status='ACTIVE',consecutive_missing=0,product_url=excluded.product_url,image_url=excluded.image_url,last_seen_at=excluded.last_seen_at,last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at""",
                (cid, sku, record.get("name_es"), record.get("name_zh"), record.get("current_price"),
                 record.get("original_price"), record.get("unit_price") or record.get("unit_price_raw"),
                 record.get("raw_tags") or record.get("raw_badges"), record.get("product_url"), record.get("image_url"),
                 record.get("first_seen") or observed_at, observed_at, observed_at, now, now),
            )
            db.execute(
                """INSERT OR IGNORE INTO product_observations(run_id,observation_date,canonical_id,official_sku,sitemap_seen,listing_seen,current_price,original_price,raw_json)
                VALUES(?,?,?,?,1,1,?,?,?)""",
                (f"BASELINE_{observed_at}", observed_at, cid, sku, record.get("current_price"),
                 record.get("original_price"), json.dumps(record, ensure_ascii=False, default=str)),
            )
        db.execute(
            "INSERT OR REPLACE INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?, 'BASELINE_IMPORTED','PASS',0,?,?, '1.0.0')",
            (f"BASELINE_{observed_at}", observed_at, now, now),
        )
    return sum(1 for sku in records if str(sku).strip())


class ProductionRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise ProductionRepositoryError("DB_MISSING")

    def _check_v2(self, db: sqlite3.Connection) -> None:
        try:
            values = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        except sqlite3.OperationalError as exc:
            raise ProductionRepositoryError("DB_V2_SCHEMA_MISSING") from exc
        if values.get("schema_family") != "ACTION_SQLITE_DATA" or values.get("schema_version") != "2.0.0":
            raise ProductionRepositoryError("DB_SCHEMA_IDENTITY_MISMATCH")

    def load_current_products(self) -> dict[str, dict[str, Any]]:
        """Return the complete CURRENT baseline used by the daily monitor.

        The PRIMARY monitor needs the same fact projection as exports.  Reading
        ``products`` alone loses the ES localization fields, which in turn
        makes complete products look like ``MISSING_FIELD`` candidates.
        """
        return {
            str(record["sku"]): record
            for record in self.load_current_export_records()
            if str(record.get("sku") or "").strip()
        }

    def load_current_product_rows(self) -> dict[str, dict[str, Any]]:
        """Legacy products-table-only projection, retained for diagnostics."""
        with connect(self.path) as db:
            self._check_v2(db)
            rows = db.execute(
                "SELECT * FROM products WHERE status='CURRENT' ORDER BY official_sku"
            ).fetchall()
            columns = [column[1] for column in db.execute("PRAGMA table_info(products)").fetchall()]
        return {_sku(row, columns): _product_row(row, columns) for row in rows if _sku(row, columns)}

    def load_known_skus(self) -> dict[str, dict[str, Any]]:
        with connect(self.path) as db:
            self._check_v2(db)
            rows = db.execute("SELECT * FROM lifecycle_state ORDER BY official_sku").fetchall()
            columns = [column[1] for column in db.execute("PRAGMA table_info(lifecycle_state)").fetchall()]
        return {_sku(row, columns): _lifecycle_row(row, columns) for row in rows if _sku(row, columns)}

    def load_offline_skus(self) -> dict[str, dict[str, Any]]:
        known = self.load_known_skus()
        return {sku: {
            "canonical_id": record.get("canonical_id"), "official_sku": sku,
            "offline_date": record.get("offline_date"), "last_seen_date": record.get("last_seen_date"),
            "last_status": "OFFLINE",
        } for sku, record in known.items() if record.get("last_status") == "OFFLINE"}

    def current_head(self) -> str | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()
        return str(row[0]) if row else None

    def latest_commit_info(self) -> dict[str, Any] | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute(
                "SELECT c.commit_id,c.run_id,r.run_date,c.committed_at,c.status FROM commit_batches c "
                "LEFT JOIN runs r ON r.run_id=c.run_id "
                "WHERE c.status='COMMITTED' ORDER BY c.committed_at DESC LIMIT 1"
            ).fetchone()
        return {"commit_id": row[0], "run_id": row[1], "run_date": row[2], "committed_at": row[3], "status": row[4]} if row else None

    def commit_info(self, run_id: str) -> dict[str, Any] | None:
        with connect(self.path) as db:
            self._check_v2(db)
            row = db.execute(
                "SELECT c.commit_id,c.run_id,r.run_date,c.committed_at,c.status FROM commit_batches c "
                "LEFT JOIN runs r ON r.run_id=c.run_id WHERE c.run_id=?", (run_id,)
            ).fetchone()
        return {"commit_id": row[0], "run_id": row[1], "run_date": row[2], "committed_at": row[3], "status": row[4]} if row else None

    def load_current_export_records(self) -> list[dict[str, Any]]:
        """Return CURRENT facts plus both language localization projections."""
        with connect(self.path) as db:
            self._check_v2(db)
            rows = db.execute(
                """SELECT p.canonical_id,p.official_sku,p.name_es,p.name_zh,p.current_price,p.original_price,
                   p.unit_price_raw,p.raw_badges,p.action_new_badge,p.promotion_active,p.sustainable_badge,
                   p.status,p.product_url,p.image_url,p.first_seen_at,p.last_seen_at,
                   es.name,es.cat1,es.cat2,es.spec,es.description,es.details,
                   zh.name,zh.cat1,zh.cat2,zh.spec,zh.description,zh.details,
                   zh.source_hash,zh.resolution_status,zh.review_status,zh.freshness_status,
                   zh.name_source,zh.cat1_source,zh.cat2_source,zh.spec_source,
                   zh.description_source,zh.details_source,zh.approved_by,zh.approved_at,
                   zh.last_commit_id,zh.applied_commit_id
                   FROM products p
                   LEFT JOIN product_localizations es ON es.official_sku=p.official_sku AND es.language='es'
                   LEFT JOIN product_localizations zh ON zh.official_sku=p.official_sku AND zh.language='zh'
                   WHERE p.status='CURRENT' ORDER BY p.official_sku"""
            ).fetchall()
        records = []
        for row in rows:
            records.append({
                "canonical_id": row[0], "sku": row[1], "name_es": row[16] or row[2], "name_zh": row[22] or row[3],
                "current_price": row[4], "original_price": row[5], "unit_price": row[6], "raw_tags": row[7],
                "is_new_badge": bool(row[8]), "promotion": bool(row[9]), "sustainable": bool(row[10]),
                "status": row[11], "product_url": row[12], "image_url": row[13], "first_seen": row[14], "last_seen": row[15],
                "cat1_es": row[17], "cat2_es": row[18], "spec_es": row[19], "desc_es": row[20], "details_es": row[21],
                "cat1_zh": row[23], "cat2_zh": row[24], "spec_zh": row[25], "desc_zh": row[26], "details_zh": row[27],
                "zh_source_hash": row[28], "zh_resolution_status": row[29], "zh_review_status": row[30], "zh_freshness_status": row[31],
                "zh_name_source": row[32], "zh_cat1_source": row[33], "zh_cat2_source": row[34], "zh_spec_source": row[35],
                "zh_description_source": row[36], "zh_details_source": row[37], "zh_approved_by": row[38], "zh_approved_at": row[39],
                "zh_last_commit_id": row[40], "zh_applied_commit_id": row[41],
            })
        return records


def _sku(row: sqlite3.Row | tuple, columns: list[str]) -> str:
    return str(row[columns.index("official_sku")] or "").strip()


def _value(row, columns: list[str], key: str, default=None):
    try:
        index = columns.index(key)
    except ValueError:
        return default
    return row[index]


def _product_row(row, columns: list[str]) -> dict[str, Any]:
    mapping = {
        "canonical_id": "canonical_id", "sku": "official_sku", "name_es": "name_es", "name_zh": "name_zh",
        "current_price": "current_price", "original_price": "original_price", "unit_price": "unit_price_raw",
        "raw_tags": "raw_badges", "is_new_badge": "action_new_badge", "promotion": "promotion_active",
        "sustainable": "sustainable_badge", "status": "status", "missing_count": "consecutive_missing",
        "product_url": "product_url", "image_url": "image_url", "first_seen": "first_seen_at", "last_seen": "last_seen_at",
    }
    result = {target: _value(row, columns, source) for target, source in mapping.items()}
    for key in ("is_new_badge", "promotion", "sustainable"):
        result[key] = bool(result.get(key))
    return result


def _lifecycle_row(row, columns: list[str]) -> dict[str, Any]:
    mapping = {
        "canonical_id": "canonical_id", "official_sku": "official_sku", "first_seen_date": "first_seen_date",
        "last_seen_date": "last_seen_date", "last_status": "current_status", "missing_count": "missing_count",
        "last_missing_date": "last_missing_date", "offline_date": "offline_date",
        "last_state_observation_date": "last_state_observation_date", "ever_offline": "ever_offline",
        "last_run_id": "last_run_id", "updated_at": "updated_at",
    }
    result = {target: _value(row, columns, source) for target, source in mapping.items()}
    result["missing_count"] = str(result.get("missing_count") or 0)
    result["ever_offline"] = "true" if result.get("ever_offline") else "false"
    return result
