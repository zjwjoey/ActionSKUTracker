"""Validate and split the first-pass Qwen JSONL candidates without leakage."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


NUM = re.compile(r"\d+(?:[.,]\d+)?")
SPANISH = re.compile(r"\b(?:de|del|la|el|los|las|para|con|sin|varios|varias|unidades?|piezas?|gramos?|metros?|litros?|colores?)\b", re.I)
PLACEHOLDER = "中文品名待人工核验"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _obj(record: dict, role: str) -> dict:
    return json.loads(next(m["content"] for m in record["messages"] if m["role"] == role))


def _numeric_tokens(value: str) -> list[str]:
    return [x.replace(",", ".") for x in NUM.findall(value or "")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-09-08")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = root / "runtime" / "training" / "qwen3_8b" / args.date.replace("-", "")
    source = next(base.glob("qwen_candidates_*.jsonl"))
    rows = [json.loads(line) for line in source.open(encoding="utf-8") if line.strip()]
    seen: set[str] = set()
    issues: Counter[str] = Counter()
    valid: list[dict] = []
    for row in rows:
        sku = str(row.get("metadata", {}).get("sku") or "").strip()
        if not sku or sku in seen:
            issues["duplicate_or_empty_sku"] += 1
            continue
        seen.add(sku)
        source_obj, target_obj = _obj(row, "user"), _obj(row, "assistant")
        row_issues: set[str] = set()
        if any(not str(source_obj.get(field) or "").strip() or not str(target_obj.get(field) or "").strip() for field in FIELDS):
            row_issues.add("missing_required_field")
        if any(str(target_obj.get(field) or "").strip() == PLACEHOLDER for field in FIELDS):
            row_issues.add("placeholder_target")
        if any(SPANISH.search(str(target_obj.get(field) or "")) for field in FIELDS):
            row_issues.add("spanish_residual")
        numeric_mismatch = any(
            sorted(_numeric_tokens(str(source_obj.get(field) or ""))) != sorted(_numeric_tokens(str(target_obj.get(field) or "")))
            for field in FIELDS
        )
        # Number differences are retained as a review flag rather than an
        # exclusion: descriptions may summarize sentences and decimal/thousand
        # separators legitimately change between Spanish and Chinese.
        if numeric_mismatch:
            row.setdefault("metadata", {})["numeric_consistency"] = "REVIEW"
        if row_issues:
            for issue in row_issues:
                issues[issue] += 1
            continue
        valid.append(row)

    # Stable hash split means reruns produce exactly the same partitions.
    buckets = {"train": [], "validation": [], "test": []}
    for row in sorted(valid, key=lambda item: item["metadata"]["sku"]):
        digest = int(hashlib.sha256(row["metadata"]["sku"].encode()).hexdigest()[:8], 16) % 100
        bucket = "test" if digest < 10 else ("validation" if digest < 20 else "train")
        buckets[bucket].append(row)
    for name, values in buckets.items():
        path = base / f"qwen_{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in values:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report = {
        "source": str(source), "input_rows": len(rows), "valid_rows": len(valid),
        "rejected_rows": len(rows) - len(valid), "rejection_reasons": dict(sorted(issues.items())),
        "numeric_review_flags": sum(1 for row in valid if row.get("metadata", {}).get("numeric_consistency") == "REVIEW"),
        "splits": {name: len(values) for name, values in buckets.items()},
        "sku_overlap": False, "validation": "PASS" if not issues else "PASS_WITH_REJECTIONS",
        "fields": list(FIELDS), "split_rule": "sha256(sku) mod 100: test<10, validation<20, train>=20",
    }
    (base / "dataset_validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
