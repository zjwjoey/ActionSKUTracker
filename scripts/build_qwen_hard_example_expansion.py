"""Build a read-only review queue from field-conditioned model hard errors.

The output is deliberately *not* a training file.  Every record remains pending
manual review until a reviewer confirms the correction and promotes it through
the normal gold-data pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from compare_qwen_baselines import load_rows, source_target

REASON_TO_TAXONOMY = {
    "NUMERIC_DROPPED": "NUMERIC_MISSING",
    "NUMERIC_HALLUCINATED": "NUMERIC_ADDED",
    "INVALID_CAT1": "CATEGORY_INVALID",
    "SPANISH_RESIDUAL": "SPANISH_RESIDUAL",
    "ENGLISH_RESIDUAL": "ENGLISH_RESIDUAL",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    metadata = row.get("metadata", {})
    return str(metadata.get("sku", "")).strip(), str(metadata.get("field", "")).strip()


def taxonomy_for(issue: dict[str, Any]) -> list[str]:
    reasons = [str(value) for value in issue.get("reasons", [])]
    return sorted({REASON_TO_TAXONOMY.get(reason, "UNCLASSIFIED") for reason in reasons})


def build_queue(
    *,
    benchmark_file: Path,
    test_file: Path,
    output_file: Path,
    manifest_file: Path,
    model_name: str = "field_conditioned_qlora",
    exclude_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Bind each model issue to its frozen test row and emit review-only JSONL."""
    report = json.loads(benchmark_file.read_text(encoding="utf-8"))
    model = next((item for item in report.get("models", []) if item.get("name") == model_name), None)
    if model is None:
        raise ValueError(f"MODEL_NOT_FOUND: {model_name}")

    rows = load_rows(test_file)
    row_by_key = {row_key(row): row for row in rows if row_key(row)[0] and row_key(row)[1]}
    excluded: set[tuple[str, str]] = set()
    exclusion_hashes: dict[str, str] = {}
    for path in exclude_files or []:
        exclusion_hashes[str(path.resolve())] = sha256_file(path)
        excluded.update(row_key(row) for row in load_rows(path) if row_key(row)[0] and row_key(row)[1])

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for issue in model.get("issues", []):
        sku = str(issue.get("sku", "")).strip()
        field = str(issue.get("field", "")).strip()
        key = (sku, field)
        if key not in row_by_key:
            raise ValueError(f"ISSUE_ROW_NOT_FOUND: {sku}/{field}")
        if key in excluded:
            raise ValueError(f"ISSUE_OVERLAPS_EXCLUDED_DATA: {sku}/{field}")
        taxonomies = taxonomy_for(issue)
        counts.update(taxonomies)
        source, expected = source_target(row_by_key[key])
        records.append({
            "review_id": f"QWEN3_ACTION_FIELD_CONDITIONED_V1:{sku}:{field}",
            "sku": sku,
            "field": field,
            "status": "PENDING_MANUAL_REVIEW",
            "source_es": str(source.get(field, "") or ""),
            "reference_zh": str(expected.get(field, "") or ""),
            "model_prediction": str(issue.get("prediction", "") or ""),
            "guard_reasons": [str(reason) for reason in issue.get("reasons", [])],
            "taxonomy": taxonomies,
            "missing_numbers": issue.get("missing_numbers", []),
            "extra_numbers": issue.get("extra_numbers", []),
            "human_decision": "",
            "training_promotion": "FORBIDDEN_UNTIL_HUMAN_CONFIRMED",
            "evidence": {
                "benchmark_file": str(benchmark_file.resolve()),
                "test_row_source_hash": row_by_key[key].get("metadata", {}).get("source_hash", ""),
            },
        })

    seen = set()
    for record in records:
        key = (record["sku"], record["field"])
        if key in seen:
            raise ValueError(f"DUPLICATE_REVIEW_KEY: {record['sku']}/{record['field']}")
        seen.add(key)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    manifest = {
        "queue_version": "QWEN3_ACTION_HARD_EXAMPLE_REVIEW_V1",
        "status": "PENDING_MANUAL_REVIEW",
        "model": model_name,
        "benchmark_file": str(benchmark_file.resolve()),
        "benchmark_sha256": sha256_file(benchmark_file),
        "test_file": str(test_file.resolve()),
        "test_sha256": sha256_file(test_file),
        "exclusion_files": exclusion_hashes,
        "rows": len(records),
        "taxonomy_counts": dict(sorted(counts.items())),
        "training_promotion": "FORBIDDEN_UNTIL_HUMAN_CONFIRMED",
        "master_write": "FORBIDDEN",
        "review_policy": "Human confirmation must write through the gold-data/manual-override pipeline; this queue is never consumed directly by training.",
        "output_sha256": None,
    }
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["output_sha256"] = sha256_file(output_file)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-file", type=Path, required=True)
    parser.add_argument("--test-file", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--manifest-file", type=Path, required=True)
    parser.add_argument("--model-name", default="field_conditioned_qlora")
    parser.add_argument("--exclude-file", type=Path, action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(build_queue(
        benchmark_file=args.benchmark_file,
        test_file=args.test_file,
        output_file=args.output_file,
        manifest_file=args.manifest_file,
        model_name=args.model_name,
        exclude_files=args.exclude_file,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
