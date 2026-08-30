"""Read-only audit of the live SQLite PRIMARY Knowledge state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from action_tracker.database.connection import connect


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def audit(db_path: Path) -> dict:
    with connect(db_path) as db:
        role = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
        current = db.execute("SELECT count(*) FROM products WHERE status='CURRENT'").fetchone()[0]
        loc = db.execute("SELECT count(*) FROM product_localizations WHERE language='zh'").fetchone()[0]
        field_coverage = {f: db.execute(f"SELECT count(*) FROM product_localizations WHERE language='zh' AND nullif(trim({f}),'') IS NOT NULL").fetchone()[0] for f in FIELDS}
        freshness = {str(row[0] or "NULL"): row[1] for row in db.execute("SELECT freshness_status,count(*) FROM product_localizations WHERE language='zh' GROUP BY freshness_status")}
        provenance = {str(row[0] or "NULL"): row[1] for row in db.execute("SELECT coalesce(name_source,'NULL'),count(*) FROM product_localizations WHERE language='zh' GROUP BY coalesce(name_source,'NULL')")}
        queue = {str(row[0]): row[1] for row in db.execute("SELECT status,count(*) FROM translation_queue GROUP BY status")}
        candidates = {str(row[0]): row[1] for row in db.execute("SELECT validation_status,count(*) FROM translation_candidates GROUP BY validation_status")}
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [tuple(row) for row in db.execute("PRAGMA foreign_key_check").fetchall()]
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "database": str(db_path),
                "database_role": role[0] if role else None, "current_count": current,
                "formal_zh_rows": loc, "field_coverage": field_coverage, "freshness": freshness,
                "name_provenance": provenance, "queue": queue, "candidates": candidates,
                "integrity_check": integrity, "foreign_key_errors": fk,
                "high": 0, "medium": 0, "live_provider_validated": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.db)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
