"""Build the exact 200-row Stage-4 owner-review candidate set.

This is a selection/provenance step only.  It never promotes a row to Gold,
never writes the Master/dictionary, and never updates any training split.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"F:/ActionSKUTracker")
RUN_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
CLASSES = (
    "BRAND_FACT_LOSS", "NUMERIC_MISSING", "NUMERIC_HALLUCINATION",
    "TECH_TOKEN_LOSS", "PRODUCT_OBJECT_SEMANTIC_ERROR",
)
TARGET_PER_CLASS = 40


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def attach_class(audit: list[dict[str, str]], queue: list[dict[str, str]]) -> list[dict[str, str]]:
    by_sku = {row["sku"]: row["failure_class"] for row in queue}
    result = []
    for row in audit:
        copy = dict(row)
        copy["failure_class"] = by_sku.get(copy.get("sku", ""), "")
        result.append(copy)
    return result


def choose_to_target(
    base: list[dict[str, str]], refill: list[dict[str, str]], target: int = TARGET_PER_CLASS,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Return model-approved rows balanced to ``target`` per failure class.

    Queue order is deliberate: it makes the output reproducible and avoids
    choosing based on model wording or any hidden confidence score.
    """
    approved_base = [row for row in base if row.get("final_status") == "APPROVED_MODEL_REVIEWED"]
    counts = Counter(row.get("failure_class") for row in approved_base)
    deficits = {kind: max(0, target - counts[kind]) for kind in CLASSES}
    additions: list[dict[str, str]] = []
    seen = {row.get("sku") for row in approved_base}
    for row in refill:
        kind = row.get("failure_class")
        if row.get("final_status") != "APPROVED_MODEL_REVIEWED" or kind not in deficits:
            continue
        if deficits[kind] <= 0 or row.get("sku") in seen:
            continue
        additions.append(row)
        seen.add(row.get("sku"))
        deficits[kind] -= 1
    if any(deficits.values()):
        raise ValueError(f"INSUFFICIENT_APPROVED_REPLACEMENTS:{deficits}")
    result = approved_base + additions
    if len(result) != target * len(CLASSES):
        raise ValueError(f"UNEXPECTED_FINAL_ROW_COUNT:{len(result)}")
    if any(Counter(row["failure_class"] for row in result)[kind] != target for kind in CLASSES):
        raise ValueError("UNBALANCED_FINAL_SET")
    return result, {kind: sum(1 for row in additions if row["failure_class"] == kind) for kind in CLASSES}


def main() -> None:
    parser = argparse.ArgumentParser(description="Select exactly 200 model-approved Stage-4 review candidates")
    parser.add_argument("--output", default=str(RUN_DIR / "stage4_remediation200_final_model_approved_20260912.csv"))
    parser.add_argument("--manifest", default=str(RUN_DIR / "stage4_remediation200_final_model_approved_20260912.manifest.json"))
    args = parser.parse_args()
    inputs = {
        "base_audit": RUN_DIR / "qwen_incremental_remediation200_candidate_200_audited.csv",
        "base_queue": RUN_DIR / "stage4_targeted_remediation_review_queue_v4_200.csv",
        "refill_audit": RUN_DIR / "qwen_incremental_remediationrefill_candidate_100_audited.csv",
        "refill_queue": RUN_DIR / "stage4_targeted_remediation_review_queue_v5_replacements.csv",
    }
    base = attach_class(read_csv(inputs["base_audit"]), read_csv(inputs["base_queue"]))
    refill = attach_class(read_csv(inputs["refill_audit"]), read_csv(inputs["refill_queue"]))
    final_rows, additions = choose_to_target(base, refill)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(final_rows[0].keys()))
        writer.writeheader()
        writer.writerows(final_rows)
    manifest = {
        "report_version": "stage4-remediation-final-200-model-approved-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(final_rows),
        "by_failure_class": dict(Counter(row["failure_class"] for row in final_rows)),
        "refill_rows_selected": additions,
        "gold_rows": 0,
        "human_confirmation_required": True,
        "training_eligible_rows": 0,
        "production_writes": False,
        "inputs": {key: {"path": str(path), "sha256": sha(path)} for key, path in inputs.items()},
        "output": {"path": str(output), "sha256": sha(output)},
    }
    manifest_path = Path(args.manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(final_rows), "by_failure_class": manifest["by_failure_class"], "refill_rows_selected": additions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
