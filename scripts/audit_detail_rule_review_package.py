"""Audit a detail-rule review queue/template package without approving rows."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.detail_rule_candidates import build_detail_rule_candidates  # noqa: E402


# Detail source cells can be substantially larger than csv's 128 KiB default.
# Keep this reader aligned with the queue/candidate/template builders.
csv.field_size_limit(10 * 1024 * 1024)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def audit_package(
    queue_path: Path, queue_manifest_path: Path,
    template_path: Path, template_manifest_path: Path,
) -> dict[str, object]:
    queue = _read_csv(queue_path)
    template = _read_csv(template_path)
    queue_manifest = json.loads(queue_manifest_path.read_text(encoding="utf-8"))
    template_manifest = json.loads(template_manifest_path.read_text(encoding="utf-8"))
    queue_ids = [str(row.get("review_id") or "") for row in queue]
    template_ids = [str(row.get("review_id") or "") for row in template]
    queue_counts = Counter(queue_ids)
    template_counts = Counter(template_ids)
    candidates = build_detail_rule_candidates(
        queue,
        source_sha256=str(queue_manifest.get("source_sha256") or ""),
        target_sha256=str(queue_manifest.get("target_sha256") or ""),
        rules_sha256=str(queue_manifest.get("detail_rules_sha256") or ""),
    )
    candidate_ids = [str(row.get("candidate_id") or "") for row in candidates]
    # V2 templates are keyed by aggregate candidate_id.  Preserve support for
    # legacy occurrence-level templates while selecting the expected identity
    # scope from the actual template instead of comparing unlike IDs.
    template_uses_candidates = any(review_id.startswith("detail-rule-") for review_id in template_ids)
    expected_ids = candidate_ids if template_uses_candidates else queue_ids
    review_kinds = Counter(str(row.get("review_kind") or "") for row in queue)
    review_reasons = Counter(str(row.get("review_reasons") or "") for row in queue)
    nonblank_decisions = sum(
        bool(any(str(row.get(field) or "").strip() for field in (
            "decision", "approved_target", "reviewer", "reviewed_at", "evidence_status",
        )))
        for row in template
    )
    checks = {
        "queue_row_count_matches_manifest": len(queue) == int(queue_manifest.get("row_count") or -1),
        "template_row_count_matches_manifest": len(template) == int(template_manifest.get("row_count") or -1),
        "queue_sha256_matches_manifest": _sha256(queue_path) == str(queue_manifest.get("sha256") or ""),
        "template_binds_queue_sha256": _sha256(queue_path) == str(template_manifest.get("queue_sha256") or ""),
        "template_binds_queue_manifest_sha256": _sha256(queue_manifest_path) == str(template_manifest.get("queue_manifest_sha256") or ""),
        "queue_review_ids_unique": all(review_id and count == 1 for review_id, count in queue_counts.items()),
        "template_review_ids_unique": all(review_id and count == 1 for review_id, count in template_counts.items()),
        "queue_and_template_review_ids_equal": set(expected_ids) == set(template_ids),
        "candidate_ids_rebuilt_unique": all(review_id and count == 1 for review_id, count in Counter(candidate_ids).items()),
        "candidate_scope_context_bound": (
            not template_uses_candidates or all(
                row.get("context_scope") and "<unknown>" not in row.get("context_scope", [])
                for row in candidates
            )
        ),
        "queue_contains_no_preapproval": not any(
            str(row.get("candidate_is_approved") or "").strip().casefold() in {"true", "1", "yes"}
            for row in queue
        ),
        "template_source_hashes_match": all(
            str(row.get("source_sha256") or "") == str(queue_manifest.get("source_sha256") or "")
            for row in template
        ),
        "template_target_hashes_match": all(
            str(row.get("target_sha256") or "") == str(queue_manifest.get("target_sha256") or "")
            for row in template
        ),
        "template_rule_hashes_match": all(
            str(row.get("detail_rules_sha256") or "") == str(queue_manifest.get("detail_rules_sha256") or "")
            for row in template
        ),
    }
    package_valid = all(checks.values())
    variant_rows = sum(count for reason, count in review_reasons.items() if "TRANSLATION_VARIANTS" in reason)
    report: dict[str, object] = {
        "schema": "ACTION_DETAIL_RULE_REVIEW_PACKAGE_AUDIT_V1",
        "status": "READY_FOR_DESCRIPTION_DETAILS_HUMAN_AUDIT" if package_valid else "DESCRIPTION_DETAILS_PILOT_FAILED",
        "package_valid": package_valid,
        "semantic_review_state": "NOT_STARTED" if nonblank_decisions == 0 else "IN_PROGRESS",
        "review_scope": "candidate_id" if template_uses_candidates else "review_id",
        "template_uses_aggregate_candidates": template_uses_candidates,
        "checks": checks,
        "counts": {
            "queue_rows": len(queue), "template_rows": len(template),
            "candidate_rows": len(candidates),
            "nonblank_decisions": nonblank_decisions,
            "source_key_rows": review_kinds.get("SOURCE_KEY", 0),
            "source_value_rows": review_kinds.get("SOURCE_VALUE", 0),
            "pair_alignment_rows": review_kinds.get("PAIR_ALIGNMENT", 0),
            "translation_variant_rows": variant_rows,
        },
        "review_kind_counts": dict(sorted(review_kinds.items())),
        "review_reason_counts": dict(sorted(review_reasons.items())),
        "facts": {
            "guard_pass_is_translation_pass": False,
            "candidate_is_approved": False,
            "qwen_calls_for_review": 0,
            "master_writes": 0,
            "production_apply": False,
        },
        "artifacts": {
            "queue": str(queue_path), "queue_sha256": _sha256(queue_path),
            "queue_manifest": str(queue_manifest_path), "queue_manifest_sha256": _sha256(queue_manifest_path),
            "decision_template": str(template_path), "decision_template_sha256": _sha256(template_path),
            "decision_template_manifest": str(template_manifest_path),
            "decision_template_manifest_sha256": _sha256(template_manifest_path),
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--queue-manifest", required=True, type=Path)
    parser.add_argument("--decision-template", required=True, type=Path)
    parser.add_argument("--decision-template-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = audit_package(
        args.queue, args.queue_manifest, args.decision_template, args.decision_template_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "package_valid": report["package_valid"],
        "semantic_review_state": report["semantic_review_state"],
        "counts": report["counts"], "output": str(args.output),
    }, ensure_ascii=True))
    return 0 if report["package_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
