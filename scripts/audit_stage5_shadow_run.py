"""Create a read-only audit package for a Stage 5 offline shadow run.

This command never mutates Master, Dictionary, SQLite, Gold, or production
configuration.  It only reads the shadow artifacts and writes an audit JSON and
Markdown report beside them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    manifest_path = run_dir / "stage5_fresh_current_20260914_manifest.json"
    evaluation_path = run_dir / "stage5_batch_evaluation.json"
    candidates_path = run_dir / "stage5_candidates.jsonl"
    queue_path = run_dir / "stage5_manual_review_queue.jsonl"
    failure_path = run_dir / "stage5_failure_report.csv"
    required = [manifest_path, evaluation_path, candidates_path, queue_path, failure_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing shadow artifacts: " + ", ".join(missing))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    candidates = read_jsonl(candidates_path)
    queue = read_jsonl(queue_path)
    failures = read_csv(failure_path)

    candidate_ids = [str(row.get("candidate_id", "")) for row in candidates]
    queue_ids = [str(row.get("candidate_id", "")) for row in queue]
    sku_hashes: dict[str, set[str]] = defaultdict(set)
    source_hash_by_sku: dict[str, set[str]] = defaultdict(set)
    status_counts = Counter(str(row.get("status", "")) for row in candidates)
    field_counts = Counter(str(row.get("source_field", "")) for row in candidates)
    for row in candidates:
        sku = str(row.get("sku", ""))
        source_hash = str(row.get("source_hash", ""))
        sku_hashes[sku].add(source_hash)
        source_hash_by_sku[sku].add(source_hash)

    artifact_hashes: dict[str, dict[str, Any]] = {}
    for name, info in manifest.get("artifacts", {}).items():
        path = Path(info["path"])
        exists = path.exists()
        actual = sha256_file(path) if exists else None
        artifact_hashes[name] = {
            "path": str(path),
            "exists": exists,
            "manifest_sha256": info.get("sha256"),
            "actual_sha256": actual,
            "match": bool(exists and actual == info.get("sha256")),
        }

    failure_ids = {str(row.get("candidate_id", "")) for row in queue if row.get("status") == "RESOLVER_FAILURE" or row.get("guard_findings")}
    queue_status = Counter(str(row.get("review_status", "")) for row in queue)
    failure_types = Counter(str(row.get("failure_type", "")) for row in failures)
    failure_skus = sorted({str(row.get("sku", "")) for row in failures})
    production_writes = manifest.get("production_writes", {})
    writes_disabled = all(value is False for value in production_writes.values())

    checks = {
        "manifest_artifacts_hash_match": all(item["match"] for item in artifact_hashes.values()),
        "candidate_id_unique": len(candidate_ids) == len(set(candidate_ids)),
        "queue_candidate_id_unique": len(queue_ids) == len(set(queue_ids)),
        "candidate_source_hash_present": all(bool(row.get("source_hash")) for row in candidates),
        "one_source_hash_per_sku": all(len(hashes) == 1 for hashes in source_hash_by_sku.values()),
        "evaluation_candidate_count_match": evaluation.get("field_count") == len(candidates),
        "evaluation_failure_count_match": evaluation.get("failure_count") == len(failures),
        "failure_rows_are_review_required": all(row.get("action") == "HUMAN_REVIEW_REQUIRED" for row in failures),
        "production_writes_disabled": writes_disabled,
        "training_writes_disabled": manifest.get("frozen_identity", {}).get("training_authorized") is False,
    }
    review_needed = bool(
        failures
        or evaluation.get("guard_reject", 0)
        or evaluation.get("human_pending", 0)
        or evaluation.get("unresolved", 0)
    )
    verdict = "REVIEW_REQUIRED_NO_WRITE" if review_needed and all(checks.values()) else "AUDIT_FAIL_NO_WRITE"

    report: dict[str, Any] = {
        "audit_version": "stage5-shadow-audit-v1",
        "run_dir": str(run_dir),
        "batch_id": manifest.get("batch_id"),
        "run_id": next(iter({str(row.get("run_id", "")) for row in candidates}), ""),
        "source_input": manifest.get("input"),
        "counts": {
            "eligible_sku_count": evaluation.get("eligible_sku_count"),
            "unique_sku_count": len(source_hash_by_sku),
            "candidate_field_count": len(candidates),
            "guard_pass": evaluation.get("guard_pass"),
            "guard_reject": evaluation.get("guard_reject"),
            "failure_count": len(failures),
            "manual_review_queue_count": len(queue),
            "manual_review_pending": queue_status.get("PENDING", 0),
        },
        "candidate_status_counts": dict(status_counts),
        "candidate_field_counts": dict(field_counts),
        "failure_type_counts": dict(failure_types),
        "failure_skus": failure_skus,
        "failure_rows": failures,
        "queue_status_counts": dict(queue_status),
        "source_hashes_per_sku": {sku: sorted(hashes) for sku, hashes in sorted(source_hash_by_sku.items())},
        "evaluation": evaluation,
        "checks": checks,
        "artifact_hashes": artifact_hashes,
        "production_writes": production_writes,
        "verdict": verdict,
        "owner_action": "Review the 47 pending fields and the 5 failure rows. Do not approve training or production write from this shadow run.",
    }

    output_json = (args.output_json or run_dir / "stage5_shadow_audit_20260914.json").resolve()
    output_md = (args.output_md or run_dir / "stage5_shadow_audit_20260914.md").resolve()
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Stage 5 Shadow Audit — 2026-09-14",
        "",
        f"- Batch: `{report['batch_id']}`",
        f"- Verdict: **{verdict}**",
        "- Scope: read-only audit; no training, dictionary, Master, SQLite, or production writes.",
        "",
        "## Counts",
        "",
        f"- Eligible SKUs: {report['counts']['eligible_sku_count']}",
        f"- Unique SKUs: {report['counts']['unique_sku_count']}",
        f"- Candidate fields: {report['counts']['candidate_field_count']}",
        f"- Guard pass / reject: {report['counts']['guard_pass']} / {report['counts']['guard_reject']}",
        f"- Failure rows: {report['counts']['failure_count']}",
        f"- Manual review queue: {report['counts']['manual_review_queue_count']} (pending {report['counts']['manual_review_pending']})",
        "",
        "## Failure types",
        "",
    ]
    for key, value in sorted(failure_types.items()):
        lines.append(f"- `{key}`: {value}")
    lines += ["", "## Integrity checks", ""]
    for key, value in checks.items():
        lines.append(f"- `{key}`: {'PASS' if value else 'FAIL'}")
    lines += ["", "## Owner decision", "", report["owner_action"], ""]
    output_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(output_json), "markdown": str(output_md), "verdict": verdict}, ensure_ascii=False))
    return 0 if verdict == "REVIEW_REQUIRED_NO_WRITE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
