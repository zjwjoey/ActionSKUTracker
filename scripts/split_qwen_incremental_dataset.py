"""Create a leakage-safe train/validation/test split for approved incremental data."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", default="qwen_incremental")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = Path(args.input)
    if not source.is_absolute():
        source = root / source
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("EMPTY_INCREMENTAL_DATASET")
    skus = [str(row.get("metadata", {}).get("sku") or "").strip() for row in rows]
    if any(not sku for sku in skus) or len(set(skus)) != len(skus):
        raise SystemExit("EMPTY_OR_DUPLICATE_SKU")
    if any(row.get("metadata", {}).get("review_status") != "APPROVED_FOR_INCREMENTAL_SPLIT" for row in rows):
        raise SystemExit("UNAPPROVED_ROW_IN_TRAINING_INPUT")

    # Sort by a full SHA-256 key for stable pseudo-randomness, then allocate
    # explicit 10% validation and 10% test slices.  A modulo rule can be
    # accidentally skewed for a small, pre-filtered SKU population.
    ordered = sorted(
        rows,
        key=lambda item: hashlib.sha256(str(item["metadata"]["sku"]).encode("utf-8")).hexdigest(),
    )
    test_count = max(1, round(len(ordered) * 0.10))
    validation_count = max(1, round(len(ordered) * 0.10))
    buckets = {
        "test": ordered[:test_count],
        "validation": ordered[test_count : test_count + validation_count],
        "train": ordered[test_count + validation_count :],
    }

    paths = {}
    for bucket, values in buckets.items():
        path = output_dir / f"{args.prefix}_{bucket}.jsonl"
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in values)
            + ("\n" if values else ""),
            encoding="utf-8",
        )
        paths[bucket] = path
    overlap = set(str(row["metadata"]["sku"]) for row in buckets["train"]) & set(str(row["metadata"]["sku"]) for row in buckets["validation"])
    overlap |= set(str(row["metadata"]["sku"]) for row in buckets["train"]) & set(str(row["metadata"]["sku"]) for row in buckets["test"])
    overlap |= set(str(row["metadata"]["sku"]) for row in buckets["validation"]) & set(str(row["metadata"]["sku"]) for row in buckets["test"])
    report = {
        "source": str(source), "source_sha256": _sha256(source), "input_rows": len(rows),
        "splits": {key: len(value) for key, value in buckets.items()}, "sku_overlap": sorted(overlap),
        "fields": ["name", "cat1", "cat2", "spec", "description", "details"],
        "split_rule": "sort by sha256(sku), first 10% test, next 10% validation, remainder train",
        "sha256": {key: _sha256(path) for key, path in paths.items()},
        "validation": "PASS" if not overlap else "FAIL",
    }
    (output_dir / f"{args.prefix}_split_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
