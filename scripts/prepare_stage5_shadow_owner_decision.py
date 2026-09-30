"""Prepare an offline Stage 5 Owner-decision package from the reviewed queue.

The package is evidence only. It never writes Gold, Dictionary, Master,
SQLite, production configuration, or starts a training run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.translation.model_guard import validate_model_output


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-csv", type=Path, required=True)
    parser.add_argument("--queue-jsonl", type=Path, required=True)
    parser.add_argument("--candidates-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    reviewed = read_csv(args.queue_csv)
    shadow = {str(row.get("candidate_id")): row for row in read_jsonl(args.queue_jsonl)}
    candidates = {str(row.get("candidate_id")): row for row in read_jsonl(args.candidates_jsonl)}
    if len(reviewed) != 47 or len({row.get("review_id") for row in reviewed}) != 47:
        raise SystemExit("REVIEW_QUEUE_CARDINALITY_INVALID")

    accepted = {"ACCEPT_AS_IS", "MINOR_EDIT"}
    isolated = {"RETRY_MODEL"}
    decision_rows: list[dict[str, Any]] = []
    issues: list[str] = []
    counts: Counter[str] = Counter()
    guard_counts: Counter[str] = Counter()
    for row in reviewed:
        candidate_id = str(row.get("candidate_id", "")).strip()
        candidate = shadow.get(candidate_id)
        source_candidate = candidates.get(candidate_id)
        disposition = str(row.get("owner_decision_draft", "")).strip()
        sku = str(row.get("sku", "")).strip()
        field = str(row.get("field", "")).strip()
        final_value = str(row.get("owner_final_value_draft", "")).strip()
        status = "OWNER_ACCEPTED" if disposition in accepted else "OWNER_ISOLATED_RETRY" if disposition in isolated else "INVALID"
        counts[status] += 1
        if candidate is None or source_candidate is None:
            issues.append(f"CANDIDATE_MISSING:{sku}:{field}")
            source_value = ""
        else:
            # The manual-review queue is a flattened review artifact and uses
            # ``field``/``spanish_source``; the candidate artifact uses
            # ``source_field``/``source_spanish_value``.  Accept either shape
            # while still requiring exact SKU, field, and source lineage.
            source_value = str(candidate.get("spanish_source", candidate.get("source_spanish_value", "")))
            candidate_field = str(candidate.get("field", candidate.get("source_field", "")))
            if str(candidate.get("sku", "")) != sku or candidate_field != field:
                issues.append(f"CANDIDATE_LINEAGE_MISMATCH:{sku}:{field}")
            if source_value != str(row.get("spanish_source", "")):
                issues.append(f"SOURCE_TEXT_MISMATCH:{sku}:{field}")
        guard_status = "ISOLATED_RETRY" if status == "OWNER_ISOLATED_RETRY" else "NOT_RUN"
        guard_reasons: list[str] = []
        if status == "OWNER_ACCEPTED":
            result = validate_model_output({field: source_value}, {field: final_value}, expected_fields=[field])
            guard_status = "PASS" if result.accepted else "OWNER_GUARD_EXCEPTION"
            guard_reasons = list(result.reasons)
            guard_counts[guard_status] += 1
        elif status == "OWNER_ISOLATED_RETRY":
            guard_counts[guard_status] += 1
        else:
            issues.append(f"INVALID_OWNER_DISPOSITION:{sku}:{field}:{disposition}")
        decision_rows.append({
            "review_id": row.get("review_id"),
            "candidate_id": candidate_id,
            "sku": sku,
            "field": field,
            "source_spanish_value": source_value,
            "reviewed_value": final_value,
            "owner_decision": disposition,
            "decision_status": status,
            "guard_status": guard_status,
            "guard_reasons": guard_reasons,
            "source_hash": source_candidate.get("source_hash") if source_candidate else None,
            "batch_id": source_candidate.get("batch_id") if source_candidate else None,
            "production_write": False,
            "training_run": False,
        })

    approved = [row for row in decision_rows if row["decision_status"] == "OWNER_ACCEPTED"]
    isolated_rows = [row for row in decision_rows if row["decision_status"] != "OWNER_ACCEPTED"]
    guard_exceptions = [row for row in approved if row["guard_status"] != "PASS"]
    report = {
        "artifact_type": "STAGE5_SHADOW_OWNER_DECISION_PACKAGE",
        "artifact_version": "V1",
        "reviewed_queue": {"path": str(args.queue_csv.resolve()), "sha256": sha256_file(args.queue_csv)},
        "shadow_queue": {"path": str(args.queue_jsonl.resolve()), "sha256": sha256_file(args.queue_jsonl)},
        "counts": {
            "review_rows": len(decision_rows),
            "owner_accepted": len(approved),
            "owner_isolated_retry": len(isolated_rows),
            "owner_guard_pass": sum(row["guard_status"] == "PASS" for row in approved),
            "owner_guard_exceptions": len(guard_exceptions),
        },
        "decision_counts": dict(sorted(counts.items())),
        "guard_counts": dict(sorted(guard_counts.items())),
        "issues": issues,
        "guard_exception_pairs": [[row["sku"], row["field"], row["guard_reasons"]] for row in guard_exceptions],
        "production_writes": {"master": False, "dictionary": False, "sqlite": False},
        "training_runs": 0,
        "status": "OWNER_DECISION_PACKAGED_NO_WRITE" if not issues else "OWNER_DECISION_BLOCKED_NO_WRITE",
        "next_action": "Review guard exceptions explicitly before any Gold ingestion or training gate.",
    }
    (args.output_dir / "stage5_shadow_owner_decisions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decision_rows), encoding="utf-8"
    )
    (args.output_dir / "stage5_shadow_owner_approved.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in approved), encoding="utf-8"
    )
    (args.output_dir / "stage5_shadow_owner_isolated_retry.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in isolated_rows), encoding="utf-8"
    )
    (args.output_dir / "stage5_shadow_owner_decision_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md = [
        "# Stage 5 Shadow Owner Decision Package — 2026-09-14",
        "",
        "- Scope: offline evidence only; no Gold, Dictionary, Master, SQLite, production, or training writes.",
        f"- Reviewed rows: {len(decision_rows)}",
        f"- Owner accepted: {len(approved)}",
        f"- Isolated retry: {len(isolated_rows)}",
        f"- Guard pass: {sum(row['guard_status'] == 'PASS' for row in approved)}",
        f"- Guard exceptions: {len(guard_exceptions)}",
        "",
        "## Guard exceptions requiring explicit review",
        "",
    ]
    if guard_exceptions:
        for row in guard_exceptions:
            md.append(f"- `{row['sku']} / {row['field']}`: {', '.join(row['guard_reasons'])}")
    else:
        md.append("- None")
    md += ["", "## Status", "", report["status"], ""]
    (args.output_dir / "README.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "OWNER_DECISION_PACKAGED_NO_WRITE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
