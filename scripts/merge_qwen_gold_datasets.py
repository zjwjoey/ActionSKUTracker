"""Build a deterministic, leakage-checked Qwen training corpus.

This tool combines two *already split* reviewed corpora without reassigning
any SKU.  Keeping the original train/validation/test membership protects the
old held-out gold records and the independently reviewed incremental holdout.
It only writes an isolated runtime training artifact and never touches Master,
SQLite, dictionaries, or review queues.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for entry in (ROOT, SRC):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from action_tracker.services.hashing import localization_source_hash


SPLITS = ("train", "validation", "test")
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalise(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _message_object(row: dict[str, Any], role: str) -> dict[str, Any]:
    matches = [message.get("content") for message in row.get("messages", []) if message.get("role") == role]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError(f"INVALID_{role.upper()}_MESSAGE")
    try:
        value = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"INVALID_{role.upper()}_JSON") from exc
    if not isinstance(value, dict) or set(value) != set(FIELDS) or any(not isinstance(value[field], str) for field in FIELDS):
        raise ValueError(f"INVALID_{role.upper()}_SCHEMA")
    return value


def source_hash(source: dict[str, Any]) -> str:
    return localization_source_hash({
        "name_es": source["name"],
        "cat1_es": source["cat1"],
        "cat2_es": source["cat2"],
        "spec_es": source["spec"],
        "desc_es": source["description"],
        "details_es": source["details"],
    })


def row_identity(row: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    """Validate a reviewed full-record row and return safe leakage keys."""

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("MISSING_METADATA")
    sku = str(metadata.get("sku") or "").strip()
    if not sku:
        raise ValueError("EMPTY_SKU")
    source = _message_object(row, "user")
    target = _message_object(row, "assistant")
    declared_hash = str(metadata.get("source_hash") or "").strip()
    recomputed_hash = source_hash(source)
    if declared_hash != recomputed_hash:
        raise ValueError(f"SOURCE_HASH_MISMATCH:{sku}")
    # The free-text fingerprint deliberately excludes closed-set categories.
    # Repeated category translations are expected and must not look like
    # leakage; repeated product text across different splits is not allowed.
    fingerprint = tuple(
        _normalise(source[field]) + "\u241f" + _normalise(target[field])
        for field in ("name", "spec", "description", "details")
    )
    return sku, recomputed_hash, fingerprint


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cross_split_overlap(values: dict[str, set[Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for index, left in enumerate(SPLITS):
        for right in SPLITS[index + 1 :]:
            shared = values[left] & values[right]
            if shared:
                result[f"{left}__{right}"] = sorted(map(str, shared))
    return result


def combine_rows(
    baseline: dict[str, list[dict[str, Any]]],
    incremental: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Merge fixed split memberships and fail closed on provenance/leakage."""

    combined: dict[str, list[dict[str, Any]]] = {}
    sku_sets: dict[str, set[str]] = {}
    hash_sets: dict[str, set[str]] = {}
    text_sets: dict[str, set[tuple[str, ...]]] = {}
    duplicate_skus: list[str] = []
    duplicate_hashes_within_split: dict[str, int] = {}
    duplicate_text_within_split: dict[str, int] = {}
    for split in SPLITS:
        rows = list(baseline[split]) + list(incremental[split])
        identities = [row_identity(row) for row in rows]
        skus = [value[0] for value in identities]
        hashes = [value[1] for value in identities]
        texts = [value[2] for value in identities]
        repeated_skus = sorted(sku for sku, count in Counter(skus).items() if count > 1)
        if repeated_skus:
            duplicate_skus.extend(f"{split}:{sku}" for sku in repeated_skus)
        sku_sets[split] = set(skus)
        hash_sets[split] = set(hashes)
        text_sets[split] = set(texts)
        duplicate_hashes_within_split[split] = len(hashes) - len(hash_sets[split])
        duplicate_text_within_split[split] = len(texts) - len(text_sets[split])
        # Stable ordering makes the artifact byte-reproducible; Trainer still
        # shuffles examples by its separately recorded random seed.
        combined[split] = [row for _, row in sorted(zip(identities, rows), key=lambda item: (item[0][1], item[0][0]))]

    overlaps = {
        "sku": cross_split_overlap(sku_sets),
        "source_hash": cross_split_overlap(hash_sets),
        "free_text_fingerprint": cross_split_overlap(text_sets),
    }
    errors = {
        "duplicate_skus_within_split": duplicate_skus,
        "cross_split_overlap": overlaps,
    }
    valid = not duplicate_skus and not any(overlaps[kind] for kind in overlaps)
    report = {
        "splits": {split: len(combined[split]) for split in SPLITS},
        "unique_skus": {split: len(sku_sets[split]) for split in SPLITS},
        "duplicate_source_hashes_within_split": duplicate_hashes_within_split,
        "duplicate_free_text_fingerprints_within_split": duplicate_text_within_split,
        "leakage": errors,
        "validation": "PASS" if valid else "FAIL",
    }
    if not valid:
        raise ValueError(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return combined, report


def _resolve(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge reviewed Qwen corpora without changing split membership.")
    parser.add_argument("--baseline-dir", default="runtime/training/qwen3_8b/20260908")
    parser.add_argument("--baseline-prefix", default="qwen_gold_clean")
    parser.add_argument("--incremental-dir", default="runtime/training/qwen3_8b/20260910/incremental_488")
    parser.add_argument("--incremental-prefix", default="qwen_incremental")
    parser.add_argument("--output-dir", default="runtime/training/qwen3_8b/20260911/combined_gold_incremental")
    parser.add_argument("--prefix", default="qwen_combined_gold_incremental")
    args = parser.parse_args()

    baseline_dir = _resolve(ROOT, args.baseline_dir)
    incremental_dir = _resolve(ROOT, args.incremental_dir)
    output_dir = _resolve(ROOT, args.output_dir)
    input_paths = {
        "baseline": {split: baseline_dir / f"{args.baseline_prefix}_{split}.jsonl" for split in SPLITS},
        "incremental": {split: incremental_dir / f"{args.incremental_prefix}_{split}.jsonl" for split in SPLITS},
    }
    missing = [str(path) for group in input_paths.values() for path in group.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("MISSING_INPUT_FILES:\n" + "\n".join(missing))

    baseline = {split: load_jsonl(path) for split, path in input_paths["baseline"].items()}
    incremental = {split: load_jsonl(path) for split, path in input_paths["incremental"].items()}
    combined, validation = combine_rows(baseline, incremental)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, Path] = {}
    for split, rows in combined.items():
        path = output_dir / f"{args.prefix}_{split}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        output_paths[split] = path

    manifest = {
        "artifact_type": "QWEN_COMBINED_REVIEWED_CORPUS",
        "artifact_version": 1,
        "split_policy": "Preserve each reviewed source corpus split; never repartition or move a held-out SKU.",
        "fields": list(FIELDS),
        "inputs": {
            group: {
                split: {"path": str(path), "rows": len((baseline if group == "baseline" else incremental)[split]), "sha256": sha256_file(path)}
                for split, path in paths.items()
            }
            for group, paths in input_paths.items()
        },
        "outputs": {
            split: {"path": str(path), "rows": len(combined[split]), "sha256": sha256_file(path)}
            for split, path in output_paths.items()
        },
        "validation": validation,
    }
    manifest_path = output_dir / f"{args.prefix}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
