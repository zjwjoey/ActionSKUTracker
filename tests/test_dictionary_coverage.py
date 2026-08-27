import csv
import json
from pathlib import Path

from action_tracker.dictionary_coverage import dictionary_coverage

from test_exporting import _cfg, _record, _run_log, _write_dictionary, _write_master, _write_snapshot


def test_dictionary_coverage_reports_auto_ready_and_writes_artifacts(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "2026-08-26_130145"
    record = _record("1001", last_seen="2026-08-26")
    _write_master(cfg["paths"]["master"], [_run_log(run_id, "2026-08-26")], [record])
    _write_snapshot(cfg["paths"]["snapshots"], run_id, "2026-08-26", [record])
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)

    report = dictionary_coverage(cfg, export_date="2026-08-26", run_id=run_id)
    assert report["total_current_skus"] == 1
    assert report["auto_ready_skus"] == 1
    assert report["auto_ready_rate"] == 1
    report_path = cfg["paths"]["dictionary"] / "reports" / "dictionary_coverage_2026-08-26.json"
    csv_path = cfg["paths"]["dictionary"] / "reports" / "dictionary_coverage_2026-08-26.csv"
    assert json.loads(report_path.read_text(encoding="utf-8"))["auto_ready_skus"] == 1
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["sku"] == "1001"
    assert row["auto_ready"] == "True"
    assert row["name_status"] == "READY"


def test_dictionary_coverage_marks_stale_source_hash_for_review(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "2026-08-26_130145"
    record = _record("1001", last_seen="2026-08-26")
    _write_master(cfg["paths"]["master"], [_run_log(run_id, "2026-08-26")], [record])
    _write_snapshot(cfg["paths"]["snapshots"], run_id, "2026-08-26", [record])
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record, product={
        "sku": "1001", "canonical_id": record["canonical_id"], "name_es_raw": record["name_es"],
        "name_zh_standard": "字典品名", "brand_id": "BrandX", "cat1_es": record["cat1_es"],
        "cat2_es": record["cat2_es"], "cat1_zh": "家务清洁", "cat2_zh": "清洁用品",
        "spec_es_raw": record["spec_es"], "spec_zh_standard": "字典规格", "source_hash": "stale",
        "translation_status": "MODEL_TRANSLATED", "review_status": "UNREVIEWED", "locked": "0",
    })

    report = dictionary_coverage(cfg, export_date="2026-08-26", run_id=run_id)
    assert report["auto_ready_skus"] == 0
    assert report["source_hash_changed"] == 1
    assert report["review_required"] == 1
