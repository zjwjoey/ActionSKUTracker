"""Parity checks between the SQLite V2 projections and frozen Excel/CSV data."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..excel.reader import load_current
from ..state import load_known_skus
from .integration import database_path
from .repository import ProductionRepository


def compare_with_legacy_files(cfg: Mapping[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    """Compare CURRENT and lifecycle identities without changing either side."""
    path = Path(db_path) if db_path else database_path(cfg)
    repo = ProductionRepository(path)
    db_current = repo.load_current_products()
    excel_current = load_current(Path(cfg["paths"]["master"]))
    db_known = repo.load_known_skus()
    file_known = load_known_skus(Path(cfg["paths"]["state"]))
    current_missing_in_db = sorted(set(excel_current) - set(db_current), key=_sku_key)
    current_extra_in_db = sorted(set(db_current) - set(excel_current), key=_sku_key)
    lifecycle_missing_in_db = sorted(set(file_known) - set(db_known), key=_sku_key)
    lifecycle_extra_in_db = sorted(set(db_known) - set(file_known), key=_sku_key)
    fact_mismatches = _fact_mismatches(excel_current, db_current)
    lifecycle_mismatches = _lifecycle_mismatches(file_known, db_known)
    return {
        "database": str(path),
        "current_excel": len(excel_current), "current_db": len(db_current),
        "known_excel": len(file_known), "known_db": len(db_known),
        "current_missing_in_db": current_missing_in_db,
        "current_extra_in_db": current_extra_in_db,
        "lifecycle_missing_in_db": lifecycle_missing_in_db,
        "lifecycle_extra_in_db": lifecycle_extra_in_db,
        "fact_mismatches": fact_mismatches,
        "lifecycle_mismatches": lifecycle_mismatches,
        "mismatch_count": len(current_missing_in_db) + len(current_extra_in_db)
        + len(lifecycle_missing_in_db) + len(lifecycle_extra_in_db)
        + len(fact_mismatches) + len(lifecycle_mismatches),
        "status": "PASS" if not (current_missing_in_db or current_extra_in_db or lifecycle_missing_in_db
                                   or lifecycle_extra_in_db or fact_mismatches or lifecycle_mismatches) else "MISMATCH",
    }


def _fact_mismatches(left: dict[str, dict], right: dict[str, dict]) -> list[dict[str, Any]]:
    fields = ("canonical_id", "name_es", "current_price", "original_price", "unit_price",
              "raw_tags", "product_url", "image_url", "first_seen", "last_seen")
    mismatches = []
    for sku in sorted(set(left) & set(right), key=_sku_key):
        differences = {
            field: {"excel": left[sku].get(field), "db": right[sku].get(field)}
            for field in fields if _normal(left[sku].get(field)) != _normal(right[sku].get(field))
        }
        if differences:
            mismatches.append({"sku": sku, "fields": differences})
    return mismatches


def _lifecycle_mismatches(left: dict[str, dict], right: dict[str, dict]) -> list[dict[str, Any]]:
    fields = ("canonical_id", "first_seen_date", "last_seen_date", "last_status", "missing_count",
              "last_missing_date", "offline_date", "ever_offline", "last_run_id")
    mismatches = []
    for sku in sorted(set(left) & set(right), key=_sku_key):
        differences = {
            field: {"excel": left[sku].get(field), "db": right[sku].get(field)}
            for field in fields if _normal(left[sku].get(field)) != _normal(right[sku].get(field))
        }
        if differences:
            mismatches.append({"sku": sku, "fields": differences})
    return mismatches


def _normal(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sku_key(value: str) -> tuple[int, int, str]:
    return (0, int(value), value) if str(value).isdigit() else (1, 0, str(value))
