from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_action_localization_lite_v3_review.py"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _write_v2_fixture(path: Path) -> None:
    path.mkdir(parents=True)
    with (path / "lite_150_sample_manifest_v2.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sku", "sample_group"])
        writer.writeheader()
        writer.writerows({"sku": str(2500000 + i), "sample_group": "FIXTURE"} for i in range(150))
    columns = ["sku", "field_name", "priority", "source_es", "qwen_zh", "reviewed_zh_v1", "reviewed_zh_v2", "decision_v1", "decision_v2", "cat1_es", "cat2_es", "spec_es", "description_es", "details_es"]
    rows = []
    for i in range(150):
        sku = str(2500000 + i)
        for field in FIELDS:
            source = {
                "name": "Cuaderno A4" if i == 0 else f"Producto {i}",
                "cat1": "Oficina y papelería", "cat2": "Cuadernos",
                "spec": "3x50 gramos" if i == 1 else "A4",
                "description": "Producto de prueba", "details": f"Número del artículo: {sku}",
            }[field]
            qwen = "笔记本" if i == 0 and field == "name" else ("3×50克" if i == 1 and field == "spec" else ("A4" if field == "spec" else ("测试商品" if field == "name" else "测试字段")))
            rows.append({"sku": sku, "field_name": field, "priority": "P0" if field == "name" else "P1" if field in {"cat1", "cat2", "spec"} else "P2", "source_es": source, "qwen_zh": qwen, "reviewed_zh_v1": qwen, "reviewed_zh_v2": qwen, "decision_v1": "KEEP", "decision_v2": "REVIEW_REQUIRED" if i == 1 and field == "spec" else "KEEP", "cat1_es": "Oficina y papelería", "cat2_es": "Cuadernos", "spec_es": source if field == "spec" else "A4", "description_es": "Producto de prueba", "details_es": f"Número del artículo: {sku}"})
    with (path / "lite_translation_150_v2.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    (path / "lite_qwen_results.csv").write_text("sku,field_name,qwen_zh\n", encoding="utf-8-sig")


def _write_audit(path: Path) -> None:
    wb = openpyxl.Workbook()
    names = wb.active; names.title = "Name Audit"
    names_headers = ["sku", "name_es", "name_qwen_zh", "name_reviewed_zh_v2", "decision_v2", "cat1_es", "cat2_es", "spec_es", "assistant_audit", "suggested_name_zh", "assistant_issue_type", "assistant_note"]
    names.append(names_headers)
    for i in range(150):
        sku = str(2500000 + i)
        names.append([sku, "Cuaderno A4" if i == 0 else f"Producto {i}", "笔记本" if i == 0 else "测试商品", "笔记本" if i == 0 else "测试商品", "KEEP", "Oficina y papelería", "Cuadernos", "A4", "NEEDS_CORRECTION" if i == 0 else "PASS", "A4笔记本" if i == 0 else "", "MODEL_OR_SPEC" if i == 0 else "", "A4 is a required identifier" if i == 0 else ""])
    owner = wb.create_sheet("Owner Queue Audit")
    owner_headers = ["sku", "field_name", "source_es", "cat1_es", "cat2_es", "spec_es", "assistant_owner_decision", "assistant_suggestion", "assistant_note"]
    owner.append(owner_headers)
    owner.append(["2500001", "spec", "3x50 gramos", "Oficina y papelería", "Cuadernos", "3x50 gramos", "KEEP", "3×50克", "x/× normalization is equivalent"])
    wb.save(path)


def test_v3_uses_audit_as_behavior_regression_and_normalizes_numeric_formats(tmp_path: Path) -> None:
    v2 = tmp_path / "v2"; audit = tmp_path / "audit.xlsx"; output = tmp_path / "v3"
    _write_v2_fixture(v2); _write_audit(audit)
    result = subprocess.run([sys.executable, str(RUNNER), "--v2-dir", str(v2), "--audit-xlsx", str(audit), "--qwen-results", str(v2 / "lite_qwen_results.csv"), "--output-dir", str(output)], cwd=ROOT, capture_output=True, text=True, check=True)
    summary = json.loads(result.stdout)
    assert summary["sku_count"] == 150
    assert summary["p0_name_counts"] == {"CORRECTED": 1, "KEEP": 149}
    assert summary["p1_counts"] == {"KEEP": 450}
    assert summary["qwen_calls"] == 0
    assert summary["master_writes"] == 0
    assert summary["v2_to_v3_changed_names"] == 1
    with (output / "lite_name_v2_v3_changes.csv").open(encoding="utf-8-sig", newline="") as fh:
        changes = list(csv.DictReader(fh))
    assert changes[0]["v3_zh"] == "A4笔记本"
