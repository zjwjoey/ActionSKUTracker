"""Write the self-improvement protocol artifacts for a retranslation batch.

This is an audit-only step. It never edits Master, PRIMARY, Dictionary or an
Owner decision. The generated proposal/ledger files make each durable QA
change traceable to evidence and regression scope.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    batch = Path(args.batch_dir).resolve()
    findings = rows(batch / "qa_findings.csv")
    normalized = rows(batch / "qa_findings_normalized.csv")
    candidates = rows(batch / "retranslation_candidates.csv")
    unresolved = [item for item in normalized if item.get("resolved") != "YES"]
    policy_accepted = [item for item in normalized if item.get("resolved") == "YES"]
    failed_fields = sorted({(item.get("sku", ""), item.get("field_name", "")) for item in findings})
    unresolved_fields = sorted({(item.get("sku", ""), item.get("field_name", "")) for item in unresolved})
    before_after = json.loads((batch / "qa_replay_summary.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8")) if (batch / "manifest.json").exists() else {}
    evidence = defaultdict(list)
    for item in normalized:
        key = item.get("root_cause") or item.get("policy_classification") or "UNKNOWN"
        if len(evidence[key]) < 20:
            evidence[key].append(f"{item.get('sku')}:{item.get('field_name')}")
    changes = [
        {
            "change_id": "LRN-RETRANS-001",
            "problem_class": "QA_FALSE_POSITIVE",
            "root_cause": "Source-bound uppercase Spanish values and reviewed technical/series tokens were treated as ordinary Spanish residuals or mandatory brand tokens.",
            "evidence_skus": sorted({item.get("sku", "") for item in normalized if item.get("root_cause") in {"INTENTIONAL_BRAND_REMOVAL", "LATIN_TOKEN_POLICY", "BRAND_POLICY_VIOLATION"}}),
            "evidence_count": sum(1 for item in normalized if item.get("root_cause") in {"INTENTIONAL_BRAND_REMOVAL", "LATIN_TOKEN_POLICY", "BRAND_POLICY_VIOLATION"}),
            "target_layer": "QA_RULE_AND_DISPLAY_POLICY",
            "current_behavior": "Brand omission, source-bound series, and localized technical abbreviations can remain as blocking QA findings.",
            "expected_behavior": "Keep the finding in the immutable audit trail, but mark explicit policy acceptance as resolved; keep true residuals and brand-policy violations blocking.",
            "planned_change": "Source-bound aliases, phrase-aware series allowlist, boundary token matching, and field-level policy resolution.",
            "risk": "An over-broad allowlist could hide a real Spanish residual.",
            "positive_regression": "Reviewed source-bound series/abbreviations and no-brand output pass without erasing audit evidence.",
            "negative_regression": "Unknown Latin words, pure brand-only names, and mixed brand-policy violations remain review-required.",
            "expected_metric_change": "Raw 138 failed fields to 8 raw failures; effective unresolved findings limited to the remaining true/owner-review blockers.",
        },
        {
            "change_id": "LRN-RETRANS-002",
            "problem_class": "NUMERIC_NORMALIZATION",
            "root_cause": "Scoped Spanish quantity phrases such as '3 capas' and source-bound refill/quantity facts were not represented by the numeric equivalence contract.",
            "evidence_skus": ["2506494", "2508891", "2516766", "2531879", "2531979", "2534566", "2549768"],
            "evidence_count": 7,
            "target_layer": "FACT_QA_AND_DETERMINISTIC_REPAIR",
            "current_behavior": "Faithful normalized Chinese output can be reported as numeric/token loss or English residual.",
            "expected_behavior": "Apply only source-bound deterministic repairs and scoped equivalence rules; never invent a value.",
            "planned_change": "Add reviewed phrase equivalences and deterministic repair records, each with a source fragment and replay regression.",
            "risk": "A global replacement could alter unrelated products.",
            "positive_regression": "The exact SKU/field pairs replay with Fact QA PASS.",
            "negative_regression": "No source fragment means no automatic repair; empty source remains a source exception.",
            "expected_metric_change": "NUMERIC_DROPPED reduced from 19 to 0 in the repaired subset; remaining raw failures do not include numeric loss.",
        },
        {
            "change_id": "LRN-RETRANS-003",
            "problem_class": "EMPTY_SOURCE_EXCEPTION",
            "root_cause": "A required Chinese description has no official Spanish description source for the SKU.",
            "evidence_skus": ["2548558"],
            "evidence_count": 1,
            "target_layer": "SOURCE_CONFLICT_REVIEW",
            "current_behavior": "Empty target is a blocking failure even when the official source field is empty.",
            "expected_behavior": "Keep the row in Owner/source-exception review; never fabricate a description from other fields.",
            "planned_change": "Record source exception and keep production Apply blocked for that field.",
            "risk": "Treating an absent source as acceptable could silently overwrite an existing Master value.",
            "positive_regression": "Empty-source row remains MISSING and production_writes=false.",
            "negative_regression": "Non-empty source with empty target remains a hard failure.",
            "expected_metric_change": "No automatic repair; one explicit source exception remains.",
        },
        {
            "change_id": "LRN-RETRANS-004",
            "problem_class": "FAMILY_POLICY_GAP",
            "root_cause": "The batch contains repeated hosiery and duvet semantics that were previously handled only by global numeric/semantic rules.",
            "evidence_skus": ["SOCKS_HOSIERY_V1", "BEDDING_DUVET_V1"],
            "evidence_count": sum(1 for item in candidates if any(term in str(item.get("source_es", "")).casefold() for term in ("calcetines", "edredón", "edredon", "4 estaciones"))),
            "target_layer": "PRODUCT_FAMILY_REGISTRY",
            "current_behavior": "Hosiery size ranges and duvet semantic facts can be treated as generic numbers without family context.",
            "expected_behavior": "Use versioned family context for product type, size range, material, quantity and four-season semantics; keep Baby Wipes as a deferred candidate.",
            "planned_change": "Register SOCKS_HOSIERY_V1 and BEDDING_DUVET_V1, emit real SKU regression cases, and emit BABY_WIPES as FAMILY_CANDIDATE only.",
            "risk": "Over-classification of ordinary mentions (for example socks mentioned in a laundry-bag description) could attach the wrong family.",
            "positive_regression": "Strong name/category evidence yields the expected versioned family; weak incidental mentions remain UNKNOWN or candidate-only.",
            "negative_regression": "Kitchen paper and unrelated products do not get promoted by a details-only incidental term.",
            "expected_metric_change": "Family regression artifacts populated for the same 500-SKU batch without changing Master.",
        },
        {
            "change_id": "LRN-RETRANS-005",
            "problem_class": "AUTHORITATIVE_FAMILY_SCOPE",
            "root_cause": "Family metadata was inherited per field/donor revision instead of being recomputed once from the complete SKU source record.",
            "evidence_skus": ["2521958", "2507094", "2545900", "2547167"],
            "evidence_count": 92,
            "target_layer": "PRODUCT_FAMILY_AND_PENDING_GATE",
            "current_behavior": "One SKU can carry multiple family ids and unrelated products can inherit CLEANING_CLOTH output.",
            "expected_behavior": "One authoritative family per SKU; all six fields inherit the same recomputed metadata.",
            "planned_change": "Require product-name identity evidence for CLEANING_CLOTH, recompute family once per SKU, and preserve family conflicts as audit evidence.",
            "risk": "A stricter classifier may leave a real family as UNKNOWN until Owner-approved evidence exists.",
            "positive_regression": "Wall hangers, canvas, shoelaces, bandages and mop systems no longer receive CLEANING_CLOTH policy.",
            "negative_regression": "Real cloth names with cleaning category context remain classified as CLEANING_CLOTH_V1.",
            "expected_metric_change": "Per-SKU family consistency becomes deterministic; false-positive family candidates are removed from resolution.",
        },
        {
            "change_id": "LRN-RETRANS-006",
            "problem_class": "FAMILY_CANDIDATE_ISOLATION",
            "root_cause": "Deferred FAMILY_CANDIDATE metadata could appear as if it were an active family policy.",
            "evidence_skus": ["2501374"],
            "evidence_count": 1,
            "target_layer": "FAMILY_REGISTRY_AND_RESOLVER",
            "current_behavior": "Candidate family metadata can be visible in a field-level chain.",
            "expected_behavior": "FAMILY_CANDIDATE is observation-only and maps to effective UNKNOWN for deterministic resolution/canonical enforcement.",
            "planned_change": "Persist family_status/family_candidate_id separately and never pass candidate family ids into resolver policy scope.",
            "risk": "Deferred family learning remains unavailable to automatic translation until Owner approval.",
            "positive_regression": "Baby-wipe candidate metadata cannot influence beverage translation.",
            "negative_regression": "ACTIVE family policies continue to scope TM, terminology and canonical QA.",
            "expected_metric_change": "Candidate-only family rows are auditable without production effect.",
        },
        {
            "change_id": "LRN-RETRANS-007",
            "problem_class": "MASTER_PENDING_QA_V2",
            "root_cause": "Basic Fact/Canonical QA did not detect identity collapse, category context mismatch, translated brand aliases or old-to-new degradation.",
            "evidence_skus": ["2500418", "2501374", "2502085", "2521958"],
            "evidence_count": 4,
            "target_layer": "PENDING_MASTER_GATE",
            "current_behavior": "A semantically wrong candidate can be REPLACE_READY when basic QA passes.",
            "expected_behavior": "V2 findings block Owner queue readiness until the candidate is repaired or explicitly reviewed.",
            "planned_change": "Add FAMILY_CONSISTENCY, FAMILY_CLASSIFIER_CONFLICT, PRODUCT_IDENTITY_DROPPED, CATEGORY_CONTEXT_MISMATCH, BRAND_ALIAS_RESIDUAL, IP_ALIAS_RESIDUAL, SEMANTIC_REGRESSION and OLD_TO_NEW_DEGRADATION checks.",
            "risk": "False positives increase review volume; findings remain non-destructive and source-bound.",
            "positive_regression": "Pepsi Max alias, XL-only gift bag, DIY paint terminology and false cleaning-cloth names are blocked or repaired.",
            "negative_regression": "Approved brand/IP omissions and legitimate technical tokens remain distinguishable from real residuals.",
            "expected_metric_change": "V2 finding count is explicit and zero only after the P0 repairs are replayed.",
        },
        {
            "change_id": "LRN-RETRANS-008",
            "problem_class": "SCOPED_CONTEXT_TERMINOLOGY",
            "root_cause": "The Spanish word pintura was translated without the official DIY category context.",
            "evidence_skus": ["2500418"],
            "evidence_count": 12,
            "target_layer": "PLANNER_AND_DETERMINISTIC_REPAIR",
            "current_behavior": "Bricolaje + Complementos de pintura can become 绘画配件.",
            "expected_behavior": "The scoped pair maps to 涂料配件; unrelated painting contexts remain unchanged.",
            "planned_change": "Add source-bound category-pair rule and replay repair for cat2/details.",
            "risk": "A broad global replacement would damage art-product translations.",
            "positive_regression": "DIY paint accessory rows use 涂料配件 and no longer raise CATEGORY_CONTEXT_MISMATCH.",
            "negative_regression": "Hobby/Pintura art contexts are not rewritten by the rule.",
            "expected_metric_change": "All matched DIY pair rows are deterministic or Owner-reviewable with zero context mismatch.",
        },
    ]
    proposal = {
        "protocol": "LOCALIZATION_SELF_IMPROVEMENT_V1",
        "batch_dir": str(batch),
        "batch_id": batch.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_writes": False,
        "master_modified": False,
        "raw_fact_fail_field_count": len(failed_fields),
        "unresolved_finding_count": len(unresolved),
        "unresolved_field_count": len(unresolved_fields),
        "policy_accepted_finding_count": len(policy_accepted),
        "source_snapshot_hash": source_manifest.get("source_snapshot_hash", ""),
        "changes": changes,
        "open_owner_review": sorted({f"{sku}:{field}" for sku, field in unresolved_fields}),
        "decision": "KEEP_BATCH_OPEN",
    }
    (batch / "learning_change_proposal.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger = {
        "ledger_version": "LOCALIZATION_LEARNING_LEDGER_V1",
        "entry_id": f"{batch.name}:self-improvement",
        "batch_id": batch.name,
        "status": "PROPOSED_PENDING_OWNER_REVIEW",
        "accepted_policy_findings": len(policy_accepted),
        "unresolved_fields": [f"{sku}:{field}" for sku, field in unresolved_fields],
        "changes": [item["change_id"] for item in changes],
        "regression_required": True,
        "same_batch_rerun_required": True,
        "production_writes": False,
        "created_at": proposal["generated_at"],
    }
    (batch / "learning_ledger_entry.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    report = [
        "# Localization Self-Improvement Protocol — Batch Artifact",
        "",
        f"- Batch: `{batch.name}`",
        f"- Raw Fact QA failed fields: **{len(failed_fields)}**",
        f"- Policy-accepted audit findings: **{len(policy_accepted)}**",
        f"- Unresolved fields requiring Owner/source review: **{len(unresolved_fields)}**",
        "- Production writes: **0**",
        "- Decision: **KEEP_BATCH_OPEN**",
        "",
        "## Learning changes",
        "",
    ]
    for change in changes:
        report.extend([
            f"### {change['change_id']} — {change['problem_class']}",
            f"- Root cause: {change['root_cause']}",
            f"- Evidence: {', '.join(change['evidence_skus'])} ({change['evidence_count']})",
            f"- Target layer: `{change['target_layer']}`",
            f"- Planned change: {change['planned_change']}",
            f"- Risk: {change['risk']}",
            f"- Positive regression: {change['positive_regression']}",
            f"- Negative regression: {change['negative_regression']}",
            "",
        ])
    report.extend([
        "## Required next steps",
        "",
        "1. Run targeted and full regression after the rule/repair changes.",
        "2. Re-run the same 500 SKU batch and compare before/after.",
        "3. Owner review the regenerated queue; do not infer approval.",
        "4. Generate Patch/Apply Preview only after Owner decisions and freshness checks.",
        "",
        "`KEEP_BATCH_OPEN`",
    ])
    (batch / "learning_change_proposal.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest_path = batch / "pending_outputs_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"batch_id": batch.name}
    files = dict(manifest.get("files") or {})
    import hashlib
    for name in ("learning_change_proposal.json", "learning_change_proposal.md", "learning_ledger_entry.json", "master_pending_qa_recheck.json", "approved_patch.csv", "apply_preview.csv", "master_diff.csv", "apply_preview_manifest.json"):
        digest = hashlib.sha256((batch / name).read_bytes()).hexdigest()
        files[name] = digest
    manifest["files"] = files
    manifest["learning_protocol"] = "LOCALIZATION_SELF_IMPROVEMENT_V1"
    manifest["raw_failed_field_count"] = len(failed_fields)
    manifest["unresolved_field_count"] = len(unresolved_fields)
    manifest["policy_accepted_finding_count"] = len(policy_accepted)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"batch_dir": str(batch), "raw_failed_fields": len(failed_fields), "unresolved_fields": len(unresolved_fields), "policy_accepted": len(policy_accepted), "production_writes": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
