"""Validation gates for an Excel -> SQLite V1 mirror."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .connection import connect
from .migration import SHEET_CONFIG, _records, _text, _map, sha256_file


def _source_counts(master_path: Path) -> dict[str, Any]:
    wb = load_workbook(master_path, read_only=True, data_only=True)
    try:
        counts: dict[str, Any] = {}
        for sheet_name, header_row in SHEET_CONFIG.items():
            ws = wb[sheet_name]
            records = list(_records(ws, header_row))
            counts[sheet_name] = {"rows": len(records)}
        # Formal SKU set and current sets are the parity authorities.
        long_ws = wb["08_LONG_TERM_MASTER"]
        long_rows = list(_records(long_ws, 7))
        long_headers = long_rows[0][1] if long_rows else []
        sku_idx = _map(long_headers, "正式SKU")
        status_idx = _map(long_headers, "当前状态")
        counts["08_LONG_TERM_MASTER"]["formal_sku_count"] = len({_text(values[sku_idx]) for _, _, values in long_rows if _text(values[sku_idx])})
        counts["08_LONG_TERM_MASTER"]["unmatched_count"] = sum(1 for _, _, values in long_rows if not _text(values[sku_idx]))
        counts["08_LONG_TERM_MASTER"]["formal_sku_set"] = {
            _text(values[sku_idx]) for _, _, values in long_rows if _text(values[sku_idx])
        }
        counts["08_LONG_TERM_MASTER"]["canonical_by_sku"] = {
            _text(values[sku_idx]): _text(values[_map(long_headers, "实体ID")])
            for _, _, values in long_rows if _text(values[sku_idx])
        }
        counts["08_LONG_TERM_MASTER"]["current_sku_set"] = {_text(values[sku_idx]) for _, _, values in long_rows if _text(values[sku_idx]) and _text(values[status_idx]) == "CURRENT"}
        for sheet_name in ("01_SKU_ZH_CURRENT", "02_SKU_ES_CURRENT"):
            rows = list(_records(wb[sheet_name], 1))
            headers = rows[0][1] if rows else []
            idx = _map(headers, "SKU")
            counts[sheet_name]["sku_set"] = {_text(values[idx]) for _, _, values in rows if _text(values[idx])}
            if sheet_name == "02_SKU_ES_CURRENT":
                cidx = _map(headers, "Canonical_ID")
                counts[sheet_name]["canonical_by_sku"] = {
                    _text(values[idx]): _text(values[cidx]) for _, _, values in rows if _text(values[idx])
                }
        counts["03_PRICE_HISTORY"]["formal_sku_rows"] = sum(1 for _, headers, values in _records(wb["03_PRICE_HISTORY"], 1) if _text(values[_map(headers, "SKU")]))
        counts["04_EVENT_HISTORY"]["formal_sku_rows"] = sum(1 for _, headers, values in _records(wb["04_EVENT_HISTORY"], 1) if _text(values[_map(headers, "SKU")]))
        return counts
    finally:
        wb.close()


def validate_mirror(master_path: Path, db_path: Path) -> dict[str, Any]:
    """Run deterministic parity and SQLite integrity checks."""
    source = _source_counts(master_path)
    result: dict[str, Any] = {
        "master_path": str(master_path),
        "master_sha256": sha256_file(master_path),
        "db_path": str(db_path),
        "checks": {},
        "source": {name: {k: v for k, v in values.items() if not isinstance(v, set) and not k.endswith("_by_sku")} for name, values in source.items()},
    }
    db = connect(db_path, read_only=True)
    try:
        foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk_rows = [dict(row) for row in db.execute("PRAGMA foreign_key_check").fetchall()]
        db_products = {row[0] for row in db.execute("SELECT sku FROM products")}
        db_current = {row[0] for row in db.execute("SELECT sku FROM v_db_current_skus")}
        zh_current = source["01_SKU_ZH_CURRENT"]["sku_set"]
        es_current = source["02_SKU_ES_CURRENT"]["sku_set"]
        formal_skus = source["08_LONG_TERM_MASTER"]["formal_sku_set"]
        long_canonical_by_sku = source["08_LONG_TERM_MASTER"]["canonical_by_sku"]
        es_canonical_by_sku = source["02_SKU_ES_CURRENT"]["canonical_by_sku"]
        es_canonical_mismatches = [
            sku for sku, canonical_id in es_canonical_by_sku.items()
            if canonical_id and canonical_id != long_canonical_by_sku.get(sku)
        ]
        db_counts = {
            "products": db.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "localizations": db.execute("SELECT COUNT(*) FROM product_localizations").fetchone()[0],
            "observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "price_history": db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0],
            "events": db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "reviews": db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
            "source_issues": db.execute("SELECT COUNT(*) FROM migration_source_issues").fetchone()[0],
        }
        metadata_hash = db.execute("SELECT value FROM schema_metadata WHERE key='master_sha256'").fetchone()
        result["db_counts"] = db_counts
        result["source_issue_counts"] = {row[0]: row[1] for row in db.execute("SELECT issue_code, COUNT(*) FROM migration_source_issues GROUP BY issue_code")}
        result["checks"].update({
            "schema_version": db.execute("SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()[0],
            "foreign_keys": foreign_keys == 1,
            "integrity_check": integrity,
            "foreign_key_check": fk_rows,
            "products_exact": db_products == formal_skus and len(db_products) == source["08_LONG_TERM_MASTER"]["formal_sku_count"],
            "es_canonical_exact": not es_canonical_mismatches,
            "zh_es_current_equal": zh_current == es_current,
            "zh_db_current_equal": zh_current == db_current,
            "es_db_current_equal": es_current == db_current,
            "price_history_count_equal": db_counts["price_history"] == source["03_PRICE_HISTORY"]["formal_sku_rows"],
            "events_count_equal": db_counts["events"] == source["04_EVENT_HISTORY"]["formal_sku_rows"],
            "runs_count_equal": db_counts["runs"] == source["05_RUN_LOG"]["rows"],
            "reviews_count_equal": db_counts["reviews"] == source["06_REVIEW_QUEUE"]["rows"],
            "master_hash_recorded": metadata_hash is not None and metadata_hash[0] == result["master_sha256"],
        })
        result["es_canonical_mismatch_count"] = len(es_canonical_mismatches)
        required = (
            "foreign_keys", "integrity_check", "foreign_key_check", "products_exact",
            "zh_es_current_equal", "zh_db_current_equal", "es_db_current_equal", "es_canonical_exact",
            "price_history_count_equal", "events_count_equal", "runs_count_equal",
            "reviews_count_equal", "master_hash_recorded",
        )
        result["status"] = "PASS" if all(
            result["checks"].get(key) is True or (key == "integrity_check" and result["checks"].get(key) == "ok")
            or (key == "foreign_key_check" and result["checks"].get(key) == [])
            for key in required
        ) else "FAIL"
    finally:
        db.close()
    return result
