"""Build the SKU-scoped Dictionary override preview from TM V1 Shadow assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

from action_tracker.knowledge.tm_adapter import build_product_override_candidates


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--termbase", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--updated-at", default="2026-09-15T00:00:00+08:00")
    args = parser.parse_args()
    termbase_path = args.termbase.resolve()
    staging_path = args.staging.resolve()
    out = args.output_dir.resolve()
    dictionary_dir = args.dictionary_dir.resolve()
    source_db = args.source_db.resolve()
    out.mkdir(parents=True, exist_ok=True)
    candidates, findings, counts = build_product_override_candidates(
        read_jsonl(termbase_path), read_jsonl(staging_path), updated_at=args.updated_at,
    )
    products = {}
    with (dictionary_dir / "product_dictionary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            products[str(row.get("sku") or "")] = row
    existing_overrides = {}
    with (dictionary_dir / "manual_overrides.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("scope") or "") == "product":
                existing_overrides[(str(row.get("key") or ""), str(row.get("field") or ""))] = str(row.get("value") or "")
    with sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True) as db:
        current_source_hashes = {
            str(row[0]): str(row[1] or "")
            for row in db.execute("SELECT official_sku, source_hash FROM product_localizations WHERE language='es'")
        }
    freshness_counts = {"SOURCE_HASH_MATCH": 0, "STALE_SOURCE_HASH": 0, "TARGET_SKU_MISSING": 0}
    gated_rows = []
    for row in candidates:
        current_hash = current_source_hashes.get(row.key)
        existing = existing_overrides.get((row.key, row.field))
        if current_hash is None:
            freshness = "TARGET_SKU_MISSING"
            action = "BLOCKED_STALE"
        elif current_hash != row.source_hash:
            freshness = "STALE_SOURCE_HASH"
            action = "BLOCKED_STALE"
        elif existing is not None and existing == row.value:
            freshness = "SOURCE_HASH_MATCH"
            action = "NO_CHANGE"
        elif existing is not None and existing != row.value:
            freshness = "SOURCE_HASH_MATCH"
            action = "CONFLICT_EXISTING_OVERRIDE"
        else:
            freshness = "SOURCE_HASH_MATCH"
            action = "WOULD_APPLY"
        freshness_counts[freshness] += 1
        gated_rows.append({**row.as_jsonable(), "source_hash_status": freshness, "apply_action": action})
    headers = ["scope", "key", "field", "value", "reason", "source", "locked", "updated_at", "source_hash", "tm_id", "shadow_scope", "source_hash_status", "apply_action"]
    with (out / "tm_v1_product_override_preview.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in gated_rows:
            writer.writerow(row)
    (out / "tm_v1_product_override_preview.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in gated_rows), encoding="utf-8",
    )
    (out / "tm_v1_product_override_findings.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in findings), encoding="utf-8",
    )
    manifest = {
        "artifact_type": "ACTION_TM_V1_PRODUCT_OVERRIDE_PREVIEW",
        "termbase_sha256": sha256_file(termbase_path),
        "staging_sha256": sha256_file(staging_path),
        "counts": {**counts, "freshness": freshness_counts, "apply_actions": {action: sum(1 for row in gated_rows if row["apply_action"] == action) for action in sorted({row["apply_action"] for row in gated_rows})}, "eligible_for_apply": sum(1 for row in gated_rows if row["apply_action"] == "WOULD_APPLY")},
        "dictionary_dir_sha256": {p.name: sha256_file(p) for p in sorted(dictionary_dir.iterdir()) if p.is_file()},
        "source_db_sha256": sha256_file(source_db),
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "status": "PASS" if counts["conflict_rows"] == 0 and freshness_counts["STALE_SOURCE_HASH"] == 0 and freshness_counts["TARGET_SKU_MISSING"] == 0 and not any(row["apply_action"] == "CONFLICT_EXISTING_OVERRIDE" for row in gated_rows) else "REVIEW",
        "apply_contract": "manual_overrides.csv schema preview only; current production gate remains closed",
    }
    (out / "tm_v1_product_override_preview_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Product Override Preview — 2026-09-15",
        "",
        "只生成 SKU 级产品覆盖预览，不修改正式 Dictionary。",
        "",
        f"- product override candidates: {counts['product_override_candidates']}",
        f"- shadow-only rows: {counts['shadow_only_rows']}",
        f"- conflict rows: {counts['conflict_rows']}",
        f"- source hash freshness: {freshness_counts}",
        f"- eligible for apply: {sum(1 for row in gated_rows if row['apply_action'] == 'WOULD_APPLY')}",
        f"- source TM rows: {counts['source_tm_rows']}",
        f"- status: {manifest['status']}",
    ]
    (out / "TM_V1_PRODUCT_OVERRIDE_PREVIEW.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
