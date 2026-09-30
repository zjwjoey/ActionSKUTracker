"""Stage source-field restoration without mutating the production dictionary."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from action_tracker.exporting.dictionary_join import _fact_source_hash


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def stage(dictionary: Path, preview: Path, output_dir: Path) -> dict[str, object]:
    products = {}
    with (dictionary / "product_dictionary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            products[_text(row.get("sku"))] = row
    source_rows = list(csv.DictReader(preview.open("r", encoding="utf-8-sig", newline="")))
    rows = []
    for source in source_rows:
        sku = _text(source.get("sku"))
        current = products.get(sku)
        if not current:
            continue
        restored = dict(current)
        restored.update({
            "name_es_raw": _text(source.get("name_es_raw")),
            "cat1_es": _text(source.get("cat1_es")),
            "cat2_es": _text(source.get("cat2_es")),
            "spec_es_raw": _text(source.get("spec_es_raw")),
        })
        rows.append({
            "sku": sku,
            "source_file": _text(source.get("source_file")),
            "old_source_hash": _text(current.get("source_hash")),
            "new_source_hash": _fact_source_hash(restored),
            "name_es_raw_old": _text(current.get("name_es_raw")),
            "name_es_raw_new": restored["name_es_raw"],
            "cat1_es_old": _text(current.get("cat1_es")),
            "cat1_es_new": restored["cat1_es"],
            "cat2_es_old": _text(current.get("cat2_es")),
            "cat2_es_new": restored["cat2_es"],
            "spec_es_raw_old": _text(current.get("spec_es_raw")),
            "spec_es_raw_new": restored["spec_es_raw"],
            "translation_status": _text(current.get("translation_status")),
            "review_status": _text(current.get("review_status")),
            "staging_action": "RESTORE_SOURCE_ONLY",
            "production_write": "false",
        })
    rows.sort(key=lambda row: row["sku"])
    output_dir.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["sku"]
    csv_path = output_dir / "source_restoration_staging.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "queue_type": "DICTIONARY_SOURCE_RESTORATION_STAGING_V1",
        "source_preview": str(preview),
        "dictionary": str(dictionary / "product_dictionary.csv"),
        "staged_sku_count": len(rows),
        "production_writes": 0,
        "status": "STAGED_NOT_APPLIED",
        "csv": str(csv_path),
    }
    (output_dir / "source_restoration_staging_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dictionary", default="runtime/dictionary")
    parser.add_argument("--preview", default="runtime/dictionary/owner_review_20260915/source_restoration_preview.csv")
    parser.add_argument("--output-dir", default="runtime/dictionary/owner_review_20260915")
    args = parser.parse_args()
    print(json.dumps(stage(Path(args.dictionary), Path(args.preview), Path(args.output_dir)), ensure_ascii=False, indent=2))
