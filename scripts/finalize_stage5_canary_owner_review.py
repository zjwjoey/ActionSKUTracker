"""Record explicit Owner confirmation for the reviewed Stage 5 canary.

This creates an immutable review decision/projection only.  It does not mutate
the original canary candidates and never writes Gold, Dictionary, Master,
SQLite, or production configuration.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.model_guard import validate_model_output


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--source-candidates", type=Path, required=True)
    parser.add_argument("--retry-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8"))
    retry_manifest = json.loads(args.retry_manifest.read_text(encoding="utf-8"))
    rows = review.get("rows", [])
    if len(rows) != 18 or not review.get("owner_approval_required"):
        raise ValueError("CANARY_REVIEW_CARDINALITY_OR_OWNER_FLAG_INVALID")
    candidates = {}
    for line in args.source_candidates.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("review_status") == "PENDING":
                candidates[(str(row["sku"]), str(row["source_field"]))] = row
    if set(candidates) != {(str(row["sku"]), str(row["field"])) for row in rows}:
        raise ValueError("CANARY_REVIEW_LINEAGE_SET_MISMATCH")

    retry_eval = retry_manifest.get("evaluation", {})
    retry_candidates_path = Path(retry_manifest["artifacts"]["candidates"]["path"])
    retry_row = None
    for line in retry_candidates_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            candidate = json.loads(line)
            if candidate.get("sku") == "2570444" and candidate.get("source_field") == "description":
                retry_row = candidate
                break
    if retry_row is None or retry_row.get("status") != "MODEL_FAILURE":
        raise ValueError("EXPECTED_RETRY_FAILURE_EVIDENCE_MISSING")

    decisions: list[dict[str, Any]] = []
    for item in rows:
        key = (str(item["sku"]), str(item["field"]))
        source = candidates[key]["source_spanish_value"]
        value = str(item["recommended_value"])
        check = validate_model_output({key[1]: source}, {key[1]: value}, expected_fields=[key[1]])
        disposition = item["ai_disposition"]
        if disposition == "ACCEPT_AS_IS":
            decision = "OWNER_APPROVED_AS_IS"
        elif disposition == "ACCEPT_WITH_GUARD_NOTE":
            decision = "OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION"
        elif disposition == "MINOR_EDIT":
            decision = "OWNER_APPROVED_MANUAL_EDIT"
        elif disposition == "RETRY_MODEL":
            decision = "OWNER_APPROVED_MANUAL_FALLBACK_AFTER_RETRY_FAILURE"
        else:
            raise ValueError(f"UNKNOWN_AI_DISPOSITION:{disposition}")
        decisions.append({
            "candidate_id": item.get("candidate_id"),
            "sku": key[0],
            "field": key[1],
            "source_hash": item.get("source_hash"),
            "original_status": item.get("status"),
            "ai_disposition": disposition,
            "owner_decision": decision,
            "owner_final_candidate": value,
            "owner_final_candidate_sha256": sha256_text(value),
            "guard_check": {"accepted": bool(check.accepted), "reasons": list(check.reasons)},
            "rationale": item.get("rationale", ""),
            "retry_evidence": (
                {"status": retry_row.get("status"), "manifest": str(args.retry_manifest.resolve()), "failure_count": retry_eval.get("failure_count")}
                if key == ("2570444", "description") else None
            ),
            "owner_confirmed": True,
            "production_write": False,
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat()
    manifest = {
        "manifest_version": "stage5-canary-owner-decision-v1",
        "generated_at": generated,
        "owner_confirmation": "CONFIRMED",
        "source_review": {"path": str(args.review.resolve()), "sha256": sha256_file(args.review)},
        "source_candidates": {"path": str(args.source_candidates.resolve()), "sha256": sha256_file(args.source_candidates)},
        "retry_manifest": {"path": str(args.retry_manifest.resolve()), "sha256": sha256_file(args.retry_manifest)},
        "reviewed_rows": len(decisions),
        "counts": {
            "OWNER_APPROVED_AS_IS": sum(x["owner_decision"] == "OWNER_APPROVED_AS_IS" for x in decisions),
            "OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION": sum(x["owner_decision"] == "OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION" for x in decisions),
            "OWNER_APPROVED_MANUAL_EDIT": sum(x["owner_decision"] == "OWNER_APPROVED_MANUAL_EDIT" for x in decisions),
            "OWNER_APPROVED_MANUAL_FALLBACK_AFTER_RETRY_FAILURE": sum(x["owner_decision"] == "OWNER_APPROVED_MANUAL_FALLBACK_AFTER_RETRY_FAILURE" for x in decisions),
        },
        "production_write": False,
        "gold_write": False,
        "dictionary_write": False,
        "master_write": False,
        "sqlite_write": False,
        "rows": decisions,
    }
    manifest_path = args.output_dir / "stage5_canary_owner_decision_18.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    projection_path = args.output_dir / "stage5_canary_owner_approved_18.jsonl"
    projection_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decisions), encoding="utf-8")
    csv_path = args.output_dir / "stage5_canary_owner_decision_18.csv"
    columns = ["candidate_id", "sku", "field", "original_status", "ai_disposition", "owner_decision", "owner_final_candidate", "guard_accepted", "guard_reasons", "owner_confirmed", "production_write"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in decisions:
            writer.writerow({
                "candidate_id": row["candidate_id"], "sku": row["sku"], "field": row["field"],
                "original_status": row["original_status"], "ai_disposition": row["ai_disposition"],
                "owner_decision": row["owner_decision"], "owner_final_candidate": row["owner_final_candidate"],
                "guard_accepted": row["guard_check"]["accepted"], "guard_reasons": ",".join(row["guard_check"]["reasons"]),
                "owner_confirmed": row["owner_confirmed"], "production_write": row["production_write"],
            })
    print(json.dumps({"manifest": str(manifest_path.resolve()), "projection": str(projection_path.resolve()), "csv": str(csv_path.resolve()), "rows": len(decisions)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
