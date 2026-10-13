import csv
import json
from pathlib import Path

from scripts.build_local_qwen_dataset import build_dataset


def _write(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_build_local_qwen_dataset_uses_only_trusted_rows_and_is_offline(tmp_path):
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    _write(dictionary / "product_dictionary.csv", [
        "sku", "name_es_raw", "name_zh_standard", "cat1_es", "cat2_es",
        "cat1_zh", "cat2_zh", "spec_es_raw", "spec_zh_standard", "source_hash",
        "translation_status", "review_status", "locked",
    ], [
        {"sku": "A", "name_es_raw": "Espumador portátil", "name_zh_standard": "便携式奶泡器", "cat1_es": "Hogar", "cat2_es": "", "cat1_zh": "家务清洁", "cat2_zh": "", "spec_es_raw": "220 V", "spec_zh_standard": "220V", "source_hash": "hA", "translation_status": "HUMAN_REVIEWED", "review_status": "HUMAN_REVIEWED", "locked": "1"},
        {"sku": "B", "name_es_raw": "Producto sin revisar", "name_zh_standard": "未审核", "cat1_es": "Hogar", "cat2_es": "", "cat1_zh": "家务清洁", "cat2_zh": "", "spec_es_raw": "", "spec_zh_standard": "", "source_hash": "hB", "translation_status": "MODEL_TRANSLATED", "review_status": "UNREVIEWED", "locked": "0"},
        {"sku": "C", "name_es_raw": "Lámpara 220 V", "name_zh_standard": "灯", "cat1_es": "Hogar", "cat2_es": "", "cat1_zh": "家务清洁", "cat2_zh": "", "spec_es_raw": "", "spec_zh_standard": "", "source_hash": "hC", "translation_status": "HUMAN_REVIEWED", "review_status": "HUMAN_REVIEWED", "locked": "1"},
    ])
    _write(dictionary / "brand_dictionary.csv", ["canonical_name", "aliases_es"], [])
    result = build_dataset(dictionary, tmp_path / "out")
    assert result["total_count"] == 3  # A name/spec/cat1; C is rejected for dropped 220
    assert result["train_count"] + result["valid_count"] == result["total_count"]
    assert result["skipped"]["UNTRUSTED_STATUS"] == 1
    assert result["skipped"]["NUMERIC_MISMATCH"] == 1
    assert result["model_calls"] == 0
    assert result["naming_policy_version"] == "NAMING_AND_SPEC_PLANNING_STANDARD_V1.0"
    assert result["policy_documents"]
    assert {"name", "spec", "cat1", "cat2"} <= set(result["field_policy_coverage"])
    rows = [json.loads(line) for line in (tmp_path / "out" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    rows += [json.loads(line) for line in (tmp_path / "out" / "valid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and all(row["messages"][-1]["role"] == "assistant" for row in rows)
    assert all(row["metadata"]["sku"] != "B" for row in rows)
    user_payloads = [json.loads(row["messages"][1]["content"]) for row in rows]
    assert all(payload["naming_policy_version"] == "NAMING_AND_SPEC_PLANNING_STANDARD_V1.0" for payload in user_payloads)
    assert all(payload["field_policy"] and payload["global_guardrails"] for payload in user_payloads)


def test_build_local_qwen_dataset_reads_v1_phrase_and_tech_token_schemas(tmp_path):
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    _write(dictionary / "product_dictionary.csv", [
        "sku", "name_es_raw", "name_zh_standard", "cat1_es", "cat2_es",
        "cat1_zh", "cat2_zh", "spec_es_raw", "spec_zh_standard", "source_hash",
        "translation_status", "review_status", "locked",
    ], [])
    _write(dictionary / "phrase_dictionary.csv", [
        "schema_version", "source_phrase", "zh_value", "semantic_type",
        "preferred_target", "allowed_targets", "category_context", "review_status", "notes",
    ], [{
        "schema_version": "1.0", "source_phrase": "varios colores", "zh_value": "多种颜色",
        "semantic_type": "VARIANT", "preferred_target": "spec_zh", "allowed_targets": "spec_zh",
        "category_context": "", "review_status": "SEED_REVIEWED", "notes": "",
    }])
    _write(dictionary / "tech_token_dictionary.csv", [
        "schema_version", "token", "canonical_token", "token_type", "keep_original",
        "normalization_rule", "review_status", "notes",
    ], [{
        "schema_version": "1.0", "token": "USB-C", "canonical_token": "USB-C", "token_type": "INTERFACE",
        "keep_original": "1", "normalization_rule": "uppercase", "review_status": "SEED_REVIEWED", "notes": "",
    }])
    result = build_dataset(dictionary, tmp_path / "out")
    assert result["total_count"] == 2
    assert result["skipped"].get("EMPTY_TERM", 0) == 0
    rows = []
    for filename in ("train.jsonl", "valid.jsonl"):
        rows.extend(json.loads(line) for line in (tmp_path / "out" / filename).read_text(encoding="utf-8").splitlines() if line)
    assert {row["metadata"]["source_kind"] for row in rows} == {"PHRASE", "TECH_TOKEN"}


def test_detail_training_pairs_require_trusted_fresh_source_and_emit_flat_fields(tmp_path):
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    product_headers = [
        "sku", "name_es_raw", "name_zh_standard", "cat1_es", "cat2_es",
        "cat1_zh", "cat2_zh", "spec_es_raw", "spec_zh_standard", "source_hash",
        "translation_status", "review_status", "locked",
    ]
    _write(dictionary / "product_dictionary.csv", product_headers, [])
    _write(dictionary / "brand_dictionary.csv", ["canonical_name", "aliases_es"], [])
    _write(dictionary / "detail_translation_dictionary.csv", [
        "sku", "source_hash", "name_es_raw", "cat1_es", "cat2_es", "spec_es_raw",
        "description_es_raw", "description_zh_standard",
        "details_es_raw", "details_zh_standard", "review_status", "locked",
        "source_run_id", "source_commit_id", "updated_at", "notes",
    ], [
        {
            "sku": "D1", "source_hash": "detail-hash-1",
            "name_es_raw": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es_raw": "500 ml",
            "description_es_raw": "Para cocina 500 ml", "description_zh_standard": "适用于厨房500ml容量",
            "details_es_raw": "Cantidad: 2 unidades | Número del artículo: D1",
            "details_zh_standard": "数量：2件｜商品编号：D1",
            "review_status": "HUMAN_REVIEWED", "locked": "1",
            "source_run_id": "run-1", "source_commit_id": "commit-1", "updated_at": "2026-09-02", "notes": "",
        },
        {
            "sku": "D2", "source_hash": "detail-hash-2",
            "name_es_raw": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es_raw": "",
            "description_es_raw": "Texto", "description_zh_standard": "文本",
            "details_es_raw": "Color: Azul", "details_zh_standard": "颜色：蓝色",
            "review_status": "PENDING", "locked": "0",
            "source_run_id": "run-1", "source_commit_id": "commit-1", "updated_at": "2026-09-02", "notes": "",
        },
    ])
    result = build_dataset(dictionary, tmp_path / "out")
    assert result["detail_pair_count"] == 2
    assert result["detail_multi_field_count"] == 1
    assert result["detail_source_hash"]
    assert result["skipped"]["DETAIL_UNTRUSTED_STATUS"] == 1
    rows = []
    for filename in ("train.jsonl", "valid.jsonl"):
        rows.extend(json.loads(line) for line in (tmp_path / "out" / filename).read_text(encoding="utf-8").splitlines() if line)
    detail_rows = [row for row in rows if row["metadata"]["sku"] == "D1"]
    assert len(detail_rows) == 3
    assert {row["metadata"]["field"] for row in detail_rows} == {"description", "details", "description+details"}
    for row in detail_rows:
        assistant = json.loads(row["messages"][-1]["content"])
        assert all(isinstance(value, str) for value in assistant["fields"].values())
        user = json.loads(row["messages"][1]["content"])
        assert user["source_facts"]["desc_es"] and user["source_facts"]["details_es"]


def test_detail_training_schema_is_fail_closed(tmp_path):
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    _write(dictionary / "product_dictionary.csv", ["sku"], [])
    _write(dictionary / "brand_dictionary.csv", ["canonical_name", "aliases_es"], [])
    _write(dictionary / "detail_translation_dictionary.csv", ["sku", "wrong"], [])
    try:
        build_dataset(dictionary, tmp_path / "out")
    except ValueError as exc:
        assert "DETAIL_DICTIONARY_SCHEMA_MISMATCH" in str(exc)
    else:
        raise AssertionError("invalid detail dictionary schema must fail closed")


def test_detail_training_approved_is_offline_only_but_accepted_by_dataset_builder(tmp_path):
    dictionary = tmp_path / "dictionary"
    dictionary.mkdir()
    _write(dictionary / "product_dictionary.csv", ["sku"], [])
    _write(dictionary / "brand_dictionary.csv", ["canonical_name", "aliases_es"], [])
    _write(dictionary / "detail_translation_dictionary.csv", [
        "sku", "source_hash", "name_es_raw", "cat1_es", "cat2_es", "spec_es_raw",
        "description_es_raw", "description_zh_standard", "details_es_raw", "details_zh_standard",
        "review_status", "locked", "source_run_id", "source_commit_id", "updated_at", "notes",
    ], [{
        "sku": "D3", "source_hash": "h3", "name_es_raw": "Producto", "cat1_es": "Hogar", "cat2_es": "",
        "spec_es_raw": "", "description_es_raw": "Uso 10 ml", "description_zh_standard": "用途10ml",
        "details_es_raw": "Número del artículo: D3", "details_zh_standard": "商品编号：D3",
        "review_status": "TRAINING_APPROVED", "locked": "0", "source_run_id": "", "source_commit_id": "", "updated_at": "", "notes": "",
    }])
    result = build_dataset(dictionary, tmp_path / "out")
    assert result["detail_field_example_count"] == 2
    assert result["detail_multi_field_count"] == 1
