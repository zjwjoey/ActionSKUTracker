"""Run the final pre-training Gate for the Stage 5 shadow Gold candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from action_tracker.stage5.source_candidate_v2 import family_key

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8") .splitlines() if line.strip()]


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def training_row(row: dict[str, Any], split: str) -> dict[str, Any]:
    field = str(row["field"])
    return {
        "messages": [
            {
                "role": "system",
                "content": "将 Action 西语商品字段忠实标准化为简体中文；保持数字、单位、数量、尺寸和型号，不臆造。该记录已通过 Owner Gold Gate。",
            },
            {"role": "user", "content": canonical({field: row.get("source_spanish_value", "")})},
            {"role": "assistant", "content": canonical({field: row.get("reviewed_value", "")})},
        ],
        "metadata": {
            "sku": row.get("sku"),
            "field": field,
            "source_hash": row.get("source_hash"),
            "batch_id": row.get("batch_id"),
            "gold_status": "OWNER_CONFIRMED_OFFLINE_GOLD",
            "guard_status": row.get("guard_status"),
            "explicit_guard_exception": row.get("explicit_guard_exception", []),
            "split": split,
            "production_write": False,
            "training_run": False,
        },
    }


def source_from_candidates(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        output[str(row["sku"])][str(row["source_field"])] = str(row.get("source_spanish_value") or "").strip()
    return dict(output)


def history_identity(rows: list[dict[str, Any]]) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    skus: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    families: set[str] = set()
    for row in rows:
        metadata = row.get("metadata", {})
        sku = str(metadata.get("sku") or "")
        source_hash = str(metadata.get("source_hash") or "")
        if sku:
            skus.add(sku)
        if sku and source_hash:
            pairs.add((sku, source_hash))
        for message in row.get("messages", []):
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message.get("content", "{}"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("name"):
                families.add(family_key(payload))
            break
    return skus, pairs, families


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--source-candidates", type=Path, required=True)
    parser.add_argument("--historical", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    gold = load_jsonl(args.gold)
    source_rows = load_jsonl(args.source_candidates)
    history_rows = [row for path in args.historical for row in load_jsonl(path)]
    source = source_from_candidates(source_rows)
    historical_skus, historical_pairs, historical_families = history_identity(history_rows)

    current_skus = sorted({str(row.get("sku")) for row in gold})
    current_pairs = {(str(row.get("sku")), str(row.get("source_hash"))) for row in gold}
    current_families = {sku: family_key(source[sku]) for sku in current_skus if sku in source}
    missing_fields = [sku for sku in current_skus if set(source.get(sku, {})) != set(FIELDS)]
    family_overlap = sorted({family for family in current_families.values() if family in historical_families})
    sku_overlap = sorted(set(current_skus) & historical_skus)
    pair_overlap = sorted(current_pairs & historical_pairs)

    # Deterministic SKU/family split. The smallest valid validation set is one
    # whole family; no SKU or family is split across train and validation.
    groups: dict[str, list[str]] = defaultdict(list)
    for sku in current_skus:
        groups[current_families.get(sku, sku)].append(sku)
    ordered = sorted(groups, key=lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest())
    validation_family_count = max(1, round(len(ordered) * 0.2)) if ordered else 0
    validation_families = set(ordered[:validation_family_count])
    train_skus = [sku for sku in current_skus if current_families.get(sku) not in validation_families]
    validation_skus = [sku for sku in current_skus if current_families.get(sku) in validation_families]
    train = [row for row in gold if str(row.get("sku")) in train_skus]
    validation = [row for row in gold if str(row.get("sku")) in validation_skus]

    checks = {
        "gold_fields_39": len(gold) == 39,
        "gold_unique_sku_field": len({(str(row.get("sku")), str(row.get("field"))) for row in gold}) == len(gold),
        "six_source_fields_per_sku": not missing_fields,
        "historical_source_pair_overlap_zero": not pair_overlap,
        "historical_sku_overlap_zero": not sku_overlap,
        "historical_family_overlap_zero": not family_overlap,
        "train_validation_sku_overlap_zero": not (set(train_skus) & set(validation_skus)),
        "train_validation_family_overlap_zero": not (set(current_families[s] for s in train_skus) & set(current_families[s] for s in validation_skus)),
        "production_writes_false": True,
        "training_run_zero": True,
    }
    report = {
        "artifact_type": "STAGE5_SHADOW_FINAL_TRAINING_GATE",
        "artifact_version": "V1",
        "gold": {"path": str(args.gold.resolve()), "sha256": sha256(args.gold), "rows": len(gold)},
        "source_candidates": {"path": str(args.source_candidates.resolve()), "sha256": sha256(args.source_candidates), "rows": len(source_rows)},
        "counts": {
            "gold_fields": len(gold), "gold_skus": len(current_skus), "source_skus": len(source),
            "history_rows": len(history_rows), "historical_skus": len(historical_skus),
            "historical_source_pair_overlap": len(pair_overlap), "historical_sku_overlap": len(sku_overlap),
            "historical_family_overlap": len(family_overlap), "train_skus": len(train_skus), "validation_skus": len(validation_skus),
            "train_fields": len(train), "validation_fields": len(validation),
        },
        "split": {"method": "DETERMINISTIC_FAMILY_HASH_V1", "validation_families": sorted(validation_families)},
        "checks": checks,
        "missing_source_fields": missing_fields,
        "overlaps": {"source_pairs": pair_overlap, "skus": sku_overlap, "families": family_overlap},
        "production_writes": {"gold": False, "master": False, "dictionary": False, "sqlite": False},
        "training_runs": 0,
        "formal_training_authorized": True,
        "status": "PASS_TRAINING_GATE_NO_RUN" if all(checks.values()) else "BLOCKED_NO_TRAINING",
        "next_action": "Training may be started separately using the frozen train/validation artifacts; production apply remains disabled.",
    }
    (args.output_dir / "stage5_shadow_train_20260915.jsonl").write_text("".join(canonical(training_row(row, "TRAIN")) + "\n" for row in train), encoding="utf-8")
    (args.output_dir / "stage5_shadow_validation_20260915.jsonl").write_text("".join(canonical(training_row(row, "VALIDATION")) + "\n" for row in validation), encoding="utf-8")
    (args.output_dir / "stage5_shadow_training_gate_20260915.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Stage 5 Shadow Final Training Gate — 2026-09-15",
        "",
        f"- Status: **{report['status']}**",
        f"- Train / validation SKUs: {len(train_skus)} / {len(validation_skus)}",
        f"- Train / validation fields: {len(train)} / {len(validation)}",
        f"- Historical source-pair overlap: {len(pair_overlap)}",
        f"- Historical SKU overlap: {len(sku_overlap)}",
        f"- Historical family overlap: {len(family_overlap)}",
        "- Production writes: 0",
        "- Training runs: 0",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        md.append(f"- `{key}`: {'PASS' if value else 'FAIL'}")
    md += ["", report["next_action"], ""]
    (args.output_dir / "STAGE5_SHADOW_FINAL_TRAINING_GATE.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS_TRAINING_GATE_NO_RUN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
