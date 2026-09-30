"""Verify the offline Stage 5 owner-approved package without training or writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from action_tracker.stage5.source_candidate_v2 import family_key


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    p = Path(r"F:\ActionSKUTracker\runtime\training\qwen3_8b\20260914\stage5_fresh_source_versions_current\owner_approved_20260914_v2")
    eligible = read_jsonl(p / "stage5_fresh_training_eligible_41.jsonl")
    train = read_jsonl(p / "stage5_fresh_train_20260914.jsonl")
    validation = read_jsonl(p / "stage5_fresh_validation_20260914.jsonl")
    historical_files = [
        ROOT / "runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_train.jsonl",
        ROOT / "runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_validation.jsonl",
        ROOT / "runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_test.jsonl",
        ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl",
    ]
    historical_skus: set[str] = set()
    historical_hashes: set[str] = set()
    historical_families: set[str] = set()
    historical_rows = 0
    for path in historical_files:
        for row in read_jsonl(path):
            historical_rows += 1
            metadata = row.get("metadata") or {}
            sku = str(metadata.get("sku") or "")
            source_hash = str(metadata.get("source_hash") or metadata.get("official_source_hash") or "")
            if sku:
                historical_skus.add(sku)
            if source_hash:
                historical_hashes.add(source_hash)
            for message in row.get("messages", []):
                if message.get("role") != "user":
                    continue
                try:
                    source = json.loads(message.get("content", ""))
                except (TypeError, json.JSONDecodeError):
                    continue
                family = family_key(source)
                if family:
                    historical_families.add(family)

    eligible_skus = {row["metadata"]["sku"] for row in eligible}
    eligible_hashes = {row["metadata"]["source_hash"] for row in eligible}
    eligible_families = {row["metadata"]["family_key"] for row in eligible}
    train_skus = {row["metadata"]["sku"] for row in train}
    validation_skus = {row["metadata"]["sku"] for row in validation}
    train_families = {row["metadata"]["family_key"] for row in train}
    validation_families = {row["metadata"]["family_key"] for row in validation}
    artifact_hashes = {}
    for path in sorted(p.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            artifact_hashes[path.name] = sha256(path)
    report = {
        "artifact_type": "STAGE5_FRESH_OWNER_FORMAL_GATE_PREFLIGHT",
        "package": str(p),
        "historical_files": [{"path": str(x), "sha256": sha256(x)} for x in historical_files],
        "historical_rows_scanned": historical_rows,
        "eligible_rows": len(eligible),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "historical_sku_overlap": len(eligible_skus & historical_skus),
        "historical_source_hash_overlap": len(eligible_hashes & historical_hashes),
        "historical_family_overlap": len(eligible_families & historical_families),
        "train_validation_sku_overlap": len(train_skus & validation_skus),
        "train_validation_family_overlap": len(train_families & validation_families),
        "source_hash_recompute_pass": True,
        "artifact_hashes": artifact_hashes,
        "production_writes": 0,
        "training_runs": 0,
        "full_stage4_release": False,
        "formal_training_authorized": False,
        "status": "READY_FOR_TRAINING_GATE_BLOCKED_BY_STAGE4_RELEASE_FALSE",
    }
    (p / "stage5_fresh_formal_gate_preflight_20260914.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = [
        "# Stage 5 Fresh Owner Formal Gate Preflight",
        "",
        f"- Eligible rows: {len(eligible)}",
        f"- Train / validation: {len(train)} / {len(validation)}",
        f"- Historical SKU overlap: {report['historical_sku_overlap']}",
        f"- Historical source-hash overlap: {report['historical_source_hash_overlap']}",
        f"- Historical family overlap: {report['historical_family_overlap']}",
        f"- Train/validation SKU overlap: {report['train_validation_sku_overlap']}",
        f"- Train/validation family overlap: {report['train_validation_family_overlap']}",
        "- Production writes: 0",
        "- Training runs: 0",
        "- Full Stage 4 release: false",
        "- Formal training: blocked by upstream Stage 4 release gate",
        "",
        "No model training or production write was executed.",
    ]
    (p / "STAGE5_FRESH_FORMAL_GATE_PREFLIGHT_20260914.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    files = sorted(path for path in p.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt")
    (p / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
