"""Build deterministic, source-only Stage 5 queue and engineering evidence.

The five historical batch JSONLs are read, never modified.  The signed owner
review is verified separately.  No model, Master, SQLite or dictionary writer
is imported by this command.
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.stage5.owner_review import (
    certify_owner_review, load_replayed_candidates, load_signed_rows,
)
from action_tracker.stage5.pipeline import canonical_json, sha256_file
from action_tracker.stage5.source_candidate_v2 import SOURCE_FIELDS


QUEUE = ROOT / "runtime/training/qwen3_8b/20260913/stage5_new_source_versions_999"
REPLAY = ROOT / "runtime/stage5/20260913/engineering_replay_v3"
SIGNED_REPORT = ROOT / "runtime/training/qwen3_8b/20260911/stage4_stage5_owner_signed_recheck_20260912.json"
RUNS = (
    ("20260907", "2026-09-07_025601"),
    ("20260908", "2026-09-08_035740"),
    ("20260909", "2026-09-09_032720"),
    ("20260910", "2026-09-10_030315"),
    ("20260912", "2026-09-12_024403"),
)


def write_new(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"IMMUTABLE_CLOSURE_ARTIFACT_CHANGED:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def main() -> int:
    source_manifest_path = QUEUE / "stage5_source_versions_v2_999_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    audit_path = QUEUE / "stage5_source_versions_v2_999_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit["qc_result"] != "PASS" or audit["selected_record_count"] != 999:
        raise ValueError("SOURCE_QUEUE_AUDIT_NOT_PASS")
    if audit["manifest_sha256"] != sha256_file(source_manifest_path):
        raise ValueError("SOURCE_QUEUE_MANIFEST_CHANGED_AFTER_AUDIT")

    batches: dict[str, dict[str, object]] = {}
    rows: list[dict] = []
    for date, run_id in RUNS:
        entry = source_manifest["artifacts"]["batches"][run_id]
        source_path = Path(entry["path"])
        if sha256_file(source_path) != entry["sha256"]:
            raise ValueError(f"BATCH_HASH_MISMATCH:{run_id}")
        content = source_path.read_bytes()
        alias = QUEUE / f"batch_{date}_source_only.jsonl"
        write_new(alias, content)
        batch_rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line]
        if len(batch_rows) != entry["count"]:
            raise ValueError(f"BATCH_COUNT_MISMATCH:{run_id}")
        rows.extend(batch_rows)
        batches[date] = {
            "source_run_id": run_id, "file": alias.name, "count": len(batch_rows),
            "sha256": sha256_file(alias),
        }
    if len(rows) != 999 or any(row["metadata"]["source_run_id"].startswith("2026-09-11") for row in rows):
        raise ValueError("SOURCE_QUEUE_COUNT_OR_0911_EXCLUSION_FAILED")
    aggregate = QUEUE / "stage5_source_queue_999.jsonl"
    write_new(aggregate, b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows))
    csv_path = QUEUE / "stage5_source_queue_999.csv"
    columns = (
        "sku", "source_run_id", "source_hash", "source_hash_contract_version",
        "source_version_status", "candidate_status", "source_consistency_status",
        "source_consistency_flags", *SOURCE_FIELDS,
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        metadata = row["metadata"]
        source = json.loads(row["messages"][0]["content"])
        writer.writerow({
            **{key: metadata.get(key, "") for key in columns if key not in SOURCE_FIELDS},
            "source_consistency_flags": "|".join(metadata["source_consistency_flags"]),
            **source,
        })
    write_new(csv_path, ("\ufeff" + stream.getvalue()).encode("utf-8"))

    signed = json.loads(SIGNED_REPORT.read_text(encoding="utf-8"))
    owner_path = Path(signed["owner_signed_input"]["path"])
    owner_rows = load_signed_rows(owner_path, signed["owner_signed_input"]["sha256"])
    candidates, replay_manifests = load_replayed_candidates(REPLAY)
    review = certify_owner_review(owner_rows, candidates)
    if review["issues"] or len(review["approved"]) != 91 or len(review["isolated"]) != 12:
        raise ValueError(f"OWNER_REVIEW_CERTIFICATION_FAILED:{review['issues'][:5]}")
    for date, run_id in RUNS:
        if not audit["run_evidence"][run_id]["eligible"]:
            raise ValueError(f"RUN_PROVENANCE_INVALID:{run_id}")

    policy = {
        "source_hash_contract_version": "SOURCE_HASH_V1",
        "source_hash_algorithm": "localization_source_hash_v1",
        "source_hash_note": "Existing six-Spanish-field V1 hash, without SKU; (SKU, hash) is the identity. Adding SKU requires SOURCE_HASH_V2.",
        "historical_split_cutoff_exclusive": "20260913",
        "later_training_overlap_is_not_prior_leakage": audit["later_training_pair_overlap_count"],
    }
    sidecars = {
        "source_consistency_report.json": {
            "records_checked": 999, "counts": audit["source_consistency_counts"],
            "source_conflicts_require_review": audit["source_consistency_counts"].get("SOURCE_CONFLICT_REVIEW", 0),
        },
        "historical_overlap_audit.json": {
            "as_of_selection_exact_pair_overlap": audit["historical_exact_pair_overlap_count"],
            "later_training_pair_overlap": audit["later_training_pair_overlap_count"],
            "historical_split_cutoff_exclusive": "20260913",
        },
        "source_hash_audit.json": {
            "contract": policy, "recomputed": audit["source_hash_recompute_match_count"],
            "expected": 999,
        },
        "run_provenance_audit.json": {
            "runs": audit["run_evidence"], "valid_run_count": len(RUNS),
            "20260911_included": 0,
        },
    }
    for name, value in sidecars.items():
        write_new(QUEUE / name, json_bytes(value))
    current_manifest = QUEUE / "manifest.json"
    created_at = (
        json.loads(current_manifest.read_text(encoding="utf-8"))["generated_at"]
        if current_manifest.exists() else datetime.now(timezone.utc).isoformat()
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    manifest = {
        "contract_id": "STAGE5_SOURCE_CANDIDATE_V2", "generated_at": created_at,
        "git_commit_at_generation": commit, "record_count": 999,
        "source_manifest_sha256": sha256_file(source_manifest_path),
        "aggregate": {"file": aggregate.name, "sha256": sha256_file(aggregate)},
        "csv": {"file": csv_path.name, "sha256": sha256_file(csv_path)},
        "batches": batches, "source_hash_contract": policy,
        "production_writes": 0, "queue_generation_training_runs": 0,
    }
    write_new(current_manifest, json_bytes(manifest))
    md = (
        "# Stage 5 source queue audit\n\n"
        f"- Source candidates: 999; unique pairs: {audit['unique_pair_count']}\n"
        f"- Run distribution: {source_manifest['batch_counts']}\n"
        "- 2026-09-11 included: 0\n"
        f"- Snapshot/hash matches: {audit['source_snapshot_exact_match_count']}/999; {audit['source_hash_recompute_match_count']}/999\n"
        f"- Prior split pair overlap: {audit['historical_exact_pair_overlap_count']} (cutoff < 2026-09-13)\n"
        f"- Later Stage 5 training overlap: {audit['later_training_pair_overlap_count']} (disclosed, not prior leakage)\n"
        f"- Source consistency: {audit['source_consistency_counts']}\n"
        f"- Signed review: {review['counts']}; 91 Guard-valid accepted, 12 isolated\n"
        f"- Recorded-output replay manifests: {replay_manifests}\n"
        "- This queue is source-only evidence, not a Gold label or production Apply.\n"
    )
    write_new(QUEUE / "stage5_source_queue_audit.md", md.encode("utf-8"))
    result = {
        "source_queue": 999, "batch_counts": source_manifest["batch_counts"],
        "prior_overlap": 0, "later_training_overlap": audit["later_training_pair_overlap_count"],
        "owner_counts": review["counts"], "owner_guard_valid": len(review["approved"]),
        "owner_isolated": len(review["isolated"]), "sidecar_manifest": str(current_manifest),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
