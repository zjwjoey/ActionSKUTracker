"""Build the 485-row Stage 4 Silver human-review package, read-only."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(r"F:/ActionSKUTracker")
RUN_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
TEST = RUN_DIR / "stage4_test_only_485.jsonl"
PRED = RUN_DIR / "stage4_full_eval_owner_signed_20260912_predictions.jsonl"
OUT_CSV = RUN_DIR / "stage4_485_silver_review_package.csv"
OUT_XLSX = RUN_DIR / "stage4_485_silver_review_package.xlsx"

P0 = {"3219003", "3220894", "3217699", "3223271", "3221705", "3221795", "3201998", "3213267", "3009588", "3209605"}
P1 = {"3217469"}
NUM = re.compile(r"\d+(?:[.,]\d+)?")
TECH = re.compile(r"\b(?:IP\d{2}|USB(?:-[A-Z])?|LED|H[1-9]|P\d{1,2}/?\d?W|\d+(?:[.,]\d+)?\s?(?:W|V|kW|mAh|GB|TB|cm|mm|kg|g|L|ml))\b", re.I)


def obj(messages: list[dict], role: str) -> dict:
    text = next(m["content"] for m in messages if m.get("role") == role)
    return json.loads(text)


def dump(value: object) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def main() -> None:
    tests = {}
    for line in TEST.open(encoding="utf-8"):
        if line.strip():
            row = json.loads(line)
            tests[str(row["metadata"]["sku"])] = row
    predictions = {}
    for line in PRED.open(encoding="utf-8"):
        if line.strip():
            row = json.loads(line)
            predictions[str(row["sku"])] = row
    rows = []
    for sku in sorted(tests, key=lambda value: int(value)):
        test = tests[sku]
        pred = predictions.get(sku, {})
        source = obj(test["messages"], "user")
        expected = obj(test["messages"], "assistant")
        output = pred.get("prediction") or {}
        checks = pred.get("field_checks") or {}
        issues = []
        for field, check in checks.items():
            if not isinstance(check, dict):
                continue
            if check.get("numeric_missing"):
                issues.append(f"{field}:NUMERIC_MISSING={dump(check['numeric_missing'])}")
            if check.get("numeric_extra"):
                issues.append(f"{field}:NUMERIC_ADDED={dump(check['numeric_extra'])}")
            if check.get("spanish_residual"):
                issues.append(f"{field}:SPANISH_RESIDUAL")
        source_all = " ".join(str(source.get(field, "")) for field in source)
        technical = sorted(set(TECH.findall(source_all)))
        risk = "P0_HARD_FACT" if sku in P0 else ("P1_QUALITY" if sku in P1 else ("AUTO_CHECK_FLAG" if issues else "NORMAL_PASS"))
        suggested = "OWNER_REVIEW_REQUIRED" if risk in {"P0_HARD_FACT", "P1_QUALITY"} else ("REVIEW_IF_SAMPLED" if issues else "ACCEPT_PENDING_OWNER")
        rows.append({
            "SKU": sku,
            "field_row": "six_fields",
            "spanish_source": dump(source),
            "reference_chinese": dump(expected),
            "current_prediction": dump(output),
            "numeric_facts_source": ", ".join(sorted(set(NUM.findall(source_all)), key=lambda x: (float(x.replace(',', '.')) if x.replace(',', '.').replace('.', '', 1).isdigit() else 0, x))),
            "technical_tokens_source": ", ".join(technical),
            "brand_source": str(source.get("name", "")),
            "category_source": f"{source.get('cat1', '')} / {source.get('cat2', '')}",
            "guard_findings": "; ".join(issues),
            "automatic_failure_flags": "; ".join(issues),
            "current_risk_level": risk,
            "suggested_disposition": suggested,
            "human_disposition": "PENDING",
            "final_value": "",
            "reviewer": "",
            "reviewed_at": "",
            "gold_status": "MODEL_REVIEWED_SILVER_NOT_HUMAN_GOLD",
            "test_only": "YES",
            "training_eligible": "NO",
        })
    fields = list(rows[0])
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Silver_485_Review"
    ws.append(fields)
    for row in rows:
        ws.append([row[field] for field in fields])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
    widths = {"A": 12, "B": 12, "C": 45, "D": 45, "E": 45, "K": 28, "L": 18, "M": 24, "N": 18, "O": 18, "P": 18, "Q": 18}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    wb.save(OUT_XLSX)
    manifest = {
        "report_version": "stage4-silver-485-review-package-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "pending": sum(row["human_disposition"] == "PENDING" for row in rows),
        "risk_counts": {risk: sum(row["current_risk_level"] == risk for row in rows) for risk in sorted({row["current_risk_level"] for row in rows})},
        "source_test": str(TEST),
        "prediction_source": str(PRED),
        "production_writes": False,
        "training_eligible_rows": 0,
        "csv": str(OUT_CSV),
        "xlsx": str(OUT_XLSX),
    }
    (RUN_DIR / "stage4_silver_coverage_report.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
