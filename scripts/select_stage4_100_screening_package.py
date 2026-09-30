"""Build a 100-row Stage-4 screening package without hiding leakage.

The package intentionally separates disjoint candidates from historical-overlap
rows.  It never promotes either group to Gold or training-eligible status.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(r"F:/ActionSKUTracker")
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.services.hashing import localization_source_hash

RUN = ROOT / "runtime/training/qwen3_8b/20260911"
CLEAN = RUN / "stage4_targeted_remediation_review_queue_v6_clean_probe.csv"
SOURCE = ROOT / "runtime/training/qwen3_8b/20260908/qwen_candidates_5767.jsonl"
MASTER = ROOT / "runtime/master/Action_Master.xlsx"
OUT = RUN / "stage4_retraining_selection_100_screening_v2_20260912.csv"
MANIFEST = RUN / "stage4_retraining_selection_100_screening_v2_20260912.json"
BLIND_GOLD_OUT = RUN / "stage4_retraining_blind_gold_100_owner_review_20260912.csv"
BLIND_GOLD_MANIFEST = RUN / "stage4_retraining_blind_gold_100_owner_review_20260912.json"

SPLIT_FILES = {
    "qwen_field_train.jsonl": "field_train",
    "qwen_field_validation.jsonl": "field_validation",
    "qwen_field_test.jsonl": "field_test",
    "qwen_train.jsonl": "row_train",
    "qwen_validation.jsonl": "row_validation",
    "qwen_test.jsonl": "row_test",
    "stage4_test_only_485.jsonl": "stage4_test_only",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_candidate(row: dict) -> dict:
    messages = row.get("messages") or []
    source = {}
    target = {}
    for message in messages:
        if message.get("role") == "user":
            try:
                source = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                source = {}
        elif message.get("role") == "assistant":
            try:
                target = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                target = {}
    metadata = row.get("metadata") or {}
    return {
        "sku": str(metadata.get("sku") or "").strip(),
        "source_hash": str(metadata.get("source_hash") or "").strip(),
        "source_name_es": source.get("name", ""),
        "source_spec_es": source.get("spec", ""),
        "source_description_es": source.get("description", ""),
        "source_details_es": source.get("details", ""),
        "source_cat1_es": source.get("cat1", ""),
        "source_cat2_es": source.get("cat2", ""),
        "candidate_name_zh": target.get("name", ""),
        "candidate_spec_zh": target.get("spec", ""),
        "candidate_description_zh": target.get("description", ""),
        "candidate_details_zh": target.get("details", ""),
        "candidate_cat1_zh": target.get("cat1", ""),
        "candidate_cat2_zh": target.get("cat2", ""),
    }


def historical_split_memberships() -> dict[str, set[str]]:
    """Index direct memberships in prior train/validation/test artifacts."""
    values: dict[str, set[str]] = {}
    for path in (ROOT / "runtime/training/qwen3_8b").rglob("*.jsonl"):
        label = SPLIT_FILES.get(path.name)
        if not label:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sku = str(row.get("sku") or (row.get("metadata") or {}).get("sku") or "").strip()
            if sku:
                values.setdefault(sku, set()).add(label)
    return values


def all_artifact_memberships() -> dict[str, set[str]]:
    """Index every prior JSONL artifact without reading its model labels forward."""
    values: dict[str, set[str]] = {}
    corpus = ROOT / "runtime/training/qwen3_8b"
    for path in corpus.rglob("*.jsonl"):
        if ".venv" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sku = str(row.get("sku") or (row.get("metadata") or {}).get("sku") or "").strip()
            if sku:
                values.setdefault(sku, set()).add(str(path.relative_to(corpus)))
    return values


def master_sources() -> dict[str, dict[str, str]]:
    """Load the current six official Spanish fields and canonical hash by SKU."""
    wb = load_workbook(MASTER, read_only=True, data_only=True)
    try:
        sheet = wb["02_SKU_ES_CURRENT"]
        rows = sheet.iter_rows(values_only=True)
        headers = [str(value or "") for value in next(rows)]
        index = {name: position for position, name in enumerate(headers)}
        column_map = {
            "source_name_es": "西班牙语品名",
            "source_cat1_es": "一级类目（西语）",
            "source_cat2_es": "二级类目（西语）",
            "source_spec_es": "规格（西语）",
            "source_description_es": "描述（西语）",
            "source_details_es": "产品详情（西语）",
        }
        missing = [name for name in column_map.values() if name not in index]
        if missing:
            raise RuntimeError(f"MASTER_SCHEMA_MISSING:{','.join(missing)}")
        result: dict[str, dict[str, str]] = {}
        for raw in rows:
            sku = str(raw[index["SKU"]] or "").strip()
            if not sku:
                continue
            source = {key: str(raw[index[column]] or "").strip() for key, column in column_map.items()}
            source["source_hash"] = localization_source_hash({
                "name_es": source["source_name_es"],
                "cat1_es": source["source_cat1_es"],
                "cat2_es": source["source_cat2_es"],
                "spec_es": source["source_spec_es"],
                "desc_es": source["source_description_es"],
                "details_es": source["source_details_es"],
            })
            result[sku] = source
        return result
    finally:
        wb.close()


def build_blind_gold_100() -> None:
    """Build an owner-review package with no prior model predictions exposed.

    18 candidate-history rows are allowed only under explicit owner approval.
    They have never appeared in a train/validation/test split.  Their prior
    artifacts are recorded only as a boolean so reviewers cannot copy a model
    answer into the new Gold label.
    """
    clean_rows = list(csv.DictReader(CLEAN.open(encoding="utf-8-sig", newline="")))
    strict_by_sku = {str(row.get("sku") or "").strip(): row for row in clean_rows}
    master_by_sku = master_sources()
    split_by_sku = historical_split_memberships()
    artifact_by_sku = all_artifact_memberships()
    source_fields = (
        "source_name_es", "source_cat1_es", "source_cat2_es",
        "source_spec_es", "source_description_es", "source_details_es",
    )

    strict = []
    targeted_candidate_history = []
    for sku, review in sorted(strict_by_sku.items()):
        source = master_by_sku.get(sku)
        if not source or split_by_sku.get(sku):
            raise RuntimeError(f"STRICT_SELECTION_LINEAGE_CHANGED:{sku}")
        if not all(source[field] for field in source_fields):
            raise RuntimeError(f"STRICT_SELECTION_SOURCE_INCOMPLETE:{sku}")
        target = (sku, source, review.get("failure_class", ""), review.get("target_field", ""))
        if artifact_by_sku.get(sku):
            targeted_candidate_history.append(target)
        else:
            strict.append(target)
    if len(strict) + len(targeted_candidate_history) != 39:
        raise RuntimeError("EXPECTED_39_TARGETED_ROWS")

    strict_skus = {item[0] for item in strict} | {item[0] for item in targeted_candidate_history}
    virgin = []
    candidate_history = []
    for sku, source in sorted(master_by_sku.items()):
        if sku in strict_skus or split_by_sku.get(sku) or not all(source[field] for field in source_fields):
            continue
        if artifact_by_sku.get(sku):
            candidate_history.append((sku, source, "", ""))
        else:
            virgin.append((sku, source, "", ""))
    if len(virgin) < 52 or len(candidate_history) < 9:
        raise RuntimeError(f"INSUFFICIENT_BLIND_REVIEW_POOL:virgin={len(virgin)},candidate_history={len(candidate_history)}")
    selected = (
        [(sku, source, "P0_TARGETED_STRICT", failure, field) for sku, source, failure, field in strict]
        + [(sku, source, "P0_TARGETED_CANDIDATE_HISTORY_BLIND_REVIEW", failure, field) for sku, source, failure, field in targeted_candidate_history]
        + [(sku, source, "UNSEEN_SOURCE_BLIND_REVIEW", failure, field) for sku, source, failure, field in virgin[:52]]
        + [(sku, source, "CANDIDATE_HISTORY_BLIND_REVIEW", failure, field) for sku, source, failure, field in candidate_history[:9]]
    )
    if len(selected) != 100 or len({row[0] for row in selected}) != 100:
        raise RuntimeError("BLIND_GOLD_SELECTION_NOT_100_UNIQUE")

    fields = [
        "sku", "selection_tier", "training_eligible", "human_gold_required",
        "prior_train_validation_test_membership", "prior_candidate_artifact_exists",
        "old_model_outputs_redacted", "family_isolation_status", "source_hash",
        "source_cat1_es", "source_cat2_es", "source_name_es", "source_spec_es",
        "source_description_es", "source_details_es", "target_failure_class", "target_field",
        "human_cat1_zh", "human_cat2_zh", "human_name_zh", "human_spec_zh",
        "human_description_zh", "human_details_zh", "human_disposition", "reviewer", "reviewed_at",
    ]
    output = []
    for sku, source, tier, failure_class, target_field in selected:
        if split_by_sku.get(sku):
            raise RuntimeError(f"BLIND_GOLD_SPLIT_LEAKAGE:{sku}")
        output.append({
            "sku": sku,
            "selection_tier": tier,
            "training_eligible": "NO",
            "human_gold_required": "YES",
            "prior_train_validation_test_membership": "",
            "prior_candidate_artifact_exists": "YES" if artifact_by_sku.get(sku) else "NO",
            "old_model_outputs_redacted": "YES",
            "family_isolation_status": "PENDING_GROUP_SIMILARITY_REVIEW",
            "source_hash": source["source_hash"],
            "source_cat1_es": source["source_cat1_es"],
            "source_cat2_es": source["source_cat2_es"],
            "source_name_es": source["source_name_es"],
            "source_spec_es": source["source_spec_es"],
            "source_description_es": source["source_description_es"],
            "source_details_es": source["source_details_es"],
            "target_failure_class": failure_class,
            "target_field": target_field,
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
    with BLIND_GOLD_OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    manifest = {
        "manifest_version": "stage4-blind-gold-100-owner-review-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner_authorized_candidate_history_blind_review": True,
        "rows": 100,
        "p0_targeted_strict_rows": 28,
        "p0_targeted_candidate_history_rows": 11,
        "unseen_source_blind_review_rows": 52,
        "candidate_history_blind_review_rows": 20,
        "historical_train_validation_test_overlap_rows": 0,
        "old_model_outputs_in_review_package": False,
        "training_eligible_rows": 0,
        "formal_training_allowed": False,
        "remaining_requirements": [
            "human_gold_review", "source_hash_recheck_before_promotion",
            "group_similarity_split", "independent_test_freeze",
        ],
        "source_master": {"path": str(MASTER), "sha256": sha(MASTER)},
        "csv": {"path": str(BLIND_GOLD_OUT), "sha256": sha(BLIND_GOLD_OUT)},
    }
    BLIND_GOLD_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "rows": 100, "strict": len(strict), "targeted_candidate_history": len(targeted_candidate_history),
        "unseen": 52, "candidate_history_blind": 20,
        "train_validation_test_overlap": 0, "training_eligible": 0,
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage-4 training screening packages")
    parser.add_argument("--blind-gold-100", action="store_true", help="Build the owner-authorized, model-output-redacted 100-row Gold review package")
    args = parser.parse_args()
    if args.blind_gold_100:
        build_blind_gold_100()
        return
    clean_rows = list(csv.DictReader(CLEAN.open(encoding="utf-8-sig", newline="")))
    clean_by_sku = {str(row.get("sku") or "").strip(): row for row in clean_rows}
    master_by_sku = master_sources()
    split_by_sku = historical_split_memberships()

    source_rows = []
    seen = set()
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = parse_candidate(json.loads(line))
        sku = parsed["sku"]
        if not sku or sku in seen or sku in clean_by_sku:
            continue
        seen.add(sku)
        if all(parsed.get(key, "").strip() for key in ("source_name_es", "source_spec_es", "source_description_es", "source_details_es", "source_cat1_es", "source_cat2_es")):
            source_rows.append(parsed)
        if len(source_rows) >= 61:
            break

    fields = [
        "sku", "selection_tier", "training_eligible", "human_gold_required",
        "eligibility_reason", "historical_split_membership", "family_isolation_status",
        "source_hash", "source_cat1_es", "source_cat2_es",
        "source_name_es", "source_spec_es", "source_description_es", "source_details_es",
        "candidate_cat1_zh", "candidate_cat2_zh", "candidate_name_zh",
        "candidate_spec_zh", "candidate_description_zh", "candidate_details_zh",
        "failure_class", "target_field",
    ]
    output = []
    for sku in sorted(clean_by_sku)[:39]:
        row = clean_by_sku[sku]
        master = master_by_sku.get(sku)
        if not master:
            raise RuntimeError(f"DISJOINT_SKU_MISSING_FROM_MASTER:{sku}")
        for field in ("source_name_es", "source_spec_es", "source_description_es", "source_details_es"):
            if master[field] != str(row.get(field) or "").strip():
                raise RuntimeError(f"MASTER_SOURCE_MISMATCH:{sku}:{field}")
        if split_by_sku.get(sku):
            raise RuntimeError(f"DISJOINT_SKU_HAS_HISTORICAL_SPLIT:{sku}")
        output.append({
            "sku": sku,
            "selection_tier": "DISJOINT_CANDIDATE",
            "training_eligible": "NO",
            "human_gold_required": "YES",
            "eligibility_reason": "NOT_IN_HISTORICAL_TRAIN_VALIDATION_TEST; HUMAN_GOLD_NOT_YET_SIGNED",
            "historical_split_membership": "",
            "family_isolation_status": "SKU_DISJOINT;EXACT_P0_CAT_PAIR_EXCLUDED;HIGH_SIMILARITY_REVIEW_REQUIRED",
            "source_hash": master["source_hash"],
            "source_cat1_es": master["source_cat1_es"],
            "source_cat2_es": master["source_cat2_es"],
            "source_name_es": master["source_name_es"],
            "source_spec_es": master["source_spec_es"],
            "source_description_es": master["source_description_es"],
            "source_details_es": master["source_details_es"],
            "candidate_cat1_zh": "",
            "candidate_cat2_zh": "",
            "candidate_name_zh": "",
            "candidate_spec_zh": "",
            "candidate_description_zh": "",
            "candidate_details_zh": "",
            "failure_class": row.get("failure_class", ""),
            "target_field": row.get("target_field", ""),
        })
    for row in source_rows:
        memberships = sorted(split_by_sku.get(row["sku"], set()))
        if not memberships:
            raise RuntimeError(f"REFERENCE_ROW_MISSING_HISTORICAL_SPLIT:{row['sku']}")
        output.append({
            "sku": row["sku"],
            "selection_tier": "HISTORICAL_OVERLAP_REFERENCE_ONLY",
            "training_eligible": "NO",
            "human_gold_required": "YES",
            "eligibility_reason": "OVERLAPS_HISTORICAL_TRAIN_VALIDATION_OR_TEST; EXCLUDED_FROM_FORMAL_TRAINING",
            "historical_split_membership": ",".join(memberships),
            "family_isolation_status": "HISTORICAL_SPLIT_OVERLAP",
            "source_hash": row["source_hash"],
            "source_cat1_es": row["source_cat1_es"],
            "source_cat2_es": row["source_cat2_es"],
            "source_name_es": row["source_name_es"],
            "source_spec_es": row["source_spec_es"],
            "source_description_es": row["source_description_es"],
            "source_details_es": row["source_details_es"],
            "candidate_cat1_zh": row["candidate_cat1_zh"],
            "candidate_cat2_zh": row["candidate_cat2_zh"],
            "candidate_name_zh": row["candidate_name_zh"],
            "candidate_spec_zh": row["candidate_spec_zh"],
            "candidate_description_zh": row["candidate_description_zh"],
            "candidate_details_zh": row["candidate_details_zh"],
            "failure_class": "",
            "target_field": "",
        })
    if len(output) != 100:
        raise SystemExit(f"EXPECTED_100_ROWS_GOT_{len(output)}")

    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    manifest = {
        "manifest_version": "stage4-retraining-selection-100-screening-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rows": 100,
        "disjoint_candidates": 39,
        "historical_overlap_reference_only": 61,
        "training_eligible_rows": 0,
        "human_gold_required_rows": 100,
        "formal_training_allowed": False,
        "reason": "39 candidates are SKU-disjoint but not human-signed; 61 overlap historical corpora and are quarantined",
        "source_clean_probe": {"path": str(CLEAN), "sha256": sha(CLEAN)},
        "source_candidate_pool": {"path": str(SOURCE), "sha256": sha(SOURCE)},
        "source_master": {"path": str(MASTER), "sha256": sha(MASTER)},
        "csv": {"path": str(OUT), "sha256": sha(OUT)},
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": 100, "disjoint": 39, "overlap_quarantined": 61, "training_eligible": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
