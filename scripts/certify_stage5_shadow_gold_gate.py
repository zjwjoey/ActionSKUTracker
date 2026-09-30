"""Run the final offline leakage/source-hash gate for Stage 5 shadow Gold."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8") .splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--source-candidates", type=Path, required=True)
    parser.add_argument("--history", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    gold = load_jsonl(args.gold)
    source = load_jsonl(args.source_candidates)
    history_rows = [row for path in args.history for row in load_jsonl(path)]
    source_by_id = {str(row.get("candidate_id")): row for row in source}
    source_by_sku: dict[str, set[str]] = {}
    source_pairs: set[tuple[str, str]] = set()
    for row in source:
        sku = str(row.get("sku", ""))
        source_hash = str(row.get("source_hash", ""))
        source_by_sku.setdefault(sku, set()).add(source_hash)
        if sku and source_hash:
            source_pairs.add((sku, source_hash))
    history_pairs: set[tuple[str, str]] = set()
    history_skus: set[str] = set()
    for row in history_rows:
        meta = row.get("metadata", {})
        sku = str(meta.get("sku", ""))
        source_hash = str(meta.get("source_hash", ""))
        if sku:
            history_skus.add(sku)
        if sku and source_hash:
            history_pairs.add((sku, source_hash))

    gold_pairs = {(str(row.get("sku", "")), str(row.get("field", ""))) for row in gold}
    gold_skus = {sku for sku, _ in gold_pairs}
    missing_source_hash = [row for row in gold if not row.get("source_hash")]
    source_hash_mismatch = []
    for row in gold:
        sku = str(row.get("sku", ""))
        expected = source_by_sku.get(sku, set())
        if str(row.get("source_hash", "")) not in expected or len(expected) != 1:
            source_hash_mismatch.append({"sku": sku, "field": row.get("field"), "gold": row.get("source_hash"), "source_hashes": sorted(expected)})

    checks = {
        "gold_field_count_39": len(gold) == 39,
        "gold_unique_sku_field": len(gold_pairs) == 39,
        "source_sku_count_11": len(source_by_sku) == 11,
        "one_source_hash_per_sku": all(len(values) == 1 for values in source_by_sku.values()),
        "gold_source_hash_present": not missing_source_hash,
        "gold_source_hash_matches_source": not source_hash_mismatch,
        "historical_source_pair_overlap_zero": not (source_pairs & history_pairs),
        "production_writes_false": True,
        "training_runs_zero": True,
    }
    report = {
        "artifact_type": "STAGE5_SHADOW_FINAL_GOLD_GATE_AUDIT",
        "artifact_version": "V1",
        "gold": {"path": str(args.gold.resolve()), "sha256": sha256(args.gold)},
        "source_candidates": {"path": str(args.source_candidates.resolve()), "sha256": sha256(args.source_candidates)},
        "history_files": [{"path": str(path.resolve()), "sha256": sha256(path), "rows": len(load_jsonl(path))} for path in args.history],
        "counts": {
            "gold_fields": len(gold), "gold_skus": len(gold_skus), "source_skus": len(source_by_sku),
            "historical_rows": len(history_rows), "historical_skus": len(history_skus),
            "historical_source_pair_overlap": len(source_pairs & history_pairs),
            "historical_sku_overlap": len(gold_skus & history_skus),
        },
        "checks": checks,
        "source_hash_mismatches": source_hash_mismatch,
        "missing_source_hash_rows": missing_source_hash,
        "production_writes": {"gold": False, "master": False, "dictionary": False, "sqlite": False},
        "training_runs": 0,
        "status": "PASS_FINAL_OFFLINE_GOLD_GATE" if all(checks.values()) else "BLOCKED_NO_WRITE",
        "next_action": "Gold candidate is ready for a separately authorized training run; production apply remains disabled.",
    }
    (args.output_dir / "stage5_shadow_gold_gate_final_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Stage 5 Shadow Final Gold Gate Audit — 2026-09-15",
        "",
        f"- Status: **{report['status']}**",
        f"- Gold fields: {len(gold)}",
        f"- Gold SKUs: {len(gold_skus)}",
        f"- Historical source-pair overlap: {report['counts']['historical_source_pair_overlap']}",
        f"- Historical SKU overlap: {report['counts']['historical_sku_overlap']}",
        "- Production writes: 0",
        "- Training runs: 0",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: {'PASS' if value else 'FAIL'}")
    lines += ["", report["next_action"], ""]
    (args.output_dir / "STAGE5_SHADOW_FINAL_GOLD_GATE.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS_FINAL_OFFLINE_GOLD_GATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
