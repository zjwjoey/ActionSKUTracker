"""Build a read-only re-review queue for TM candidates with stale source hashes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


FIELD_TO_DB = {
    "name_zh_standard": "name",
    "cat1_zh": "cat1",
    "cat2_zh": "cat2",
    "spec_zh_standard": "spec",
}
SOURCE_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_current(db_path: Path) -> dict[str, dict]:
    query = "SELECT official_sku, name, cat1, cat2, spec, description, details, source_hash, updated_at FROM product_localizations WHERE language='es'"
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as db:
        return {
            str(row[0]): {
                **dict(zip(SOURCE_FIELDS, row[1:7])),
                "source_hash": str(row[7] or ""),
                "source_observed_at": row[8],
            }
            for row in db.execute(query)
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--termbase", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    preview = args.preview.resolve()
    termbase = args.termbase.resolve()
    source_db = args.source_db.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    tm_by_id = {str(row.get("tm_id") or ""): row for row in read_jsonl(termbase)}
    current = load_current(source_db)
    rows: list[dict] = []
    with preview.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("apply_action") != "BLOCKED_STALE":
                continue
            sku = str(row.get("key") or "")
            field = str(row.get("field") or "")
            facts = current.get(sku)
            tm = tm_by_id.get(str(row.get("tm_id") or ""), {})
            db_field = FIELD_TO_DB.get(field)
            rows.append({
                "queue_status": "REVIEW_REQUIRED",
                "reason": "SOURCE_HASH_CHANGED_REVIEW",
                "scope": row.get("scope", "product"),
                "sku": sku,
                "field": field,
                "db_field": db_field or "",
                "candidate_value": row.get("value", ""),
                "candidate_tm_id": row.get("tm_id", ""),
                "candidate_source_value": tm.get("source_value_raw", ""),
                "old_source_hash": row.get("source_hash", ""),
                "current_source_hash": (facts or {}).get("source_hash", ""),
                "source_hash_contract": "localization_source_hash_v1",
                "source_hash_status": row.get("source_hash_status", ""),
                "source_observed_at": (facts or {}).get("source_observed_at", ""),
                "target_exists": bool(facts),
                "current_source_value": (facts or {}).get(db_field, "") if db_field else "",
                "current_name_es": (facts or {}).get("name", ""),
                "current_cat1_es": (facts or {}).get("cat1", ""),
                "current_cat2_es": (facts or {}).get("cat2", ""),
                "current_spec_es": (facts or {}).get("spec", ""),
                "current_description_es": (facts or {}).get("description", ""),
                "current_details_es": (facts or {}).get("details", ""),
                "review_note": "源字段已变化；必须重新确认 source 与中文值，禁止沿用旧候选直接 Apply。",
            })

    headers = list(rows[0]) if rows else [
        "queue_status", "reason", "scope", "sku", "field", "db_field", "candidate_value",
        "candidate_tm_id", "candidate_source_value", "old_source_hash", "current_source_hash",
        "source_hash_contract", "source_hash_status", "source_observed_at", "target_exists",
        "current_source_value", "current_name_es", "current_cat1_es", "current_cat2_es",
        "current_spec_es", "current_description_es", "current_details_es", "review_note",
    ]
    csv_path = out / "tm_v1_stale_rebase_queue.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    jsonl_path = out / "tm_v1_stale_rebase_queue.jsonl"
    jsonl_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    counts = Counter(row["field"] for row in rows)
    manifest = {
        "artifact_type": "ACTION_TM_V1_STALE_REBASE_QUEUE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preview_sha256": sha256_file(preview),
        "termbase_sha256": sha256_file(termbase),
        "source_db_sha256": sha256_file(source_db),
        "source_hash_contract": "localization_source_hash_v1",
        "row_count": len(rows),
        "field_counts": dict(sorted(counts.items())),
        "target_missing_count": sum(1 for row in rows if not row["target_exists"]),
        "old_current_hash_equal_count": sum(1 for row in rows if row["old_source_hash"] == row["current_source_hash"]),
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "status": "PASS" if rows and all(row["old_source_hash"] != row["current_source_hash"] and row["target_exists"] for row in rows) else "REVIEW",
        "csv_sha256": sha256_file(csv_path),
        "jsonl_sha256": sha256_file(jsonl_path),
    }
    (out / "tm_v1_stale_rebase_queue_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Stale Rebase Queue",
        "",
        "该队列只用于重新核对源版本变化的候选，禁止直接 Apply。",
        "",
        f"- rows: {manifest['row_count']}",
        f"- fields: {manifest['field_counts']}",
        f"- target missing: {manifest['target_missing_count']}",
        f"- old/current hash unexpectedly equal: {manifest['old_current_hash_equal_count']}",
        f"- production writes: {manifest['production_writes']}",
        f"- status: {manifest['status']}",
    ]
    (out / "TM_V1_STALE_REBASE_QUEUE.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
