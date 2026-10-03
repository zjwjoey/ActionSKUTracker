from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_action_localization_lite_v2_review.py"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _make_v1_input(path: Path) -> None:
    path.mkdir(parents=True)
    sample_rows = [{"sku": str(2500000 + i), "sample_group": "FIXTURE"} for i in range(150)]
    with (path / "lite_150_sample_manifest.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sku", "sample_group"])
        writer.writeheader()
        writer.writerows(sample_rows)

    columns = [
        "sku", "field_name", "source_es", "qwen_zh", "reviewed_zh", "review_decision",
        "source_hash", "provider", "model",
    ]
    rows: list[dict[str, str]] = []
    for i in range(150):
        sku = str(2500000 + i)
        for field in FIELDS:
            source = {
                "name": "Cuaderno A4" if i == 0 else f"Producto {i}",
                "cat1": "Oficina y papelería",
                "cat2": "Cuadernos",
                "spec": "A4" if i == 0 else "1 unidad",
                "description": "Producto de prueba",
                "details": "Número del artículo: " + sku,
            }[field]
            qwen = "笔记本" if i == 0 and field == "name" else ("测试商品" if field == "name" else "测试字段")
            rows.append({
                "sku": sku,
                "field_name": field,
                "source_es": source,
                "qwen_zh": qwen,
                "reviewed_zh": qwen,
                "review_decision": "KEEP",
                "source_hash": f"hash-{sku}-{field}",
                "provider": "qwen_mt",
                "model": "qwen-mt-flash",
            })
    with (path / "lite_review_rows.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    (path / "lite_qwen_results.csv").write_text("sku,field_name,qwen_zh\n", encoding="utf-8-sig")


def test_v2_reviews_all_names_without_provider_calls(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    output = tmp_path / "v2"
    _make_v1_input(v1)
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--v1-dir", str(v1), "--output-dir", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["sku_count"] == 150
    assert summary["p0_name_reviewed"] == 150
    assert summary["qwen_calls"] == 0
    assert summary["master_writes"] == 0
    with (output / "lite_name_review_150_v2.csv").open(encoding="utf-8-sig", newline="") as fh:
        names = list(csv.DictReader(fh))
    assert len(names) == 150
    assert names[0]["decision_v2"] == "CORRECTED"
    assert names[0]["name_reviewed_zh_v2"].startswith("A4")
    assert list(names[0]) == [
        "sku", "name_es", "name_qwen_zh", "name_reviewed_zh_v1", "name_reviewed_zh_v2",
        "decision_v1", "decision_v2", "correction_type", "review_note", "cat1_es", "cat2_es", "spec_es",
    ]


def test_v2_keeps_source_and_qwen_values_unchanged(tmp_path: Path) -> None:
    v1 = tmp_path / "v1"
    output = tmp_path / "v2"
    _make_v1_input(v1)
    before = (v1 / "lite_review_rows.csv").read_bytes()
    subprocess.run([sys.executable, str(RUNNER), "--v1-dir", str(v1), "--output-dir", str(output)], cwd=ROOT, check=True)
    assert (v1 / "lite_review_rows.csv").read_bytes() == before
    with (output / "lite_translation_150_v2.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 900
    assert all(row["qwen_zh"] for row in rows)
