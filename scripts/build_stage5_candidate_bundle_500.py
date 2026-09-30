"""Build a versioned 500-row Stage 5 candidate bundle.

This is an offline packaging utility. It never changes Master, SQLite,
dictionaries, or an adapter. The bundle is deliberately marked as not
training-ready because it combines previously reviewed silver rows with a
fresh review-required batch and records quarantined rows explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "runtime/training/qwen3_8b/20260910/qwen_incremental_approved_500.jsonl"
FRESH = ROOT / "runtime/training/qwen3_8b/20260913/qwen_incremental_stage5_fresh12_v1_candidate_12.jsonl"

# Stage 5 owner-signed isolation list. Rows touching these SKUs remain in the
# package only as quarantined evidence and must not enter training.
QUARANTINED_SKUS = {
    "2556104", "3206734", "3215546", "3219090", "3221992",
    "3222807", "3224146", "3224907", "3225230", "3225667",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="runtime/training/qwen3_8b/20260913")
    args = ap.parse_args()
    old = read_jsonl(OLD)
    fresh = read_jsonl(FRESH)
    rows = old + fresh
    if len(rows) != 500:
        raise SystemExit(f"EXPECTED_500_ROWS:{len(rows)}")
    skus = [str(r.get("metadata", {}).get("sku", "")) for r in rows]
    if not all(skus) or len(set(skus)) != len(skus):
        raise SystemExit("SKU_MUST_BE_UNIQUE_AND_NONEMPTY")
    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "qwen_incremental_stage5_candidate_bundle_500_v1.jsonl"
    manifest = out_dir / "qwen_incremental_stage5_candidate_bundle_500_v1.manifest.json"
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            copied = json.loads(json.dumps(row, ensure_ascii=False))
            meta = copied.setdefault("metadata", {})
            sku = str(meta.get("sku", ""))
            meta["bundle_id"] = "stage5_candidate_bundle_500_v1"
            meta["bundle_source"] = "approved_488_plus_fresh12"
            meta["quarantine_status"] = "QUARANTINED_OWNER_SIGNED_ISOLATION" if sku in QUARANTINED_SKUS else "NOT_QUARANTINED"
            meta["training_eligible"] = False
            fh.write(json.dumps(copied, ensure_ascii=False, sort_keys=True) + "\n")
    quarantine = sorted({s for s in skus if s in QUARANTINED_SKUS})
    payload = {
        "manifest_version": "stage5-candidate-bundle-500-v1",
        "status": "CANDIDATE_BUNDLE_NOT_TRAINING_READY",
        "row_count": len(rows),
        "unique_sku_count": len(set(skus)),
        "sources": {
            "approved_488": {"path": str(OLD), "rows": len(old), "sha256": sha256(OLD)},
            "fresh12": {"path": str(FRESH), "rows": len(fresh), "sha256": sha256(FRESH)},
        },
        "quarantine": {
            "sku_count": len(quarantine),
            "row_count": sum(1 for s in skus if s in QUARANTINED_SKUS),
            "skus": quarantine,
            "reason": "Stage5 owner-signed MAJOR/AMBIGUOUS isolation; evidence retained but excluded from training.",
        },
        "training_eligible": False,
        "required_before_training": [
            "Owner review and source-hash confirmation for fresh12",
            "Keep all quarantined rows out of training",
            "Run full SKU and product-family split leakage audit",
            "Create an unseen validation/test holdout; do not reuse this bundle for evaluation",
        ],
        "artifact": {"path": str(out), "sha256": sha256(out)},
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
