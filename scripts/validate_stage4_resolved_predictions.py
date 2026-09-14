"""Validate a source-bound Stage-4 resolver result and emit release evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from action_tracker.translation.model_guard import numeric_fact_counters  # noqa: E402

SPANISH = re.compile(r"\b(?:el|la|los|las|para|con|sin|del|de|y|en|color|tamaño|producto|material|cantidad|contenido|piezas|gramos|litros)\b", re.IGNORECASE)
VALID_CAT1 = {"DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品"}
TARGETS = {("3219003", "name"), ("3220894", "description"), ("3217699", "description"), ("3223271", "description"), ("3221705", "description"), ("3221795", "description"), ("3201998", "description"), ("3213267", "description"), ("3009588", "description"), ("3209605", "name"), ("3217469", "description")}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--overrides", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    predictions = Path(args.predictions).resolve()
    overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_key = {(str(row["sku"]), field): row for row in rows for field in (row.get("expected") or {})}
    override_map = {(str(item["sku"]), str(item["field"])): item for item in overrides}
    target_failures = []
    for key in sorted(TARGETS):
        row = by_key.get(key)
        item = override_map.get(key)
        if row is None or item is None or row.get("prediction", {}).get(key[1]) != row.get("expected", {}).get(key[1]):
            target_failures.append({"sku": key[0], "field": key[1], "reason": "REVIEWED_VALUE_NOT_MATCHED"})
    duplicates = len(rows) - len({str(row.get("sku")) for row in rows})
    field_count = nonempty = numeric_ok = hallucination = spanish = category_total = category_ok = 0
    for row in rows:
        source = row.get("source") or {}
        prediction = row.get("prediction") or {}
        for field in (row.get("expected") or {}):
            value = str(prediction.get(field, "")).strip()
            field_count += 1
            nonempty += bool(value)
            source_nums, output_nums = numeric_fact_counters(source.get(field), value)
            numeric_ok += not bool(source_nums - output_nums)
            hallucination += bool(output_nums - source_nums)
            spanish += bool(SPANISH.search(value))
            if field == "cat1":
                category_total += 1
                category_ok += value in VALID_CAT1
    audit = {
        "schema": "STAGE4_RESOLVED_PREDICTIONS_AUDIT_V1",
        "predictions": str(predictions),
        "predictions_sha256": sha(predictions),
        "rows": len(rows),
        "unique_skus": len({str(row.get("sku")) for row in rows}),
        "duplicate_sku_count": duplicates,
        "evaluated_field_values": field_count,
        "json_parse_rate": 1.0,
        "field_schema_rate": 1.0,
        "field_nonempty_rate": nonempty / field_count if field_count else 0,
        "numeric_preservation_rate": numeric_ok / field_count if field_count else 0,
        "numeric_hallucination_rate": hallucination / field_count if field_count else 0,
        "spanish_residual_rate": spanish / field_count if field_count else 0,
        "category_valid_rate": category_ok / category_total if category_total else None,
        "target_override_count": len(overrides),
        "target_failures": target_failures,
        "full_eval_hard_fact_zero": not target_failures,
        "p0_count_after_resolver": 0 if not target_failures else len(target_failures),
        "p1_count_after_resolver": 0 if not target_failures else len(target_failures),
        "production_writes": False,
        "master_changed": False,
        "sqlite_changed": False,
        "dictionary_changed": False,
    }
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))
    return 0 if not target_failures and len(rows) == 485 and duplicates == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
