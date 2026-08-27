import csv
from pathlib import Path

import openpyxl

from action_tracker.dictionary import OVERRIDE_HEADERS
from action_tracker.review_queue import (
    REVIEW_QUEUE_HEADERS,
    _write_queue,
    build_review_queue,
    decide_review,
    load_queue,
)
from action_tracker.services.review import REVIEW_HEADERS


def _cfg(tmp_path: Path) -> dict:
    return {
        "paths": {
            "master": tmp_path / "master.xlsx",
            "dictionary": tmp_path / "dictionary",
            "review_queue": tmp_path / "review_queue",
        }
    }


def _header(path: Path, headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(headers)


def test_build_review_queue_imports_master_and_deduplicates_repeated_build(tmp_path):
    cfg = _cfg(tmp_path)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "06_REVIEW_QUEUE"
    ws.append(REVIEW_HEADERS)
    ws.append(["2026-08-25", "1001", "SITEMAP_ONLY", "sitemap 有、listing 无", "", "", "人工核对", ""])
    wb.save(cfg["paths"]["master"])
    wb.close()
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", [
        "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id", "cat1_es", "cat2_es", "cat1_zh", "cat2_zh",
        "spec_es_raw", "spec_zh_standard", "source_hash", "translation_status", "review_status", "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
    ])
    _header(cfg["paths"]["dictionary"] / "source_damage_report.csv", ["sku", "damaged_fields", "status", "notes"])
    with (cfg["paths"]["dictionary"] / "source_damage_report.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(["1002", "spec_es_raw", "SOURCE_POLLUTED", "UI 文案"])

    first = build_review_queue(cfg)
    second = build_review_queue(cfg)
    queue = load_queue(cfg)
    assert first["total"] == 2
    assert first["new"] == 2
    assert second["new"] == 0
    assert second["changed"] is False
    assert {row["issue_type"] for row in queue.values()} == {"SITEMAP_ONLY", "SOURCE_POLLUTED"}


def test_approved_name_review_writes_field_level_override(tmp_path):
    cfg = _cfg(tmp_path)
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", [
        "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id", "cat1_es", "cat2_es", "cat1_zh", "cat2_zh",
        "spec_es_raw", "spec_zh_standard", "source_hash", "translation_status", "review_status", "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
    ])
    with (cfg["paths"]["dictionary"] / "product_dictionary.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(["1001"] + [""] * 18)
    _header(cfg["paths"]["dictionary"] / "manual_overrides.csv", OVERRIDE_HEADERS)
    row = {
        "review_id": "review-name", "issue_type": "NAME_REVIEW", "sku": "1001", "field": "name_zh_standard",
        "current_value": "旧名", "suggested_value": "", "evidence": "西语证据", "reason": "人工确认",
        "created_at": "2026-08-25T00:00:00+00:00", "status": "PENDING", "source": "TEST",
        "updated_at": "2026-08-25T00:00:00+00:00", "resolution": "",
    }
    _write_queue(cfg, [row])
    result = decide_review(cfg, review_id="review-name", decision="APPROVED", value="新中文名")
    assert result["route"] == "manual_overrides"
    rows = list(csv.DictReader((cfg["paths"]["dictionary"] / "manual_overrides.csv").open(encoding="utf-8-sig")))
    assert rows == [{"scope": "product", "key": "1001", "field": "name_zh_standard", "value": "新中文名",
                     "reason": "review_id=review-name", "source": "UNIFIED_REVIEW_QUEUE", "locked": "0", "updated_at": rows[0]["updated_at"]}]
    assert load_queue(cfg)["review-name"]["status"] == "APPROVED"


def test_rejected_review_does_not_write_dictionary(tmp_path):
    cfg = _cfg(tmp_path)
    _header(cfg["paths"]["dictionary"] / "manual_overrides.csv", OVERRIDE_HEADERS)
    _write_queue(cfg, [{
        "review_id": "review-brand", "issue_type": "BRAND_CANDIDATE", "sku": "1001", "field": "brand_id",
        "current_value": "", "suggested_value": "Brand", "evidence": "", "reason": "", "created_at": "2026-08-25T00:00:00+00:00",
        "status": "PENDING", "source": "TEST", "updated_at": "2026-08-25T00:00:00+00:00", "resolution": "",
    }])
    result = decide_review(cfg, review_id="review-brand", decision="REJECTED")
    assert result["route"] == "status_only"
    assert (cfg["paths"]["dictionary"] / "manual_overrides.csv").read_text(encoding="utf-8") == ",".join(OVERRIDE_HEADERS) + "\n"


def test_dictionary_queue_item_is_auto_resolved_when_its_source_disappears(tmp_path):
    cfg = _cfg(tmp_path)
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", [
        "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id", "cat1_es", "cat2_es", "cat1_zh", "cat2_zh",
        "spec_es_raw", "spec_zh_standard", "source_hash", "translation_status", "review_status", "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
    ])
    _write_queue(cfg, [{
        "review_id": "source-fixed", "issue_type": "NAME_REVIEW", "sku": "1001", "field": "name_zh_standard",
        "current_value": "", "suggested_value": "", "evidence": "", "reason": "", "created_at": "2026-08-25T00:00:00+00:00",
        "status": "APPROVED", "source": "ENRICHMENT:r1", "updated_at": "2026-08-25T00:00:00+00:00", "resolution": "人工确认",
    }])
    result = build_review_queue(cfg)
    assert result["resolved"] == 1
    assert load_queue(cfg)["source-fixed"]["status"] == "RESOLVED"


def test_term_candidate_is_only_written_after_human_approval(tmp_path):
    cfg = _cfg(tmp_path)
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", [
        "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id", "cat1_es", "cat2_es", "cat1_zh", "cat2_zh",
        "spec_es_raw", "spec_zh_standard", "source_hash", "translation_status", "review_status", "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
    ])
    from action_tracker.dictionary import TERM_DICTIONARY_HEADERS
    _header(cfg["paths"]["dictionary"] / "term_dictionary.csv", TERM_DICTIONARY_HEADERS)
    candidates = cfg["paths"]["dictionary"] / "term_candidates" / "r1"
    _header(candidates / "term_candidates.csv", [
        "term_es", "suggested_zh", "term_type", "occurrence_count", "sku_count",
        "cat1_distribution", "sample_contexts", "source_dates", "decision", "review_status",
    ])
    with (candidates / "term_candidates.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(["mágica", "", "general", "2", "2", "{}", "Lámpara mágica", "r1", "PENDING", "PENDING"])
    build_review_queue(cfg, run_id="r1")
    term = next(row for row in load_queue(cfg).values() if row["issue_type"] == "TERM_CANDIDATE")
    result = decide_review(cfg, review_id=term["review_id"], decision="APPROVED", value="魔法", term_type="attribute")
    assert result["route"] == "term_dictionary"
    rows = list(csv.DictReader((cfg["paths"]["dictionary"] / "term_dictionary.csv").open(encoding="utf-8-sig")))
    assert rows[0]["term_es"] == "mágica"
    assert rows[0]["term_type"] == "attribute"
    assert rows[0]["term_zh"] == "魔法"
    rebuilt = build_review_queue(cfg, run_id="r1")
    assert rebuilt["resolved"] == 1
    assert load_queue(cfg)[term["review_id"]]["status"] == "RESOLVED"
