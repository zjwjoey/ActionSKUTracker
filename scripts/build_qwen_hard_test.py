"""Build a deterministic, held-out Hard Test candidate set for Qwen."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
UNIT = re.compile(r"(?:°?C|°?F|kg|g|mg|ml|l|mm|cm|m²|m2|V|W|A|GB|TB|MHz|Hz|%)", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:no|sin|nunca|ningún|ninguna|no contiene|no incluye)\b", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_target(row: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    source = next(item["content"] for item in row["messages"] if item["role"] == "user")
    target = next(item["content"] for item in row["messages"] if item["role"] == "assistant")
    return json.loads(source), json.loads(target)


def row_sku(row: dict[str, Any]) -> str:
    return str(row.get("metadata", {}).get("sku", "")).strip()


def source_fingerprint(row: dict[str, Any]) -> str:
    source, _ = source_target(row)
    payload = json.dumps({field: str(source.get(field, "") or "") for field in FIELDS}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def row_source_hash(row: dict[str, Any]) -> str:
    return str(row.get("metadata", {}).get("source_hash", "")).strip()


def hard_score(row: dict[str, Any]) -> tuple[int, dict[str, int]]:
    source, _ = source_target(row)
    text = " ".join(str(source.get(field, "") or "") for field in FIELDS)
    numbers = len(NUMBER.findall(text))
    units = len(UNIT.findall(text))
    negations = len(NEGATION.findall(text))
    long_fields = sum(len(str(source.get(field, "") or "")) >= 120 for field in FIELDS)
    # Numeric and negation cases are intentionally weighted above length.
    parts = {"numbers": numbers, "units": units, "negations": negations, "long_fields": long_fields}
    return numbers * 5 + units * 3 + negations * 5 + long_fields, parts


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def build_hard_test(
    *, candidate_file: Path, exclusion_files: list[Path], output_file: Path,
    manifest_file: Path, limit: int = 250,
) -> dict[str, Any]:
    candidates = load_rows(candidate_file)
    excluded: set[str] = set()
    excluded_fingerprints: set[str] = set()
    excluded_source_hashes: set[str] = set()
    exclusion_hashes = {}
    for path in exclusion_files:
        rows = load_rows(path)
        values = {row_sku(row) for row in rows if row_sku(row)}
        fingerprints = {source_fingerprint(row) for row in rows if row_sku(row)}
        source_hashes = {row_source_hash(row) for row in rows if row_source_hash(row)}
        if excluded & values:
            raise ValueError(f"exclusion files overlap: {path}")
        if excluded_fingerprints & fingerprints:
            raise ValueError(f"exclusion source fingerprints overlap: {path}")
        if excluded_source_hashes & source_hashes:
            raise ValueError(f"exclusion source hashes overlap: {path}")
        excluded.update(values)
        excluded_fingerprints.update(fingerprints)
        excluded_source_hashes.update(source_hashes)
        exclusion_hashes[str(path.resolve())] = sha256_file(path)

    eligible = []
    seen = set()
    for row in candidates:
        sku = row_sku(row)
        fingerprint = source_fingerprint(row)
        source_hash = row_source_hash(row)
        if not sku or sku in excluded or fingerprint in excluded_fingerprints or source_hash in excluded_source_hashes or sku in seen:
            continue
        seen.add(sku)
        score, parts = hard_score(row)
        metadata = dict(row.get("metadata", {}))
        metadata.update({"hard_test_version": "QWEN3_ACTION_HARD_TEST_V1", "hard_test_status": "PENDING_MANUAL_REVIEW", "hard_test_score": score, "hard_test_score_parts": parts})
        prepared = dict(row)
        prepared["metadata"] = metadata
        eligible.append((score, sku, prepared))
    eligible.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in eligible[:limit]]
    if len(selected) < limit:
        raise ValueError(f"HARD_TEST_INSUFFICIENT_ELIGIBLE: {len(selected)} < {limit}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    selected_skus = [row_sku(row) for row in selected]
    sku_blob = "\n".join(sorted(selected_skus)).encode("utf-8")
    manifest = {
        "hard_test_version": "QWEN3_ACTION_HARD_TEST_V1",
        "status": "PENDING_MANUAL_REVIEW",
        "candidate_file": str(candidate_file.resolve()),
        "candidate_file_sha256": sha256_file(candidate_file),
        "exclusion_files": exclusion_hashes,
        "excluded_sku_count": len(excluded),
        "excluded_source_fingerprint_count": len(excluded_fingerprints),
        "excluded_source_hash_count": len(excluded_source_hashes),
        "rows": len(selected),
        "eligible_rows": len(eligible),
        "selected_sku_sha256": hashlib.sha256(sku_blob).hexdigest(),
        "selection": "descending deterministic hard_score, then SKU ascending",
        "hard_score_components": {"numbers": 5, "units": 3, "negations": 5, "long_fields": 1},
        "training_policy": "never add this file to training/validation; manual review required before freezing",
        "output_sha256": None,
    }
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["output_sha256"] = sha256_file(output_file)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--exclude", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=250)
    args = parser.parse_args()
    print(json.dumps(build_hard_test(candidate_file=args.candidate_file, exclusion_files=args.exclude, output_file=args.output, manifest_file=args.manifest, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
