"""Prepare the Stage 6 owner-approved training pool after all holdout gates.

This is an offline data-packaging step.  It never writes Master, SQLite, the
dictionary, or production state.  The frozen Stage 6 holdout and all prior
validation/test/frozen-test SKU families are excluded before training.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(r"F:\ActionSKUTracker")
GOLD = ROOT / "runtime/stage6/20260913/owner_review_ingest_v2/stage6_owner_gold_eligible.jsonl"
SOURCE = ROOT / "runtime/stage6/20260913/source_candidate_500/stage6_source_candidate_500.jsonl"
HOLDOUT = ROOT / "runtime/stage6/20260913/holdout_freeze_100/stage6_blind_holdout_100.jsonl"
OUT = ROOT / "runtime/stage6/20260914/training_candidate_256"
HISTORICAL_EVAL = [
    ROOT / "runtime/training/qwen3_8b/20260908/qwen_gold_clean_validation.jsonl",
    ROOT / "runtime/training/qwen3_8b/20260908/qwen_gold_clean_test.jsonl",
    ROOT / "runtime/training/qwen3_8b/20260910/incremental_488/qwen_incremental_validation.jsonl",
    ROOT / "runtime/training/qwen3_8b/20260910/incremental_488/qwen_incremental_test.jsonl",
    ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl",
]
EVAL_SOURCE = ROOT / "runtime/training/qwen3_8b/20260911/combined_gold_incremental/qwen_combined_gold_incremental_validation.jsonl"


def jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def source_from_row(row: dict) -> dict[str, str]:
    for message in row.get("messages", []):
        if message.get("role") == "user":
            try:
                value = json.loads(message.get("content", ""))
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def family_key(source: dict[str, str]) -> str:
    text = unicodedata.normalize("NFKD", str(source.get("name") or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|mg|cm|m|mm|uds?|unidades?|metros?|litros?|gramos?)\b",
        " ",
        text,
    )
    text = re.sub(r"\b(?:varios|diferentes|distintos)\s+(?:colores|variantes)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    gold = list(jsonl(GOLD))
    source_meta = {
        f"{row['metadata'].get('sku')}|{row['metadata'].get('source_hash')}": row["metadata"]
        for row in jsonl(SOURCE)
    }
    holdout_pairs = {
        f"{row['metadata'].get('sku')}|{row['metadata'].get('source_hash')}"
        for row in jsonl(HOLDOUT)
    }
    blocked_skus: set[str] = set()
    blocked_families: set[str] = set()
    for path in HISTORICAL_EVAL:
        for row in jsonl(path):
            metadata = row.get("metadata") or {}
            sku = str(metadata.get("sku") or "").strip()
            if sku:
                blocked_skus.add(sku)
            family = family_key(source_from_row(row))
            if family:
                blocked_families.add(family)

    OUT.mkdir(parents=True, exist_ok=True)
    selected: list[dict] = []
    audit_rows: list[dict[str, str]] = []
    for row in gold:
        sku = str(row.get("sku") or "").strip()
        pair = f"{sku}|{row.get('source_hash')}"
        metadata = source_meta.get(pair, {})
        family = str(metadata.get("family_key") or "")
        reasons: list[str] = []
        if pair in holdout_pairs:
            reasons.append("STAGE6_BLIND_HOLDOUT")
        if sku in blocked_skus:
            reasons.append("HISTORICAL_EVAL_OR_FROZEN_SKU")
        if family and family in blocked_families:
            reasons.append("HISTORICAL_EVAL_OR_FROZEN_FAMILY")
        status = "TRAINING_ELIGIBLE" if not reasons else "HELD_OUT"
        audit_rows.append(
            {
                "sku": sku,
                "source_hash": str(row.get("source_hash") or ""),
                "family_key": family,
                "status": status,
                "reasons": ";".join(reasons),
            }
        )
        if status == "TRAINING_ELIGIBLE":
            selected.append(row)

    train_path = OUT / "stage6_training_256.jsonl"
    with train_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    # The Trainer's JSON loader requires identical columns across train/eval.
    # Keep provenance in the original file above, but feed only the frozen chat
    # messages to the model so audit metadata cannot affect schema inference.
    train_messages_path = OUT / "stage6_training_256_messages.jsonl"
    with train_messages_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in selected:
            messages = [
                {
                    "role": "system",
                    "content": "将 Action 西语商品六字段忠实标准化为中文；保持数字、单位、数量和品牌/型号，不臆造。",
                },
                {"role": "user", "content": json.dumps(row["source"], ensure_ascii=False, sort_keys=True)},
                {"role": "assistant", "content": json.dumps(row["proposed_zh"], ensure_ascii=False, sort_keys=True)},
            ]
            handle.write(json.dumps({"messages": messages}, ensure_ascii=False, separators=(",", ":")) + "\n")
    eval_messages_path = OUT / "stage6_validation_messages.jsonl"
    with EVAL_SOURCE.open(encoding="utf-8") as source_handle, eval_messages_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for line in source_handle:
            if line.strip():
                row = json.loads(line)
                handle.write(json.dumps({"messages": row["messages"]}, ensure_ascii=False, separators=(",", ":")) + "\n")
    with (OUT / "stage6_training_candidate_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "source_hash", "family_key", "status", "reasons"])
        writer.writeheader()
        writer.writerows(audit_rows)

    holdout_count = sum(1 for row in audit_rows if "STAGE6_BLIND_HOLDOUT" in row["reasons"])
    historical_blocked_count = sum(
        1
        for row in audit_rows
        if "HISTORICAL_EVAL_OR_FROZEN" in row["reasons"]
        and "STAGE6_BLIND_HOLDOUT" not in row["reasons"]
    )
    manifest = {
        "contract_id": "STAGE6_TRAINING_CANDIDATE_V1",
        "gold_input_rows": len(gold),
        "blind_holdout_rows": holdout_count,
        "historical_eval_or_frozen_blocked_non_holdout_rows": historical_blocked_count,
        "training_rows": len(selected),
        "unique_training_pairs": len({(row["sku"], row["source_hash"]) for row in selected}),
        "unique_training_skus": len({row["sku"] for row in selected}),
        "unique_training_families": len(
            {str(source_meta[f"{row['sku']}|{row['source_hash']}"].get('family_key') or '') for row in selected}
        ),
        "source_gold_sha256": sha256(GOLD),
        "holdout_sha256": sha256(HOLDOUT),
        "training_sha256": sha256(train_path),
        "training_messages_sha256": sha256(train_messages_path),
        "validation_messages_sha256": sha256(eval_messages_path),
        "validation_rows": sum(1 for _ in jsonl(EVAL_SOURCE)),
        "training_messages_path": str(train_messages_path),
        "validation_messages_path": str(eval_messages_path),
        "historical_eval_paths": [str(path) for path in HISTORICAL_EVAL],
        "production_writes": 0,
        "training_runs": 0,
        "status": "READY_FOR_TRAINING",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "audit.md").write_text(
        "# Stage 6 Training Candidate Audit\n\n"
        f"- Gold input: {len(gold)} source-version pairs\n"
        f"- Frozen Stage 6 holdout: {manifest['blind_holdout_rows']} rows\n"
        f"- Historical validation/test/frozen-family blocked outside Stage 6 holdout: {historical_blocked_count} rows\n"
        f"- Training candidates: {len(selected)} rows\n"
        "- Production writes: 0\n"
        "- Training runs before this package: 0\n\n"
        "The training file contains only owner-approved rows outside every frozen/evaluation SKU and family boundary.\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
