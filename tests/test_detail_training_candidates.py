import csv
import json
from pathlib import Path

from scripts.build_detail_training_candidates import build_candidates
from scripts.build_local_qwen_dataset import DETAIL_DICTIONARY_HEADERS


def test_build_detail_training_candidates_is_source_only_and_deduplicated(tmp_path: Path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps({"sku": "2", "detail": {"sku": "2", "desc_es": "Dos", "details_es": "Cantidad: 2 unidades"}}, ensure_ascii=False) + "\n", encoding="utf-8")
    second.write_text(
        "\n".join([
            json.dumps({"sku": "1", "detail": {"sku": "1", "desc_es": "Uno", "details_es": "Color: Azul"}}, ensure_ascii=False),
            json.dumps({"sku": "2", "detail": {"sku": "2", "desc_es": "Dos actualizado", "details_es": "Cantidad: 3 unidades"}}, ensure_ascii=False),
        ]) + "\n", encoding="utf-8")
    output = tmp_path / "candidates.csv"
    result = build_candidates([first, second], output, source_run_id="run-1")
    assert result["rows"] == 2
    with output.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert tuple(rows[0]) == DETAIL_DICTIONARY_HEADERS
    assert [row["sku"] for row in rows] == ["1", "2"]
    assert rows[1]["description_es_raw"] == "Dos actualizado"
    assert all(row["review_status"] == "PENDING" and not row["description_zh_standard"] for row in rows)
