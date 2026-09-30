"""Partition the owner-reviewed replacement queue without production writes."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main(owner_path: Path, conflict_path: Path, output_dir: Path) -> dict:
    owner_rows = read_csv(owner_path)
    conflict_rows = read_csv(conflict_path)
    if len(owner_rows) != 25 or len(conflict_rows) != 11:
        raise ValueError(f"expected 25 owner rows and 11 conflict rows, got {len(owner_rows)} and {len(conflict_rows)}")
    decisions = {str(row.get("owner_decision", "")).strip().upper() for row in owner_rows}
    if decisions != {"APPROVE", "REJECT"}:
        raise ValueError(f"owner decisions must be exactly APPROVE/REJECT, got {sorted(decisions)}")
    approved = [row for row in owner_rows if row["owner_decision"].strip().upper() == "APPROVE"]
    rejected = [row for row in owner_rows if row["owner_decision"].strip().upper() == "REJECT"]
    if len(approved) != 10 or len(rejected) != 15:
        raise ValueError(f"expected 10 approved and 15 rejected, got {len(approved)} and {len(rejected)}")
    if any(str(row.get("owner_decision", "")).strip() for row in conflict_rows):
        raise ValueError("conflict rows must remain unapproved and isolated")
    pairs = [(row.get("sku", "").strip(), row.get("source_hash", "").strip()) for row in owner_rows + conflict_rows]
    if len(set(pairs)) != 36:
        raise ValueError("duplicate (sku, source_hash) in replacement outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = list(owner_rows[0])
    approved_path = output_dir / "stage5_replacement_owner_approved_10.csv"
    rejected_path = output_dir / "stage5_replacement_returned_for_repair_15.csv"
    isolated_path = output_dir / "stage5_replacement_conflict_isolated_11.csv"
    write_csv(approved_path, approved, columns)
    write_csv(rejected_path, rejected, columns)
    write_csv(isolated_path, conflict_rows, list(conflict_rows[0]))
    manifest = {
        "artifact": "STAGE5_REPLACEMENT_OWNER_REVIEW_FINAL",
        "owner_reviewed_count": 25,
        "approved_for_next_gold": 10,
        "returned_for_repair": 15,
        "source_conflict_isolated": 11,
        "total_reconciled": 36,
        "production_writes": 0,
        "training_runs": 0,
        "owner_review_status": "COMPLETE_FOR_25",
        "next_step": "GOLD_REVIEW_FOR_10_APPROVED_ROWS",
        "approved_csv": str(approved_path),
        "returned_csv": str(rejected_path),
        "isolated_csv": str(isolated_path),
    }
    (output_dir / "stage5_replacement_owner_review_final_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "stage5_replacement_owner_review_final.md").write_text(
        "# Stage 5 replacement owner review\n\n"
        "- 10 rows: approved for the next manual Gold process\n"
        "- 15 rows: returned for translation/data repair\n"
        "- 11 rows: source conflict isolated\n"
        "- Production writes: 0\n- Training runs: 0\n",
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--conflicts", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(main(Path(args.owner), Path(args.conflicts), Path(args.output_dir)), ensure_ascii=False, indent=2))
