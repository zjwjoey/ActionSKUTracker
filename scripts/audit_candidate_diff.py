"""Read-only comparison of PRIMARY and an isolated localization candidate DB."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path) -> dict[str, dict[str, str | None]]:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            """SELECT p.official_sku,z.name,z.cat1,z.cat2,z.spec,z.description,z.details
               FROM products p LEFT JOIN product_localizations z
                 ON z.official_sku=p.official_sku AND z.language='zh'
               WHERE p.status='CURRENT' ORDER BY p.official_sku"""
        ).fetchall()
    return {
        str(row[0]): {field: row[index] for index, field in enumerate(FIELDS, start=1)}
        for row in rows
    }


def compare(source: Path, candidate: Path) -> dict:
    before = _rows(source)
    after = _rows(candidate)
    source_skus, candidate_skus = set(before), set(after)
    field_counts = {field: 0 for field in FIELDS}
    changed_skus: dict[str, list[str]] = {}
    for sku in sorted(source_skus & candidate_skus):
        changed = [field for field in FIELDS if before[sku].get(field) != after[sku].get(field)]
        if changed:
            changed_skus[sku] = changed
            for field in changed:
                field_counts[field] += 1
    return {
        "source_db": str(source),
        "candidate_db": str(candidate),
        "source_db_sha256": _sha256(source),
        "candidate_db_sha256": _sha256(candidate),
        "source_current_count": len(source_skus),
        "candidate_current_count": len(candidate_skus),
        "sku_set_added": sorted(candidate_skus - source_skus),
        "sku_set_removed": sorted(source_skus - candidate_skus),
        "changed_sku_count": len(changed_skus),
        "changed_field_counts": field_counts,
        "changed_skus": changed_skus,
        "read_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.source_db, args.candidate_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
