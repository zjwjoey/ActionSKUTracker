"""Build a 200-row training-only experiment package.

The package combines 177 owner-confirmed remediation rows (historically
overlapped and therefore not release-test eligible) with 23 Guard-approved
model-reviewed Silver rows.  It must never be used as an independent test set.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"F:/ActionSKUTracker")
RUN = ROOT / "runtime/training/qwen3_8b/20260911"
OWNER = RUN / "qwen_incremental_remediation177_owner_confirmed_gold.jsonl"
SILVER = RUN / "qwen_incremental_stage4_release500_approved_500.jsonl"
OUT = RUN / "stage4_training_experiment_200_20260912.jsonl"
MANIFEST = RUN / "stage4_training_experiment_200_20260912.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        metadata = row.get("metadata") or {}
        sku = str(metadata.get("sku") or row.get("sku") or "").strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        row["metadata"] = {**metadata, "sku": sku}
        rows.append(row)
    return rows


def has_six_field_target(row: dict) -> bool:
    for message in row.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        try:
            target = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            return False
        return all(str(target.get(field) or "").strip() for field in ("name", "spec", "description", "details", "cat1", "cat2"))
    return False


def main() -> None:
    owner_rows = read_rows(OWNER)
    silver_rows = read_rows(SILVER)
    owner_skus = {str((row.get("metadata") or {}).get("sku")) for row in owner_rows}
    candidates = []
    for row in silver_rows:
        metadata = row.get("metadata") or {}
        sku = str(metadata.get("sku") or "").strip()
        if not sku or sku in owner_skus or not has_six_field_target(row):
            continue
        if str(metadata.get("review_status") or "") != "APPROVED_FOR_INCREMENTAL_SPLIT":
            continue
        if str(metadata.get("second_verdict") or "") != "CONFIRMED":
            continue
        if metadata.get("guard_exceptions"):
            continue
        candidates.append(row)
    candidates.sort(key=lambda row: str((row.get("metadata") or {}).get("sku")))
    silver_selected = candidates[:23]
    if len(owner_rows) != 177 or len(silver_selected) != 23:
        raise SystemExit(f"EXPECTED_177_PLUS_23_GOT_{len(owner_rows)}_PLUS_{len(silver_selected)}")
    selected = []
    for row in owner_rows:
        metadata = row.get("metadata") or {}
        row["metadata"] = {
            **metadata,
            "training_package_tier": "OWNER_CONFIRMED_REMEDIATION",
            "training_use": "EXPERIMENT_ONLY",
            "formal_release_eligible": False,
            "historical_overlap": True,
        }
        selected.append(row)
    for row in silver_selected:
        metadata = row.get("metadata") or {}
        row["metadata"] = {
            **metadata,
            "training_package_tier": "MODEL_REVIEWED_SILVER",
            "training_use": "EXPERIMENT_ONLY",
            "formal_release_eligible": False,
            "historical_overlap": True,
        }
        selected.append(row)
    if len({str((row.get("metadata") or {}).get("sku")) for row in selected}) != 200:
        raise SystemExit("DUPLICATE_SKU_IN_TRAINING_EXPERIMENT")
    if not all(has_six_field_target(row) for row in selected):
        raise SystemExit("INCOMPLETE_SIX_FIELD_TARGET")
    OUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
    manifest = {
        "manifest_version": "stage4-training-experiment-200-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "training experiment only; never an independent release test",
        "rows": 200,
        "owner_confirmed_remediation_rows": 177,
        "model_reviewed_silver_rows": 23,
        "all_rows_have_six_field_targets": True,
        "all_skus_unique": True,
        "formal_release_eligible_rows": 0,
        "independent_test_eligible_rows": 0,
        "historical_overlap_allowed_for_training_only": True,
        "production_writes": False,
        "source_owner": {"path": str(OWNER), "sha256": sha(OWNER)},
        "source_silver": {"path": str(SILVER), "sha256": sha(SILVER)},
        "output": {"path": str(OUT), "sha256": sha(OUT)},
        "restrictions": [
            "do not use as Core/Hard/Temporal/OOD test",
            "do not set FULL_STAGE4_RELEASE=true",
            "do not overwrite old adapters",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": 200, "owner_confirmed": 177, "silver": 23, "formal_release_eligible": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
