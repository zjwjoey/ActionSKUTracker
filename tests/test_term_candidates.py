import csv
import json
from pathlib import Path

from action_tracker.dictionary import PRODUCT_DICTIONARY_HEADERS, TERM_DICTIONARY_HEADERS
from action_tracker.term_candidates import extract_term_candidates


def _header(path: Path, headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(headers)


def _cfg(tmp_path: Path) -> dict:
    return {"paths": {"snapshots": tmp_path / "snapshots", "dictionary": tmp_path / "dictionary"}}


def _formal_snapshot(cfg: dict, run_id: str) -> Path:
    path = cfg["paths"]["snapshots"] / "2026-08-25" / run_id
    path.mkdir(parents=True)
    (path / "run_report.json").write_text(json.dumps({
        "run_id": run_id, "dry_run": False, "commit_status": "FULL_COMMIT", "run_date": "2026-08-25",
    }), encoding="utf-8")
    (path / "qa_report.json").write_text(json.dumps({"passed": True, "state": "PASS"}), encoding="utf-8")
    return path


def test_term_candidate_extraction_is_incremental_and_does_not_write_term_dictionary(tmp_path):
    cfg = _cfg(tmp_path)
    _formal_snapshot(cfg, "r1")
    enrichment = cfg["paths"]["dictionary"] / "enrichment" / "r1"
    _header(enrichment / "selected_skus.csv", ["sku", "reasons", "source_hash"])
    with (enrichment / "selected_skus.csv").open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["1001", "NEW", "a"])
        writer.writerow(["1002", "NEEDS_REVIEW", "b"])
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS)
    with (cfg["paths"]["dictionary"] / "product_dictionary.csv").open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRODUCT_DICTIONARY_HEADERS)
        writer.writerow({"sku": "1001", "name_es_raw": "Lámpara mágica", "spec_es_raw": "color mágico", "cat1_zh": "家居布置", "name_zh_standard": "魔法灯"})
        writer.writerow({"sku": "1002", "name_es_raw": "Caja mágica", "spec_es_raw": "diseño mágico", "cat1_zh": "家居布置", "name_zh_standard": "魔法盒"})
    _header(cfg["paths"]["dictionary"] / "term_dictionary.csv", TERM_DICTIONARY_HEADERS)
    before = (cfg["paths"]["dictionary"] / "term_dictionary.csv").read_bytes()

    result = extract_term_candidates(cfg, run_id="r1", min_sku_count=2)
    rows = list(csv.DictReader(Path(result["output"]).open(encoding="utf-8")))
    assert result["selected_skus"] == 2
    assert any(row["term_es"] == "mágica" for row in rows)
    assert set(rows[0]) == {
        "term_es", "suggested_zh", "term_type", "occurrence_count", "sku_count",
        "cat1_distribution", "sample_contexts", "source_dates", "decision", "review_status",
    }
    assert all(row["sku_count"] == "2" for row in rows)
    assert (cfg["paths"]["dictionary"] / "term_dictionary.csv").read_bytes() == before


def test_term_candidate_does_not_repeat_a_phrase_made_only_of_known_terms(tmp_path):
    cfg = _cfg(tmp_path)
    _formal_snapshot(cfg, "r1")
    enrichment = cfg["paths"]["dictionary"] / "enrichment" / "r1"
    _header(enrichment / "selected_skus.csv", ["sku", "reasons", "source_hash"])
    with (enrichment / "selected_skus.csv").open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow(["1001", "NEW", "a"])
        csv.writer(fh).writerow(["1002", "NEW", "b"])
    _header(cfg["paths"]["dictionary"] / "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS)
    with (cfg["paths"]["dictionary"] / "product_dictionary.csv").open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRODUCT_DICTIONARY_HEADERS)
        writer.writerow({"sku": "1001", "name_es_raw": "Varios colores", "cat1_zh": "服饰鞋包"})
        writer.writerow({"sku": "1002", "name_es_raw": "Varios colores", "cat1_zh": "服饰鞋包"})
    _header(cfg["paths"]["dictionary"] / "term_dictionary.csv", TERM_DICTIONARY_HEADERS)
    with (cfg["paths"]["dictionary"] / "term_dictionary.csv").open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TERM_DICTIONARY_HEADERS)
        writer.writerow({"term_es": "varios", "term_zh": "多种", "term_type": "spec"})
        writer.writerow({"term_es": "colores", "term_zh": "颜色", "term_type": "spec"})

    result = extract_term_candidates(cfg, run_id="r1", min_sku_count=2)
    assert result["candidate_terms"] == 0
