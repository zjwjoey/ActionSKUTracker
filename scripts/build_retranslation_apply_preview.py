"""Build a gated Patch/Apply preview for a retranslation batch.

The command is intentionally safe: blank or invalid Owner decisions produce
an explicit ``AWAITING_OWNER_REVIEW`` preview with zero writes. It never
modifies Master/PRIMARY and never treats a candidate as approved implicitly.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


HEADERS = [
    "sku", "field_name", "before_zh", "after_zh", "change_type",
    "owner_decision", "source_hash", "old_target_hash", "new_target_hash",
    "conflict_reason", "apply_status",
]
VALID = {"APPROVE", "REJECT"}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()
    batch = Path(args.batch_dir).resolve()
    rows = read(batch / "owner_review_queue.csv")
    invalid = [
        f"{row.get('sku')}:{row.get('field_name')}"
        for row in rows
        if str(row.get("owner_decision") or "").strip().upper() not in VALID
    ]
    approved_patch: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    if not invalid:
        for row in rows:
            decision = str(row.get("owner_decision") or "").strip().upper()
            if decision == "REJECT":
                rejected.append(row)
                continue
            if row.get("field_status") != "REPLACE_READY" or row.get("ready_for_master") != "REVIEW_READY":
                continue
            before = str(row.get("old_zh") or "")
            after = str(row.get("candidate_zh") or "")
            if not after.strip() or not str(row.get("old_target_hash") or ""):
                continue
            approved_patch.append({
                "sku": row.get("sku", ""), "field_name": row.get("field_name", ""),
                "before_zh": before, "after_zh": after,
                "change_type": "REPLACE", "owner_decision": decision,
                "source_hash": row.get("source_hash", ""),
                "old_target_hash": row.get("old_target_hash", ""),
                "new_target_hash": hashlib.sha256(after.encode("utf-8")).hexdigest(),
                "conflict_reason": "", "apply_status": "PREVIEW_ONLY",
            })
    status = "AWAITING_OWNER_REVIEW" if invalid else "READY_FOR_CONFLICT_GATE"
    preview = approved_patch if not invalid else []
    diff = preview
    write(batch / "approved_patch.csv", approved_patch, HEADERS)
    write(batch / "apply_preview.csv", preview, HEADERS)
    write(batch / "master_diff.csv", diff, HEADERS)
    manifest = {
        "batch_id": rows[0].get("batch_id", batch.name) if rows else batch.name,
        "status": status,
        "owner_queue_rows": len(rows),
        "owner_decisions_missing": len(invalid),
        "owner_decision_invalid_keys": invalid[:100],
        "approved_patch_count": len(approved_patch),
        "rejected_count": len(rejected),
        "production_writes": False,
        "master_modified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (batch / "apply_preview_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
