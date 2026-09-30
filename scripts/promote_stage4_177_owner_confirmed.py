"""Record explicit owner confirmation for the 177 AI-accepted rows.

Promotion is isolated to a new training artifact.  It never edits Master,
the dictionary, the frozen 485 test, or the existing adapters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_skus(root: Path) -> dict[str, set[str]]:
    """Collect only established train/validation/test artifacts, never candidates."""
    result: dict[str, set[str]] = {}
    for path in root.rglob("*.jsonl"):
        if not (path.name.endswith(("_train.jsonl", "_validation.jsonl", "_test.jsonl")) or path.name == "stage4_test_only_485.jsonl"):
            continue
        values: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sku = str(row.get("sku") or (row.get("metadata") or {}).get("sku") or "").strip()
            if sku:
                values.add(sku)
        result[str(path)] = values
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--historical-root", default="runtime/training/qwen3_8b")
    parser.add_argument("--reviewer", default="项目所有者（本次对话明确确认）")
    args = parser.parse_args()
    source = Path(args.input)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 177 or len({row.get("metadata", {}).get("sku") for row in rows}) != 177:
        raise ValueError("EXPECTED_177_UNIQUE_ROWS")
    candidate_skus = {str(row["metadata"]["sku"]) for row in rows}
    overlap_by_file = {path: sorted(candidate_skus & values) for path, values in historical_skus(Path(args.historical_root)).items() if candidate_skus & values}
    leakage_blocked = bool(overlap_by_file)
    confirmed_at = datetime.now(timezone.utc).isoformat()
    output_rows = []
    for row in rows:
        metadata = dict(row["metadata"])
        metadata.update({
            "gold_status": "HUMAN_CONFIRMED_REMEDIATION_GOLD_LEAKAGE_BLOCKED" if leakage_blocked else "HUMAN_CONFIRMED_REMEDIATION_GOLD",
            "owner_decision": "ACCEPT_AS_GOLD",
            "owner_reviewer": args.reviewer,
            "owner_confirmed_at": confirmed_at,
            "training_eligible": not leakage_blocked,
            "training_note": "Explicit owner confirmation in thread; blocked until disjoint source data is available." if leakage_blocked else "Explicit owner confirmation in thread; isolated targeted retrain only after split/leakage validation.",
        })
        output_rows.append({"messages": row["messages"], "metadata": metadata})
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output_rows), encoding="utf-8")
    manifest = {
        "report_version": "stage4-remediation177-human-confirmed-v1",
        "created_at": confirmed_at,
        "owner_confirmation": {"confirmed": True, "reviewer": args.reviewer, "decision": "ACCEPT_AS_GOLD", "rows": 177},
        "input": {"path": str(source), "sha256": sha(source)},
        "rows": 177, "unique_skus": 177, "gold_rows": 177, "training_eligible_rows": 0 if leakage_blocked else 177,
        "leakage_status": "BLOCKED" if leakage_blocked else "PASS",
        "historical_train_validation_test_overlap": len(set().union(*(set(values) for values in historical_skus(Path(args.historical_root)).values())) & candidate_skus),
        "overlap_by_file": overlap_by_file,
        "production_writes": False,
        "output": {"path": str(destination), "sha256": sha(destination)},
    }
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": 177, "gold_rows": 177, "training_eligible_rows": 0 if leakage_blocked else 177, "leakage_status": "BLOCKED" if leakage_blocked else "PASS", "production_writes": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
