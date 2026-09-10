from pathlib import Path

import openpyxl

from action_tracker.database.integration import build_daily_bundle, commit_daily_bundle
from action_tracker.database.parity import compare_with_legacy_files
from action_tracker.excel.reader import ES_MAP, ZH_MAP
from action_tracker.state import save_known_skus
from action_tracker.data_quality.collection import evaluate_and_persist
from action_tracker.database.schema import migrate_v2


def test_parity_command_matches_current_and_lifecycle(tmp_path: Path):
    master = tmp_path / "master.xlsx"
    state = tmp_path / "state"
    state.mkdir()
    record = {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Producto",
              "name_zh": "商品", "current_price": 2.5, "product_url": "https://action.test/p/1001",
              "last_seen": "2026-08-30", "first_seen": "2026-08-30", "status": "CURRENT"}
    wb = openpyxl.Workbook()
    zh = wb.active
    zh.title = "01_SKU_ZH_CURRENT"
    zh.append(list(ZH_MAP))
    zh.append([record.get(key) for key in ZH_MAP.values()])
    es = wb.create_sheet("02_SKU_ES_CURRENT")
    es.append(list(ES_MAP))
    es.append([record.get(key) for key in ES_MAP.values()])
    wb.save(master)
    wb.close()
    known = {"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "first_seen_date": "2026-08-30", "last_seen_date": "2026-08-30", "last_status": "ACTIVE", "missing_count": "0", "ever_offline": "false", "last_run_id": "2026-08-30_010000"}}
    save_known_skus(state, known)
    cfg = {"project_root": tmp_path, "storage": {"mode": "SQLITE_PRIMARY", "db_path": tmp_path / "action.db"}, "paths": {"master": master, "state": state}}
    from types import SimpleNamespace
    status = SimpleNamespace(sku="1001", canonical_id="ACT0001001", status="ACTIVE", source_flag="BOTH", sitemap_present=True, listing_present=True, nuevo_present=False, promotion_present=False, observation_valid=True, first_seen="2026-08-30", missing_count=0)
    migrate_v2(cfg["storage"]["db_path"], role="PRIMARY")
    quality = evaluate_and_persist(cfg["storage"]["db_path"], "2026-08-30_010000", {
        "run_date": "2026-08-30", "sitemap_unique": 1, "listing_unique": 1,
        "current_valid": 1, "price_coverage": 1.0, "cat2_coverage": 1.0,
        "description_coverage": 1.0, "detail_failure_rate": 0.0,
        "category_coverage": {f"cat-{i}": True for i in range(15)},
    })
    bundle = build_daily_bundle(run_id="2026-08-30_010000", observation_date="2026-08-30", qa_state="PASS", today_records={"1001": record}, baseline={}, statuses={"1001": status}, known=known, transition={"known": known, "offline": []}, today_set={"1001"}, observation_complete=True, price_events=[], event_events=[], review_rows=[], run_record={"dry_run": False, "collection_quality_state": quality.state, "collection_metrics_hash": quality.metrics_hash})
    commit_daily_bundle(cfg, bundle, mode="SQLITE_PRIMARY")
    result = compare_with_legacy_files(cfg)
    assert result["status"] == "PASS"
    assert result["mismatch_count"] == 0
