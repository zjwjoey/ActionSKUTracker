"""Export the JSONL model review queue to a reviewer-friendly CSV."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def export_csv(input_file: Path, output_file: Path) -> dict[str, int | str]:
    rows = [json.loads(line) for line in input_file.open(encoding="utf-8") if line.strip()]
    columns = ["review_id", "sku", "status"]
    for field in FIELDS:
        columns.extend([f"{field}_es", f"{field}_model_zh", f"{field}_reference_zh"])
    columns.extend([
        "automated_issues", "guard_reasons", "taxonomy", "missing_numbers", "extra_numbers",
        "training_promotion", "human_decision", "final_taxonomy", "review_notes",
    ])
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            field_only = str(row.get("field", "") or "").strip()
            source = row.get("source_es") or {}
            prediction = row.get("model_prediction") or {}
            reference = row.get("reference_zh") or {}
            # Field-conditioned hard-example queues deliberately store scalar
            # values.  Present them in the same reviewer CSV contract as a
            # normal six-field queue without fabricating any other fields.
            if isinstance(source, str):
                source = {field_only: source} if field_only in FIELDS else {}
            if isinstance(prediction, str):
                prediction = {field_only: prediction} if field_only in FIELDS else {}
            if isinstance(reference, str):
                reference = {field_only: reference} if field_only in FIELDS else {}
            record = {
                "review_id": row.get("review_id", ""),
                "sku": row.get("sku", ""),
                "status": row.get("status", "PENDING_MANUAL_REVIEW"),
                "automated_issues": json.dumps(row.get("automated_issues", []), ensure_ascii=False),
                "guard_reasons": json.dumps(row.get("guard_reasons", []), ensure_ascii=False),
                "taxonomy": json.dumps(row.get("taxonomy", []), ensure_ascii=False),
                "missing_numbers": json.dumps(row.get("missing_numbers", []), ensure_ascii=False),
                "extra_numbers": json.dumps(row.get("extra_numbers", []), ensure_ascii=False),
                "training_promotion": row.get("training_promotion", ""),
                "human_decision": "",
                "final_taxonomy": "",
                "review_notes": "",
            }
            for field in FIELDS:
                record[f"{field}_es"] = str(source.get(field, "") or "")
                record[f"{field}_model_zh"] = str(prediction.get(field, "") or "")
                record[f"{field}_reference_zh"] = str(reference.get(field, "") or "")
            writer.writerow(record)
    return {"rows": len(rows), "columns": len(columns), "status": "PENDING_MANUAL_REVIEW", "output": str(output_file.resolve())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_csv(args.input, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
