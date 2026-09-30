"""Quarantine a known bad TM candidate and rebuild the isolated staging package.

This repair deliberately does not rewrite historical Gold or any production
dictionary.  The bad category pair is moved to the existing Owner review
queue until the corrected value has explicit review evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    args = parser.parse_args()
    queue_path = args.queue.resolve()
    rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    repaired = 0
    for row in rows:
        if row.get("field") == "cat1" and row.get("source_value_raw") == "Artículos deportivos" and row.get("target_value") == "食品饮料":
            flags = list(row.get("policy_flags") or [])
            if "CATEGORY_CANONICAL_MISMATCH" not in flags:
                flags.append("CATEGORY_CANONICAL_MISMATCH")
            row["policy_flags"] = sorted(set(flags))
            row["candidate_status"] = "REVIEW_REQUIRED"
            repaired += 1
    rows.sort(key=lambda item: (item.get("candidate_status", ""), item.get("field", ""), item.get("source_value_raw", ""), item.get("pair_hash", "")))
    queue_path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    csv_path = queue_path.with_suffix(".csv")
    columns = ["candidate_status", "field", "source_value_raw", "target_value", "source_hash", "pair_hash", "sku", "gold_status", "guard_status", "occurrence_count", "policy_flags"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: "; ".join(row.get(column, [])) if isinstance(row.get(column), list) else row.get(column, "") for column in columns})
    print(json.dumps({"repaired": repaired, "queue": str(queue_path)}, ensure_ascii=False))
    return 0 if repaired == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
