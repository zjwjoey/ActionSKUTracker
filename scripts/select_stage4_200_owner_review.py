"""Select a 200-row blind owner-review package from the current Master.

This is a screening package, not a training set.  Rows are source-only: prior
model outputs are deliberately omitted.  Historical split overlap and source
conflicts remain explicit so an owner can select a safe subset later.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"F:/ActionSKUTracker")
sys.path.insert(0, str(ROOT / "scripts"))
from select_stage4_100_screening_package import (  # type: ignore
    all_artifact_memberships,
    historical_split_memberships,
    master_sources,
    sha,
)

RUN = ROOT / "runtime/training/qwen3_8b/20260911"
MASTER = ROOT / "runtime/master/Action_Master.xlsx"
OUT = RUN / "stage4_blind_owner_review_200_screening_20260912.csv"
MANIFEST = RUN / "stage4_blind_owner_review_200_screening_20260912.json"

SOURCE_CONFLICTS = {
    "3226221": "spec 200 gramos vs details 200 kg",
    "3224814": "spec 5 piezas vs details/description 4 piezas",
    "3215124": "spec 3 piezas vs details 2 piezas",
    "3219963": "spec 5 piezas vs details 6 piezas",
    "3213525": "spec 10 ml vs details 11 ml",
    "3225091": "name/description lana vs details Poliéster",
    "3225092": "name/description lana vs details Poliéster",
    "3219090": "name/description lana vs details Poliéster",
}


def main() -> None:
    master = master_sources()
    split = historical_split_memberships()
    prior_artifacts = all_artifact_memberships()
    source_fields = (
        "source_name_es", "source_cat1_es", "source_cat2_es",
        "source_spec_es", "source_description_es", "source_details_es",
    )
    complete = [sku for sku, row in master.items() if all(row[field] for field in source_fields)]
    no_split = [sku for sku in complete if not split.get(sku)]
    split_only = [sku for sku in complete if split.get(sku)]
    # Keep all currently unseen source rows, then add a clearly quarantined
    # historical reference pool to reach exactly 200 review rows.
    no_split.sort(key=lambda sku: (sku not in SOURCE_CONFLICTS, sku))
    split_only.sort()
    selected_no_split = no_split[:116]
    selected_split = split_only[: max(0, 200 - len(selected_no_split))]
    selected = selected_no_split + selected_split
    if len(selected) != 200 or len(set(selected)) != 200:
        raise SystemExit(f"EXPECTED_200_UNIQUE_GOT_{len(selected)}")
    missing_conflicts = sorted(set(SOURCE_CONFLICTS) - set(selected))
    if missing_conflicts:
        raise SystemExit(f"SOURCE_CONFLICTS_NOT_INCLUDED:{','.join(missing_conflicts)}")

    fields = [
        "sku", "selection_tier", "training_eligible", "human_gold_required",
        "source_completeness", "source_conflict_status", "source_conflict_reason",
        "historical_split_membership", "prior_candidate_artifact_exists",
        "old_model_outputs_redacted", "family_isolation_status", "source_hash",
        "source_cat1_es", "source_cat2_es", "source_name_es", "source_spec_es",
        "source_description_es", "source_details_es", "human_cat1_zh", "human_cat2_zh",
        "human_name_zh", "human_spec_zh", "human_description_zh", "human_details_zh",
        "human_disposition", "reviewer", "reviewed_at",
    ]
    output = []
    for sku in selected:
        source = master[sku]
        split_membership = sorted(split.get(sku, set()))
        has_split = bool(split_membership)
        conflict_reason = SOURCE_CONFLICTS.get(sku, "")
        output.append({
            "sku": sku,
            "selection_tier": "HISTORICAL_SPLIT_REFERENCE_ONLY" if has_split else "NO_DIRECT_SPLIT_OVERLAP",
            "training_eligible": "NO",
            "human_gold_required": "YES",
            "source_completeness": "COMPLETE",
            "source_conflict_status": "SOURCE_CONFLICT" if conflict_reason else "NONE",
            "source_conflict_reason": conflict_reason,
            "historical_split_membership": ",".join(split_membership),
            "prior_candidate_artifact_exists": "YES" if prior_artifacts.get(sku) else "NO",
            "old_model_outputs_redacted": "YES",
            "family_isolation_status": "PENDING_GROUP_SIMILARITY_REVIEW",
            "source_hash": source["source_hash"],
            "source_cat1_es": source["source_cat1_es"],
            "source_cat2_es": source["source_cat2_es"],
            "source_name_es": source["source_name_es"],
            "source_spec_es": source["source_spec_es"],
            "source_description_es": source["source_description_es"],
            "source_details_es": source["source_details_es"],
            "human_cat1_zh": "",
            "human_cat2_zh": "",
            "human_name_zh": "",
            "human_spec_zh": "",
            "human_description_zh": "",
            "human_details_zh": "",
            "human_disposition": "PENDING_OWNER_REVIEW",
            "reviewer": "",
            "reviewed_at": "",
        })
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    manifest = {
        "manifest_version": "stage4-blind-owner-review-200-screening-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "owner selects a safe subset; this is not a training set",
        "rows": 200,
        "no_direct_split_overlap_rows": sum(not split.get(sku) for sku in selected),
        "historical_split_reference_rows": sum(bool(split.get(sku)) for sku in selected),
        "source_conflict_rows": sum(sku in SOURCE_CONFLICTS for sku in selected),
        "source_complete_rows": len(selected),
        "training_eligible_rows": 0,
        "formal_training_allowed": False,
        "old_model_outputs_in_review_package": False,
        "human_gold_required_rows": 200,
        "promotion_requirements": [
            "exclude SOURCE_CONFLICT",
            "exclude HISTORICAL_SPLIT_REFERENCE_ONLY for independent evaluation",
            "human Gold labels for all six fields",
            "source_hash recheck",
            "group similarity split",
        ],
        "source_conflict_skus": sorted(SOURCE_CONFLICTS),
        "source_master": {"path": str(MASTER), "sha256": sha(MASTER)},
        "csv": {"path": str(OUT), "sha256": sha(OUT)},
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rows": 200,
        "no_direct_split_overlap": manifest["no_direct_split_overlap_rows"],
        "historical_reference": manifest["historical_split_reference_rows"],
        "source_conflict": manifest["source_conflict_rows"],
        "training_eligible": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
