"""Dump per-row model predictions for manual source-fidelity review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import compare_qwen_baselines as compare


def build_review_rows(rows: list[dict[str, Any]], predictions: list[dict[str, Any] | None], allowed_brands=None) -> list[dict[str, Any]]:
    if len(rows) != len(predictions):
        raise ValueError("PREDICTION_ROW_COUNT_MISMATCH")
    allowed_brands = allowed_brands or {}
    result = []
    for row, prediction in zip(rows, predictions):
        source, expected = compare.source_target(row)
        sku = str(row.get("metadata", {}).get("sku", ""))
        _, issues = compare.score_prediction(row, prediction, allowed_brands.get(sku, ()))
        result.append({
            "review_id": f"QWEN3_ACTION_20260911_V1:{sku}",
            "sku": sku,
            "status": "PENDING_MANUAL_REVIEW",
            "source_es": source,
            "reference_zh": expected,
            "model_prediction": prediction,
            "automated_issues": issues,
            "review_instruction": "逐字段对照 source_es 与 model_prediction；参考 reference_zh 只作已审核译文，不替代源字段忠实度判断。",
        })
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--adapter-path", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-input-length", type=int, default=768)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--strict-prompt", action="store_true")
    args = parser.parse_args()
    rows = compare.load_rows(args.test_file)
    cfg = compare.load_settings(ROOT / "config" / "settings.yaml")
    context = compare.load_dictionary_context(cfg)
    allowed = compare.allowed_brands_by_sku(rows, context)
    predictions = compare.run_model(args.model_path, rows, args.batch_size, args.max_input_length, args.max_new_tokens, args.adapter_path, strict_prompt=args.strict_prompt)
    records = build_review_rows(rows, predictions, allowed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "output": str(args.output.resolve()),
        "rows": len(records),
        "rows_with_automated_issues": sum(bool(record["automated_issues"]) for record in records),
        "status": "PENDING_MANUAL_REVIEW",
        "benchmark_policy": "stage4_safety_first_v3_2026-09-11",
    }
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
