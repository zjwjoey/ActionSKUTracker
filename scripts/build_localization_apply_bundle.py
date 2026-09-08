"""Build a deterministic, non-applying localization correction bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _db_rows(path: Path) -> dict[str, dict[str, object]]:
    with sqlite3.connect(path) as db:
        rows = db.execute(
            """SELECT p.official_sku,z.name,z.cat1,z.cat2,z.spec,z.description,z.details,
                      z.source_hash
               FROM products p LEFT JOIN product_localizations z
                 ON z.official_sku=p.official_sku AND z.language='zh'
               WHERE p.status='CURRENT' ORDER BY p.official_sku"""
        ).fetchall()
        head = db.execute(
            """SELECT commit_id FROM commit_batches WHERE status='COMMITTED'
                ORDER BY committed_at DESC,commit_id DESC LIMIT 1"""
        ).fetchone()
    result = {
        str(row[0]): {**{field: row[index] for index, field in enumerate(FIELDS, start=1)}, "source_hash": row[7]}
        for row in rows
    }
    return result, str(head[0]) if head else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(source: Path, candidate: Path) -> dict:
    before, base_commit_id = _db_rows(source)
    after, candidate_head = _db_rows(candidate)
    if set(before) != set(after):
        raise ValueError("APPLY_BUNDLE_SKU_SET_MISMATCH")
    localizations: dict[str, dict[str, object]] = {}
    field_counts = {field: 0 for field in FIELDS}
    for sku in sorted(before):
        changed = {
            field: after[sku].get(field)
            for field in FIELDS
            if before[sku].get(field) != after[sku].get(field)
        }
        if changed:
            localizations[sku] = changed
            for field in changed:
                field_counts[field] += 1
    hashes = {sku: str(after[sku].get("source_hash") or "") for sku in localizations}
    if any(not value for value in hashes.values()):
        raise ValueError("APPLY_BUNDLE_SOURCE_HASH_MISSING")
    payload = {
        "base_commit_id": base_commit_id,
        "candidate_head": candidate_head,
        "source_db": str(source),
        "candidate_db": str(candidate),
        "source_db_sha256": _sha256(source),
        "candidate_db_sha256": _sha256(candidate),
        "sku_count": len(localizations),
        "field_counts": field_counts,
        "localizations_by_sku": localizations,
        "source_hashes": hashes,
        "apply_authorized": False,
        "production_write": False,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    payload["bundle_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source_db, args.candidate_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("base_commit_id", "candidate_head", "sku_count", "field_counts", "bundle_sha256", "apply_authorized")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
