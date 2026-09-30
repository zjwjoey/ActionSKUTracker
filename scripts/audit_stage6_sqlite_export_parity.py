"""Audit SQLite CURRENT projection against the Master export after Stage 6 Apply."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.database.repository import ProductionRepository
from action_tracker.excel.reader import load_current
from action_tracker.database.integration import database_path
from action_tracker.config import load_settings

OUT = ROOT / "runtime/stage6/20260913/sqlite_export_parity_v1"
MASTER = ROOT / "runtime/master/Action_Master.xlsx"
DB = ROOT / "runtime/db/action_tracker.db"
PREVIEW = ROOT / "runtime/stage6/20260913/offline_preview_v1"


def norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_expected() -> dict[tuple[str, str], dict[str, str]]:
    with (PREVIEW / "stage6_would_update_48.csv").open(encoding="utf-8-sig", newline="") as handle:
        frozen = {(row["sku"], row["field"]): row for row in csv.DictReader(handle)}
    preview = {
        (row["sku"], row["field"]): row
        for row in (json.loads(line) for line in (PREVIEW / "stage6_apply_preview.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
        if row.get("apply_action") == "WOULD_UPDATE" and row.get("conflict_status") == "NONE"
    }
    if set(frozen) != set(preview):
        raise ValueError("FROZEN_APPLY_SCOPE_PREVIEW_MISMATCH")
    return preview


def main() -> int:
    cfg = load_settings()
    db_records = {row["sku"]: row for row in ProductionRepository(DB).load_current_export_records()}
    master_records = load_current(MASTER)
    expected = load_expected()
    zh_map = {
        "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh",
        "spec": "spec_zh", "description": "desc_zh", "details": "details_zh",
    }
    source_fields = (
        "canonical_id", "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es",
        "current_price", "original_price", "unit_price", "raw_tags", "product_url", "image_url",
        "first_seen", "last_seen", "status", "is_new_badge", "promotion", "sustainable",
    )
    all_fields = source_fields + tuple(zh_map.values())
    mismatches: list[dict[str, Any]] = []
    expected_deltas: list[dict[str, Any]] = []
    for sku in sorted(set(db_records) | set(master_records), key=lambda value: (int(value) if str(value).isdigit() else 10**20, str(value))):
        if sku not in db_records or sku not in master_records:
            mismatches.append({"sku": sku, "kind": "SKU_SET", "db": sku in db_records, "master": sku in master_records})
            continue
        db_row, master_row = db_records[sku], master_records[sku]
        for field in all_fields:
            left, right = db_row.get(field), master_row.get(field)
            if norm(left) == norm(right):
                continue
            applied_field = next((key for key, value in zh_map.items() if value == field), None)
            expected_row = expected.get((sku, applied_field)) if applied_field else None
            if expected_row and norm(left) == norm(expected_row.get("reviewed_value")) and norm(right) == norm(expected_row.get("current_target_value")):
                expected_deltas.append({"sku": sku, "field": applied_field, "master_before": right, "sqlite_after": left})
            else:
                mismatches.append({"sku": sku, "field": field, "db": left, "master": right})
    expected_keys = set(expected)
    actual_keys = {(row["sku"], row["field"]) for row in expected_deltas}
    result = {
        "contract_id": "STAGE6_SQLITE_EXPORT_PARITY_V1",
        "database": str(DB), "master": str(MASTER),
        "sqlite_current_count": len(db_records), "master_current_count": len(master_records),
        "sku_set_equal": set(db_records) == set(master_records),
        "expected_authorized_deltas": len(expected_keys),
        "observed_authorized_deltas": len(actual_keys),
        "unexpected_mismatch_count": len(mismatches),
        "expected_delta_keys_missing": sorted(expected_keys - actual_keys),
        "unexpected_mismatches": mismatches,
        "status": "PASS" if len(expected_keys) == len(actual_keys) == 48 and not mismatches and set(db_records) == set(master_records) else "FAIL",
        "production_writes": 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage6_sqlite_export_parity_v1.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (OUT / "stage6_sqlite_export_expected_deltas.json").write_text(json.dumps(expected_deltas, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (OUT / "stage6_sqlite_export_parity_v1.md").write_text(
        "# Stage 6 SQLite → Export Parity\n\n"
        f"- SQLite CURRENT: {len(db_records)}; Master CURRENT: {len(master_records)}; SKU set equal: {result['sku_set_equal']}\n"
        f"- Expected authorized Chinese deltas: 48; observed: {len(actual_keys)}\n"
        f"- Unexpected mismatches: {len(mismatches)}\n"
        "- This audit is read-only; no production write occurred.\n"
        f"- Status: `{result['status']}`\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
