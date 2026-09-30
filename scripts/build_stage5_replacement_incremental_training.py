"""Build an isolated field-conditioned corpus with the 10 approved replacements."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def nums(value: str) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUMBER.findall(value or ""))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def row_source_hash(row: dict) -> str:
    return str(row.get("source_hash") or "").strip()


def field_rows(row: dict) -> list[dict]:
    output = []
    for field in FIELDS:
        source = str(row.get(f"{field}_es") or "").strip()
        target = str(row.get(f"{field}_zh") or "").strip()
        if not source or not target:
            raise ValueError(f"MISSING_GOLD_FIELD:{row.get('sku')}:{field}")
        if nums(source) != nums(target):
            raise ValueError(f"NUMERIC_GUARD_FAILED:{row.get('sku')}:{field}:{nums(source)}!={nums(target)}")
        output.append({
            "messages": [
                {"role": "system", "content": f"只将 Action 西语 {field} 忠实标准化为简体中文。只输出 JSON 字段 {field}；不得新增、删除或从其他字段补入事实；严格保留数字、单位、否定关系、品牌和型号。"},
                {"role": "user", "content": json.dumps({field: source}, ensure_ascii=False)},
                {"role": "assistant", "content": json.dumps({field: target}, ensure_ascii=False)},
            ],
            "metadata": {"sku": str(row["sku"]).strip(), "field": field, "source_hash": row_source_hash(row), "parent_split": "replacement10", "label_tier": "REPLACEMENT_GOLD_OWNER_APPROVED"},
        })
    return output


def main(approved_csv: Path, old_train: Path, old_validation: Path, old_test: Path, output_dir: Path) -> dict:
    with approved_csv.open(encoding="utf-8-sig", newline="") as handle:
        approved = list(csv.DictReader(handle))
    if len(approved) != 10 or {row.get("owner_decision", "").strip().upper() for row in approved} != {"APPROVE"}:
        raise ValueError("APPROVED_REPLACEMENT_INPUT_MUST_BE_EXACTLY_10_APPROVED_ROWS")
    old_splits = {"train": load_jsonl(old_train), "validation": load_jsonl(old_validation), "test": load_jsonl(old_test)}
    historical_skus = {str(row.get("metadata", {}).get("sku", "")).strip() for rows in old_splits.values() for row in rows}
    new_skus = {str(row.get("sku", "")).strip() for row in approved}
    if historical_skus & new_skus:
        raise ValueError(f"SKU_OVERLAP_WITH_HISTORICAL_SPLITS:{sorted(historical_skus & new_skus)}")
    new_rows = [item for row in approved for item in field_rows(row)]
    output_dir.mkdir(parents=True, exist_ok=True)
    train = old_splits["train"] + new_rows
    outputs = {"train": train, "validation": old_splits["validation"], "test": old_splits["test"]}
    files = {}
    for split, rows in outputs.items():
        path = output_dir / f"field_conditioned_{split}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        files[split] = {"path": str(path.resolve()), "rows": len(rows), "sha256": sha256_file(path)}
    manifest = {
        "artifact": "QWEN3_ACTION_FIELD_CONDITIONED_REPLACEMENT10_V1",
        "status": "READY_FOR_SMOKE_THEN_OFFLINE_TRAINING",
        "approved_sku_count": 10,
        "new_field_rows": len(new_rows),
        "historical_train_rows": len(old_splits["train"]),
        "outputs": files,
        "sku_overlap_with_historical": 0,
        "production_writes": 0,
        "training_runs": 0,
        "base_model": "QWEN3_ACTION_FIELD_CONDITIONED_20260911_V1",
        "training_policy": "isolated revision only; do not replace production model",
    }
    (output_dir / "replacement10_training_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-csv", type=Path, required=True)
    parser.add_argument("--old-train", type=Path, required=True)
    parser.add_argument("--old-validation", type=Path, required=True)
    parser.add_argument("--old-test", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(main(args.approved_csv, args.old_train, args.old_validation, args.old_test, args.output_dir), ensure_ascii=False, indent=2))
