"""Create a review-only detail translation queue from fetched Spanish facts.

This utility never calls a model and never changes SQLite, the production
dictionary, or an export.  It only projects fetched ``detail_fetch.jsonl``
records into the strict detail-training CSV schema; a reviewer must fill the
Chinese fields and approve the rows before the Qwen dataset builder accepts
them.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:  # works both as ``python scripts/...`` and ``python -m scripts...``
    from scripts.build_local_qwen_dataset import DETAIL_DICTIONARY_HEADERS
except ModuleNotFoundError:  # pragma: no cover - direct script fallback
    from build_local_qwen_dataset import DETAIL_DICTIONARY_HEADERS


def _read_fetch(path: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            detail = payload.get("detail") or {}
            sku = str(payload.get("sku") or detail.get("sku") or "").strip()
            if not sku or not isinstance(detail, dict):
                continue
            records[sku] = {str(key): "" if value is None else str(value).strip() for key, value in detail.items()}
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"DETAIL_FETCH_JSON_INVALID:{path}:{line_no}") from exc
    return records


def build_candidates(inputs: list[Path], output: Path, *, database: Path | None = None, source_run_id: str = "") -> dict[str, int | str]:
    details: dict[str, dict[str, str]] = {}
    for path in inputs:
        details.update(_read_fetch(path))
    hashes: dict[str, str] = {}
    if database and database.exists():
        with sqlite3.connect(database) as connection:
            for sku, source_hash in connection.execute("select official_sku, source_hash from products"):
                if sku and source_hash:
                    hashes[str(sku)] = str(source_hash)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for sku in sorted(details):
        detail = details[sku]
        rows.append({
            "sku": sku,
            "source_hash": hashes.get(sku, ""),
            "name_es_raw": detail.get("name_es", ""),
            "cat1_es": detail.get("cat1_es", ""),
            "cat2_es": detail.get("cat2_es", ""),
            "spec_es_raw": detail.get("spec_es", ""),
            "description_es_raw": detail.get("desc_es", ""),
            "description_zh_standard": "",
            "details_es_raw": detail.get("details_es", ""),
            "details_zh_standard": "",
            "review_status": "PENDING",
            "locked": "0",
            "source_run_id": source_run_id,
            "source_commit_id": "",
            "updated_at": now,
            "notes": "Fill Chinese fields and approve after human review; source-only candidate",
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DETAIL_DICTIONARY_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return {"output": str(output), "rows": len(rows), "with_source_hash": sum(bool(row["source_hash"]) for row in rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="detail_fetch.jsonl; may be repeated")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source-run-id", default="")
    args = parser.parse_args()
    print(json.dumps(build_candidates(args.input, args.output, database=args.database, source_run_id=args.source_run_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
