"""Run a read-only owner-corrected gate for the legacy Qwen adapter.

The correction manifest is immutable evidence from Owner review. This command
does not modify Master, Dictionary, SQLite, model weights, or production
configuration. It reruns only the old adapter on the frozen 146-row benchmark,
applies the explicit field-level corrections in memory, removes only the
explicitly approved false-positive guard findings, and emits a new audit.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import compare_qwen_baselines as compare


def _owner_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("sku", "")), str(item.get("field", "")), str(item.get("reason", "")))


def _clone_expected(row: dict[str, Any], overrides: dict[tuple[str, str], str]) -> dict[str, Any]:
    out = copy.deepcopy(row)
    sku = str(out.get("metadata", {}).get("sku", ""))
    if not any(key[0] == sku for key in overrides):
        return out
    for message in out.get("messages", []):
        if message.get("role") != "assistant":
            continue
        target = compare.parse_object(message.get("content", "")) or {}
        for (target_sku, field), value in overrides.items():
            if target_sku == sku:
                target[field] = value
        message["content"] = json.dumps(target, ensure_ascii=False, sort_keys=True)
    return out


def _apply_corrections(rows: list[dict[str, Any]], predictions: list[dict[str, Any] | None], manifest: dict[str, Any]) -> None:
    corrections = {(str(x["sku"]), str(x["field"])): x for x in manifest.get("corrections", [])}
    for row, prediction in zip(rows, predictions):
        if not prediction:
            continue
        sku = str(row.get("metadata", {}).get("sku", ""))
        for (target_sku, field), correction in corrections.items():
            if target_sku != sku or field not in prediction:
                continue
            if "value" in correction:
                prediction[field] = str(correction["value"])
            for replacement in correction.get("replacements", []):
                prediction[field] = str(prediction[field]).replace(
                    str(replacement["from"]), str(replacement["to"])
                )


def _aggregate_with_owner_review(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any] | None], name: str,
    allowed: dict[str, tuple[str, ...]], approved: set[tuple[str, str, str]],
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    approved_hits: list[dict[str, Any]] = []
    for row, prediction in zip(rows, predictions):
        sku = str(row.get("metadata", {}).get("sku", ""))
        result, row_issues = compare.score_prediction(row, prediction, allowed.get(sku, ()))
        kept: list[dict[str, Any]] = []
        for issue in row_issues:
            reasons = list(issue.get("reasons", ()))
            removed = []
            for reason in reasons:
                key = (sku, str(issue.get("field", "")), reason)
                if key in approved:
                    removed.append(reason)
                    approved_hits.append({"sku": sku, "field": issue.get("field"), "reason": reason})
                    if reason == "NUMERIC_HALLUCINATED":
                        result["numeric_hallucination"] = max(0, result.get("numeric_hallucination", 0) - 1)
                    if reason == "SPANISH_RESIDUAL":
                        result["spanish"] = max(0, result.get("spanish", 0) - 1)
                    if reason == "ENGLISH_RESIDUAL":
                        result["english"] = max(0, result.get("english", 0) - 1)
            remaining = [reason for reason in reasons if reason not in removed]
            if remaining:
                issue = dict(issue)
                issue["reasons"] = remaining
                kept.append(issue)
        scored.append(result)
        issues.extend(kept)
    total_rows = len(rows)
    parsed_rows = sum(x["parsed"] for x in scored)
    schema_rows = sum(x["schema"] for x in scored)
    total_fields = sum(x.get("total", 0) for x in scored)
    required_total = sum(x.get("required_total", 0) for x in scored)
    category_total = sum(x.get("category_total", 0) for x in scored)
    reasons = Counter(reason for issue in issues for reason in issue.get("reasons", ()))
    metrics = {
        "name": name,
        "rows": total_rows,
        "json_parse_rate": parsed_rows / total_rows if total_rows else 0,
        "field_schema_rate": schema_rows / total_rows if total_rows else 0,
        "evaluated_field_values": total_fields,
        "field_exact_match_rate": sum(x.get("exact", 0) for x in scored) / total_fields if total_fields else 0,
        "field_format_normalized_match_rate": sum(x.get("normalised_exact", 0) for x in scored) / total_fields if total_fields else 0,
        "field_nonempty_rate": sum(x.get("nonempty", 0) for x in scored) / total_fields if total_fields else 0,
        "required_field_completeness_rate": sum(x.get("required_nonempty", 0) for x in scored) / required_total if required_total else 1,
        "numeric_preservation_rate": sum(x.get("numeric_ok", 0) for x in scored) / total_fields if total_fields else 0,
        "numeric_hallucination_rate": sum(x.get("numeric_hallucination", 0) for x in scored) / total_fields if total_fields else 0,
        "spanish_residual_rate": sum(x.get("spanish", 0) for x in scored) / total_fields if total_fields else 0,
        "english_residual_rate": sum(x.get("english", 0) for x in scored) / total_fields if total_fields else 0,
        "category_valid_rate": sum(x.get("category_ok", 0) for x in scored) / category_total if category_total else None,
        "category_rows": category_total,
        "hard_error_count": sum(1 for issue in issues if set(issue.get("reasons", [])) & compare.SAFETY_REASONS),
        "issue_reason_counts": dict(sorted(reasons.items())),
        "issues": issues,
        "owner_approved_exception_hits": approved_hits,
    }
    metrics["automated_safety"] = compare.automated_safety_pass(metrics)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-input-length", type=int, default=768)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--strict-prompt", action="store_true")
    args = parser.parse_args()
    for label, path in (("model", args.model_path), ("adapter", args.adapter), ("test", args.test_file), ("manifest", args.manifest)):
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = compare.load_rows(args.test_file)
    expected_overrides = {
        (str(x["sku"]), str(x["field"])): str(x["expected_override"])
        for x in manifest.get("corrections", []) if x.get("expected_override")
    }
    eval_rows = [_clone_expected(row, expected_overrides) for row in rows]
    from action_tracker.config import load_settings
    from action_tracker.exporting.dictionary_join import load_dictionary_context
    cfg = load_settings(ROOT / "config" / "settings.yaml")
    context = load_dictionary_context(cfg)
    allowed = compare.allowed_brands_by_sku(eval_rows, context)
    predictions = compare.run_model(
        args.model_path, eval_rows, args.batch_size, args.max_input_length,
        args.max_new_tokens, args.adapter, strict_prompt=args.strict_prompt,
    )
    _apply_corrections(eval_rows, predictions, manifest)
    approved = {_owner_key(item) for item in manifest.get("accepted_exceptions", [])}
    metrics = _aggregate_with_owner_review(eval_rows, predictions, "old_gold_adapter_owner_corrected", allowed, approved)
    report = {
        "evaluation_policy_version": "stage6_owner_corrected_gate_v1_2026-09-14",
        "source_benchmark": str(args.test_file.resolve()),
        "adapter": str(args.adapter.resolve()),
        "correction_manifest": str(args.manifest.resolve()),
        "rows": len(eval_rows),
        "strict_prompt": args.strict_prompt,
        "owner_review": {
            "status": manifest.get("review_status"),
            "reviewed_rows": manifest.get("benchmark_rows_reviewed"),
            "approved_rows": manifest.get("approved_rows"),
            "correction_rows": manifest.get("correction_rows"),
            "approved_exception_count": len(approved),
        },
        "metrics": metrics,
        "production_write": False,
        "production_approval": "PENDING_FINAL_OWNER_APPROVAL" if not metrics["automated_safety"] else "PENDING_OWNER_RELEASE_AUTHORIZATION",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "rows": len(eval_rows), "automated_safety": metrics["automated_safety"], "remaining_issues": len(metrics["issues"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
