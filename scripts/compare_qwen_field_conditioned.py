"""Evaluate a field-conditioned QLoRA adapter on its isolated test set.

This is a read-only evaluator.  Each JSONL row contains one source field and
declares the same field in ``metadata.field``.  Raw Qwen, the candidate
adapter, and the dictionary resolver are scored with the same field-level
guard; no Master, SQLite or dictionary file is changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from compare_qwen_baselines import (  # noqa: E402
    aggregate,
    allowed_brands_by_sku,
    load_rows,
    resolver_predictions,
    run_model,
)
from action_tracker.config import load_settings  # noqa: E402
from action_tracker.exporting.dictionary_join import load_dictionary_context  # noqa: E402


def field_resolver_predictions(rows: list[dict], context) -> list[dict[str, str]]:
    """Resolve only the field requested by each test row."""

    full = resolver_predictions(rows, context)
    output: list[dict[str, str]] = []
    for row, prediction in zip(rows, full):
        field = str(row.get("metadata", {}).get("field", "")).strip()
        output.append({field: str(prediction.get(field, ""))} if field else prediction)
    return output


def validate_field_rows(rows: list[dict]) -> None:
    """Reject a test file that is not actually field-conditioned."""

    if not rows:
        raise ValueError("FIELD_CONDITIONED_TEST_EMPTY")
    if any(not str(row.get("metadata", {}).get("field", "")).strip() for row in rows):
        raise ValueError("FIELD_CONDITIONED_FIELD_MISSING")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # Windows PowerShell often exposes a GBK stdout; reports contain
        # Spanish/Chinese evidence and must never fail at the final print.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-path", default="runtime/models/Qwen3-8B")
    ap.add_argument(
        "--adapter-path",
        default="runtime/training/qwen3_8b/20260911/field_conditioned_v1/formal_qlora_200_earlystop/adapter",
    )
    ap.add_argument(
        "--test-file",
        default="runtime/training/qwen3_8b/20260911/field_conditioned_v1/field_conditioned_test.jsonl",
    )
    ap.add_argument(
        "--output",
        default="runtime/training/qwen3_8b/20260911/field_conditioned_v1/field_conditioned_benchmark.json",
    )
    ap.add_argument("--limit", type=int, default=0, help="0 evaluates every test row")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--max-input-length", type=int, default=512)
    ap.add_argument("--max-new-tokens", type=int, default=160)
    args = ap.parse_args()

    model = Path(args.model_path)
    model = model if model.is_absolute() else ROOT / model
    adapter = Path(args.adapter_path)
    adapter = adapter if adapter.is_absolute() else ROOT / adapter
    test = Path(args.test_file)
    test = test if test.is_absolute() else ROOT / test
    output = Path(args.output)
    output = output if output.is_absolute() else ROOT / output

    rows = load_rows(test)
    if args.limit:
        rows = rows[: args.limit]
    validate_field_rows(rows)

    raw = run_model(model, rows, args.batch_size, args.max_input_length, args.max_new_tokens)
    tuned = run_model(model, rows, args.batch_size, args.max_input_length, args.max_new_tokens, adapter)
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    allowed = allowed_brands_by_sku(rows, context)
    resolver = field_resolver_predictions(rows, context)

    reports = [
        aggregate(rows, raw, "raw_qwen_field_conditioned", allowed),
        aggregate(rows, tuned, "field_conditioned_qlora", allowed),
        aggregate(rows, resolver, "dictionary_resolver_field_conditioned", allowed),
    ]
    tuned_metrics = reports[1]
    report = {
        "evaluation_policy_version": "field_conditioned_safety_v2_2026-09-11",
        "test_file": str(test.resolve()),
        "adapter_path": str(adapter.resolve()),
        "rows": len(rows),
        "batch_size": args.batch_size,
        "max_input_length": args.max_input_length,
        "max_new_tokens": args.max_new_tokens,
        "models": reports,
        "field_counts": {
            field: sum(1 for row in rows if row["metadata"]["field"] == field)
            for field in sorted({row["metadata"]["field"] for row in rows})
        },
        "automated_safety": {
            "requires": [
                "JSON/schema 100%",
                "required field completeness 100%",
                "no dropped or hallucinated numeric facts",
                "no ordinary Spanish or English residual",
                "fixed 15-category cat1 values when cat1 is evaluated",
            ],
            "candidate_pass": tuned_metrics["hard_error_count"] == 0
            and tuned_metrics["json_parse_rate"] == 1
            and tuned_metrics["field_schema_rate"] == 1
            and tuned_metrics["required_field_completeness_rate"] == 1
            and tuned_metrics["numeric_preservation_rate"] == 1
            and tuned_metrics["numeric_hallucination_rate"] == 0
            and tuned_metrics["spanish_residual_rate"] == 0
            and tuned_metrics["english_residual_rate"] == 0
            and tuned_metrics["category_valid_rate"] in (None, 1),
            "manual_source_fidelity_review_required": True,
            "production_write": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
