"""Compare an isolated Stage 5 shadow batch with human Gold without applying it."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def message_object(row: dict, role: str) -> dict:
    values = [message["content"] for message in row.get("messages", []) if message.get("role") == role]
    if len(values) != 1:
        raise ValueError(f"EXPECTED_ONE_{role.upper()}_MESSAGE:{row.get('metadata', {}).get('sku')}")
    return json.loads(values[0])


def loose(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("disjoint", "all"), default="disjoint")
    args = parser.parse_args()

    gold_rows = load_jsonl(args.gold)
    gold = {}
    for row in gold_rows:
        metadata = row.get("metadata", {})
        if args.scope == "all" or metadata.get("leakage_status") == "DISJOINT_ELIGIBLE":
            gold[metadata["sku"]] = message_object(row, "assistant")
    candidates = load_jsonl(args.candidates)
    mismatches = []
    exact = 0
    loose_exact = 0
    by_field = Counter()
    for row in candidates:
        sku = str(row.get("sku") or "")
        field = str(row.get("source_field") or "")
        expected = gold[sku][field]
        actual = row.get("final_candidate")
        same = actual == expected
        same_loose = loose(actual) == loose(expected)
        if same:
            exact += 1
        if same_loose:
            loose_exact += 1
        if not same:
            by_field[field] += 1
            mismatches.append({
                "sku": sku, "field": field, "status": row.get("status"),
                "expected_gold": expected, "shadow_candidate": actual,
                "exact_match": same, "whitespace_normalized_match": same_loose,
                "model_invoked": row.get("model_invoked", False),
                "guard_accepted": (row.get("guard_result") or {}).get("accepted"),
            })
    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    report = {
        "artifact_type": "STAGE5_SHADOW_GOLD_COMPARISON",
        "status": "REVIEW_REQUIRED_NO_APPLY",
        "gold_rows": len(gold), "candidate_fields": len(candidates),
        "exact_match_count": exact, "whitespace_normalized_match_count": loose_exact,
        "mismatch_count": len(mismatches), "mismatch_by_field": dict(sorted(by_field.items())),
        "pipeline_guard_reject_count": evaluation.get("guard_reject"),
        "fact_hallucination_escaped": evaluation.get("fact_hallucination_escaped"),
        "source_fact_loss_escaped": evaluation.get("source_fact_loss_escaped"),
        "mismatches": mismatches,
        "production_apply": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "gold_rows", "candidate_fields", "exact_match_count", "whitespace_normalized_match_count", "mismatch_count", "mismatch_by_field", "production_apply")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
