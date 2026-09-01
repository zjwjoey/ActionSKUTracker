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
    rows = [json.loads(line) for line in (tmp_path / "out" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    rows += [json.loads(line) for line in (tmp_path / "out" / "valid.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows and all(row["messages"][-1]["role"] == "assistant" for row in rows)
    assert all(row["metadata"]["sku"] != "B" for row in rows)
