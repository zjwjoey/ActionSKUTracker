"""Evaluate a saved adapter benchmark after deterministic dictionary projection.

The raw adapter benchmark intentionally measures unconstrained model output.
This evaluator measures the production-shaped Stage 5 contract: approved
dictionary/category values win before model values, while the Guard still
checks the merged record for numeric, technical-token and language safety.
It is read-only and never writes Master, Dictionary or SQLite.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.config import load_settings  # noqa: E402
from action_tracker.dictionary_resolver import resolve_record  # noqa: E402
from action_tracker.exporting.dictionary_join import load_dictionary_context  # noqa: E402
from action_tracker.stage5.pipeline import RULE_SOURCES  # noqa: E402
from action_tracker.translation.model_guard import numeric_fact_counters, validate_model_output  # noqa: E402


def parse(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_record(row: dict[str, Any]) -> dict[str, str]:
    source = row.get("source") or {}
    return {
        "sku": str(row.get("sku") or ""),
        "name_es": str(source.get("name") or ""),
        "cat1_es": str(source.get("cat1") or ""),
        "cat2_es": str(source.get("cat2") or ""),
        "spec_es": str(source.get("spec") or ""),
        "desc_es": str(source.get("description") or ""),
        "details_es": str(source.get("details") or ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    predictions = parse(args.predictions)
    context = load_dictionary_context(load_settings(ROOT / "config/settings.yaml"))
    fields = ("name", "cat1", "cat2", "spec", "description", "details")
    fixed = 0
    fixed_fields: Counter[str] = Counter()
    numeric_missing = numeric_extra = spanish = schema_errors = 0
    category_total = category_ok = 0
    rows = []
    for row in predictions:
        prediction = dict(row.get("prediction") or {})
        source = row.get("source") or {}
        resolution = resolve_record(source_record(row), context)
        projection = dict(prediction)
        applied = []
        for field in fields:
            resolved = resolution.fields.get(field)
            if not resolved or resolved.status != "READY" or resolved.source not in RULE_SOURCES:
                continue
            candidate = {field: resolved.value}
            check = validate_model_output({field: source.get(field, "")}, candidate, expected_fields=[field])
            if check.accepted:
                if projection.get(field) != resolved.value:
                    fixed += 1
                    fixed_fields[field] += 1
                projection[field] = resolved.value
                applied.append({"field": field, "source": resolved.source, "value": resolved.value})
        expected_fields = set((row.get("expected") or {}).keys())
        if set(projection) != expected_fields or not all(isinstance(projection.get(f), str) for f in expected_fields):
            schema_errors += 1
        for field in expected_fields:
            value = str(projection.get(field) or "")
            expected, actual = numeric_fact_counters(source.get(field), value)
            numeric_missing += sum((expected - actual).values())
            numeric_extra += sum((actual - expected).values())
            guard = validate_model_output({field: source.get(field, "")}, {field: value}, expected_fields=[field])
            reasons = list(guard.field_reasons.get(field, ()))
            spanish += int("SPANISH_RESIDUAL" in reasons)
            if field == "cat1":
                category_total += 1
                category_ok += int(guard.accepted and value in {
                    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容",
                    "家居布置", "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具",
                    "兴趣手作", "园艺户外", "运动用品",
                })
        rows.append({"sku": row.get("sku"), "applied_dictionary_fields": applied, "projected": projection})
    report = {
        "status": "RULE_FIRST_PROJECTION_COMPLETE",
        "rows": len(predictions),
        "dictionary_projection_fields_changed": fixed,
        "dictionary_projection_by_field": dict(sorted(fixed_fields.items())),
        "schema_error_rows": schema_errors,
        "numeric_missing_tokens": numeric_missing,
        "numeric_extra_tokens": numeric_extra,
        "spanish_residual_fields": spanish,
        "category_total": category_total,
        "category_valid_rate": category_ok / category_total if category_total else None,
        "source_predictions": str(args.predictions.resolve()),
        "production_writes": False,
        "master_changed": False,
        "dictionary_changed": False,
        "sqlite_changed": False,
        "rows_detail": rows,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "rows", "dictionary_projection_fields_changed", "dictionary_projection_by_field", "numeric_missing_tokens", "numeric_extra_tokens", "category_valid_rate", "production_writes")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
