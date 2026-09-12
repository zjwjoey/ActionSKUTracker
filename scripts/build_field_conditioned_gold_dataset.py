"""Convert full-row gold data into field-conditioned SFT examples.

Each example exposes only one source field and asks the model to emit only
that field.  Split membership is preserved and numeric mismatches are rejected
instead of being silently promoted to training data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def nums(value: object) -> list[str]:
    return sorted(item.replace(",", ".") for item in NUMBER.findall(str(value or "")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("message content must be a JSON object")
    return value


def source_target(row: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    source = parse_object(next(item["content"] for item in row["messages"] if item["role"] == "user"))
    target = parse_object(next(item["content"] for item in row["messages"] if item["role"] == "assistant"))
    return source, target


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def convert_split(rows: list[dict[str, Any]], split: str) -> tuple[list[dict[str, Any]], Counter]:
    converted: list[dict[str, Any]] = []
    rejected: Counter = Counter()
    for row in rows:
        source, target = source_target(row)
        sku = str(row.get("metadata", {}).get("sku", "")).strip()
        source_hash = str(row.get("metadata", {}).get("source_hash", "")).strip()
        if not sku:
            rejected["missing_sku"] += 1
            continue
        for field in FIELDS:
            source_value = str(source.get(field, "") or "").strip()
            target_value = str(target.get(field, "") or "").strip()
            if not source_value or not target_value:
                rejected[f"missing_{field}"] += 1
                continue
            source_numbers = Counter(nums(source_value))
            target_numbers = Counter(nums(target_value))
            missing = list((source_numbers - target_numbers).elements())
            extra = list((target_numbers - source_numbers).elements())
            if missing:
                rejected[f"numeric_dropped_{field}"] += 1
                continue
            if extra:
                rejected[f"numeric_hallucinated_{field}"] += 1
                continue
            converted.append({
                "messages": [
                    {"role": "system", "content": f"只将 Action 西语 {field} 忠实标准化为简体中文。只输出 JSON 字段 {field}；不得新增、删除或从其他字段补入事实；严格保留数字、单位、否定关系、品牌和型号。"},
                    {"role": "user", "content": json.dumps({field: source_value}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({field: target_value}, ensure_ascii=False)},
                ],
                "metadata": {"sku": sku, "field": field, "source_hash": source_hash, "parent_split": split, "label_tier": "COMBINED_GOLD"},
            })
    return converted, rejected


def build_dataset(*, train_file: Path, validation_file: Path, test_file: Path, output_dir: Path) -> dict[str, Any]:
    inputs = {"train": train_file, "validation": validation_file, "test": test_file}
    all_rows: dict[str, list[dict[str, Any]]] = {}
    report_rejected: Counter = Counter()
    for split, path in inputs.items():
        converted, rejected = convert_split(load_rows(path), split)
        all_rows[split] = converted
        report_rejected.update({f"{split}:{key}": value for key, value in rejected.items()})

    seen: dict[tuple[str, str], str] = {}
    for split, rows in all_rows.items():
        for row in rows:
            key = (str(row["metadata"]["sku"]), str(row["metadata"]["field"]))
            if key in seen:
                raise ValueError(f"FIELD_SPLIT_OVERLAP: {key} in {seen[key]} and {split}")
            seen[key] = split

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for split, rows in all_rows.items():
        path = output_dir / f"field_conditioned_{split}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        outputs[split] = {"path": str(path.resolve()), "rows": len(rows), "sha256": sha256_file(path)}
    manifest = {
        "dataset_version": "QWEN3_ACTION_FIELD_CONDITIONED_V1",
        "status": "READY_FOR_OFFLINE_SMOKE_ONLY",
        "inputs": {split: {"path": str(path.resolve()), "sha256": sha256_file(path)} for split, path in inputs.items()},
        "outputs": outputs,
        "field_counts": {split: dict(Counter(row["metadata"]["field"] for row in rows)) for split, rows in all_rows.items()},
        "rejected": dict(sorted(report_rejected.items())),
        "overlap_check": "PASS",
        "training_policy": "Do not replace the current model or add to production; run only as a separately versioned offline experiment.",
    }
    (output_dir / "field_conditioned_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-file", type=Path, required=True)
    parser.add_argument("--validation-file", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_dataset(train_file=args.train_file, validation_file=args.validation_file, test_file=args.test_file, output_dir=args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
