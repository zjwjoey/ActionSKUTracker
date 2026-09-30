"""Freeze a deterministic 100-SKU blind holdout for Stage 6 Gold production.

This is a read-only data-factory step.  It never writes Master, SQLite, the
dictionary, or any training split.  The holdout is selected by a stable
SHA-256 ranking over ``(sku, source_hash)`` so the membership can be replayed
exactly from the immutable 500-row source candidate pool.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runtime/stage6/20260913"
SOURCE = BASE / "source_candidate_500/stage6_source_candidate_500.jsonl"
OUT = BASE / "holdout_freeze_100"
HOLDOUT_COUNT = 100


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if len(rows) != 500:
        raise RuntimeError(f"SOURCE_COUNT_INVALID:{len(rows)}")
    pairs = [(str(r["metadata"]["sku"]), str(r["metadata"]["source_hash"])) for r in rows]
    if len(set(pairs)) != len(pairs):
        raise RuntimeError("SOURCE_PAIR_DUPLICATE")
    return rows


def row_key(row: dict[str, Any]) -> str:
    meta = row["metadata"]
    return hashlib.sha256(
        f"{meta['sku']}|{meta['source_hash']}|STAGE6_BLIND_HOLDOUT_V1".encode("utf-8")
    ).hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    payload = b"".join((canonical(row) + "\n").encode("utf-8") for row in rows)
    path.write_bytes(payload)
    return sha_bytes(payload)


def main() -> int:
    rows = load_rows()
    ranked = sorted(rows, key=row_key)
    # Select 100 deterministic product families, then move every source
    # version/SKU belonging to those families into the holdout.  This keeps
    # both SKU and family boundaries disjoint; the resulting row count can be
    # larger than 100 because a family may have multiple source versions.
    selected_families: set[str] = set()
    selected_seed_skus: set[str] = set()
    for row in ranked:
        meta = row["metadata"]
        sku = str(meta["sku"])
        family = str(meta.get("family_key") or "")
        if sku in selected_seed_skus or (family and family in selected_families):
            continue
        selected_seed_skus.add(sku)
        selected_families.add(family)
        if len(selected_families) >= HOLDOUT_COUNT:
            break
    if len(selected_families) != HOLDOUT_COUNT:
        raise RuntimeError(f"HOLDOUT_FAMILY_COUNT_INVALID:{len(selected_families)}")
    holdout = sorted(
        [r for r in rows if str(r["metadata"].get("family_key") or "") in selected_families],
        key=lambda r: int(r["metadata"]["sku"]),
    )
    pool = sorted(
        [r for r in rows if str(r["metadata"].get("family_key") or "") not in selected_families],
        key=lambda r: int(r["metadata"]["sku"]),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    holdout_path = OUT / "stage6_blind_holdout_100.jsonl"
    pool_path = OUT / "stage6_training_pool_400.jsonl"
    holdout_hash = write_jsonl(holdout_path, holdout)
    pool_hash = write_jsonl(pool_path, pool)
    holdout_skus = {str(r["metadata"]["sku"]) for r in holdout}
    pool_skus = {str(r["metadata"]["sku"]) for r in pool}
    pool_families = {str(r["metadata"].get("family_key") or "") for r in pool}
    if holdout_skus & pool_skus or selected_families & pool_families:
        raise RuntimeError("HOLDOUT_SPLIT_INVALID")
    manifest = {
        "contract_id": "STAGE6_BLIND_HOLDOUT_V1",
        "source": str(SOURCE.resolve()),
        "source_sha256": sha_file(SOURCE),
        "selection_algorithm": "rank SHA256(sku|source_hash|STAGE6_BLIND_HOLDOUT_V1), select 100 families, include all family rows",
        "selected_family_count": len(selected_families),
        "holdout_count": len(holdout),
        "holdout_sku_count": len(holdout_skus),
        "training_pool_count": len(pool),
        "holdout_sha256": holdout_hash,
        "training_pool_sha256": pool_hash,
        "holdout_path": str(holdout_path.resolve()),
        "training_pool_path": str(pool_path.resolve()),
        "holdout_skus": sorted(holdout_skus, key=int),
        "holdout_families": sorted(selected_families),
        "production_writes": 0,
        "training_runs": 0,
        "owner_review": "PENDING",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (OUT / "holdout_index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "source_hash", "partition", "rank_key"])
        writer.writeheader()
        for row in ranked:
            meta = row["metadata"]
            writer.writerow({
                "sku": meta["sku"],
                "source_hash": meta["source_hash"],
                "partition": "BLIND_HOLDOUT" if str(meta.get("family_key") or "") in selected_families else "TRAINING_POOL",
                "rank_key": row_key(row),
            })
    print(json.dumps({k: manifest[k] for k in ("contract_id", "holdout_count", "training_pool_count", "source_sha256", "holdout_sha256", "training_pool_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
