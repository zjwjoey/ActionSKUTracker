"""Run the frozen Stage-4 four-way benchmark on one held-out test set.

The benchmark compares raw Qwen, the previous gold adapter, the current
combined-data adapter, and the offline dictionary resolver.  It is read-only:
no Master, SQLite, dictionary, or training files are changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import compare_qwen_baselines as compare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=ROOT / "runtime/models/Qwen3-8B")
    parser.add_argument("--old-adapter", type=Path, required=True)
    parser.add_argument("--current-adapter", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-input-length", type=int, default=768)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strict-prompt", action="store_true")
    args = parser.parse_args()

    for label, path in (
        ("model", args.model_path),
        ("old adapter", args.old_adapter),
        ("current adapter", args.current_adapter),
        ("test file", args.test_file),
    ):
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    rows = compare.load_rows(args.test_file)
    if args.limit is not None:
        rows = rows[: args.limit]
    if not rows:
        raise ValueError("four-way benchmark requires at least one test row")

    from action_tracker.config import load_settings
    from action_tracker.exporting.dictionary_join import load_dictionary_context

    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    allowed = compare.allowed_brands_by_sku(rows, context)

    reports = []
    raw = compare.run_model(
        args.model_path, rows, args.batch_size, args.max_input_length,
        args.max_new_tokens, strict_prompt=args.strict_prompt,
    )
    reports.append(compare.aggregate(rows, raw, "raw_qwen", allowed))
    old = compare.run_model(
        args.model_path, rows, args.batch_size, args.max_input_length,
        args.max_new_tokens, args.old_adapter, strict_prompt=args.strict_prompt,
    )
    reports.append(compare.aggregate(rows, old, "old_gold_adapter", allowed))
    current = compare.run_model(
        args.model_path, rows, args.batch_size, args.max_input_length,
        args.max_new_tokens, args.current_adapter, strict_prompt=args.strict_prompt,
    )
    reports.append(compare.aggregate(rows, current, "current_combined_adapter", allowed))
    resolved = compare.resolver_predictions(rows, context)
    reports.append(compare.aggregate(rows, resolved, "dictionary_resolver", allowed))

    report = {
        "evaluation_policy_version": "stage4_safety_first_v3_2026-09-11",
        "test_file": str(args.test_file.resolve()),
        "rows": len(rows),
        "strict_prompt": args.strict_prompt,
        "models": reports,
        "stage4_gate": {
            "all_four_systems_compared": True,
            "automated_safety": {
                report["name"]: compare.automated_safety_pass(report)
                for report in reports
            },
            "manual_source_fidelity_review": {
                "required": True,
                "status": "NOT_RECORDED_BY_EVALUATOR",
            },
            "stage5_eligible": False,
            "stage5_blocker": "Manual source-fidelity review is external to this benchmark.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "rows": len(rows),
        "systems": [item["name"] for item in reports],
        "safety": report["stage4_gate"]["automated_safety"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
