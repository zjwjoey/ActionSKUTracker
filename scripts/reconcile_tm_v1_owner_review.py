"""Reconcile the Owner-reviewed stale TM queue into a read-only apply preview."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value - 1


def sheet_rows(book: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(book) as z:
        root = ET.fromstring(z.read(sheet_name))
    result: list[list[str]] = []
    for row in root.findall(".//x:row", NS):
        cells: dict[int, str] = {}
        for cell in row.findall("x:c", NS):
            value = cell.find("x:v", NS)
            cells[col_index(cell.attrib.get("r", "A1"))] = value.text if value is not None and value.text is not None else ""
        width = max(cells, default=-1) + 1
        result.append([cells.get(i, "") for i in range(width)])
    return result


def load_current(db_path: Path) -> dict[str, dict]:
    fields = ("name", "cat1", "cat2", "spec", "description", "details")
    query = "SELECT official_sku, name, cat1, cat2, spec, description, details, source_hash FROM product_localizations WHERE language='es'"
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as db:
        return {
            str(row[0]): {**dict(zip(fields, row[1:7])), "source_hash": str(row[7] or "")}
            for row in db.execute(query)
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-review", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    owner = args.owner_review.resolve()
    preview = args.preview.resolve()
    source_db = args.source_db.resolve()
    dictionary_dir = args.dictionary_dir.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    owner_rows = sheet_rows(owner, "xl/worksheets/sheet2.xml")
    if not owner_rows:
        raise SystemExit("owner review workbook sheet2 is empty")
    owner_header = owner_rows[0]
    owner_map = {row[3]: row for row in owner_rows[1:] if len(row) >= 26 and row[3]}
    required_headers = {"sku": "sku", "field": "field", "owner_decision": "owner_decision", "approved_value": "approved_value", "current_source_hash": "current_source_hash"}
    indices = {name: owner_header.index(column) for name, column in required_headers.items()}
    decisions = Counter(row[indices["owner_decision"]] for row in owner_rows[1:] if len(row) > indices["owner_decision"])
    if decisions != Counter({"APPROVE_REBASE": 295}):
        raise SystemExit(f"unexpected owner decisions: {decisions}")

    conflict_rows = sheet_rows(owner, "xl/worksheets/sheet3.xml")
    conflict_map: dict[tuple[str, str], dict] = {}
    for row in conflict_rows[1:]:
        if len(row) >= 7 and row[0] and row[1]:
            conflict_map[(row[0], row[1])] = {
                "decision": row[5],
                "approved_value": row[6],
                "note": row[7] if len(row) > 7 else "",
            }

    current = load_current(source_db)
    with preview.open("r", encoding="utf-8-sig", newline="") as handle:
        original = list(csv.DictReader(handle))
    owner_keys = {(row[indices["sku"]], row[indices["field"]]) for row in owner_rows[1:] if len(row) >= 26}
    stale_preview = {(row["key"], row["field"]): row for row in original if row["apply_action"] == "BLOCKED_STALE"}
    if owner_keys != set(stale_preview):
        raise SystemExit(f"owner/stale key mismatch: owner={len(owner_keys)} stale={len(stale_preview)}")

    output_rows: list[dict] = []
    for row in original:
        key = (row["key"], row["field"])
        sku = row["key"]
        facts = current.get(sku, {})
        owner_decision = ""
        owner_value = ""
        owner_note = ""
        if row["apply_action"] == "BLOCKED_STALE":
            # Key by (SKU, field) because one SKU has multiple reviewed fields.
            source_row = next(r for r in owner_rows[1:] if len(r) >= 26 and (r[indices["sku"]], r[indices["field"]]) == key)
            owner_decision = source_row[indices["owner_decision"]]
            owner_value = source_row[indices["approved_value"]]
            owner_note = source_row[25]
            current_hash = facts.get("source_hash", "")
            if current_hash != source_row[indices["current_source_hash"]]:
                action = "BLOCKED_STALE"
            else:
                action = "WOULD_APPLY"
            value = owner_value
            reason = f"tm_v1_owner_rebase:{row['tm_id']}"
        elif row["apply_action"] == "CONFLICT_EXISTING_OVERRIDE":
            review = conflict_map.get(key)
            if not review or not review["approved_value"]:
                action = "CONFLICT_EXISTING_OVERRIDE"
                value = row["value"]
            else:
                action = "WOULD_APPLY" if facts.get("source_hash", "") == row["source_hash"] else "BLOCKED_STALE"
                value = review["approved_value"]
                owner_decision = "APPROVE_REPLACE_EXISTING"
                owner_value = review["approved_value"]
                owner_note = review["note"]
            reason = f"tm_v1_owner_approved:{row['tm_id']}"
        else:
            action = row["apply_action"]
            value = row["value"]
            reason = row["reason"]
        output_rows.append({
            **row,
            "value": value,
            "reason": reason,
            "owner_decision": owner_decision,
            "owner_approved_value": owner_value,
            "owner_review_note": owner_note,
            "current_source_hash_rechecked": facts.get("source_hash", ""),
            "apply_action": action,
        })

    headers = list(output_rows[0]) if output_rows else []
    csv_path = out / "tm_v1_product_override_owner_reconciled_preview.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(output_rows)
    jsonl_path = out / "tm_v1_product_override_owner_reconciled_preview.jsonl"
    jsonl_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output_rows), encoding="utf-8")
    action_counts = Counter(row["apply_action"] for row in output_rows)
    manifest = {
        "artifact_type": "ACTION_TM_V1_OWNER_RECONCILED_PRODUCT_OVERRIDE_PREVIEW",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "owner_review_sha256": sha256_file(owner),
        "source_preview_sha256": sha256_file(preview),
        "source_db_sha256": sha256_file(source_db),
        "dictionary_dir_sha256": {p.name: sha256_file(p) for p in sorted(dictionary_dir.iterdir()) if p.is_file()},
        "source_hash_contract": "localization_source_hash_v1",
        "row_count": len(output_rows),
        "apply_actions": dict(sorted(action_counts.items())),
        "owner_rebase_approved": decisions["APPROVE_REBASE"],
        "owner_conflict_resolution_count": len(conflict_map),
        "current_source_hash_recheck_failures": sum(1 for row in output_rows if row["apply_action"] == "BLOCKED_STALE"),
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "status": "PASS" if action_counts.get("WOULD_APPLY", 0) == 527 and action_counts.get("NO_CHANGE", 0) == 1 and not action_counts.get("BLOCKED_STALE") and not action_counts.get("CONFLICT_EXISTING_OVERRIDE") else "REVIEW",
        "csv_sha256": sha256_file(csv_path),
        "jsonl_sha256": sha256_file(jsonl_path),
    }
    (out / "tm_v1_product_override_owner_reconciled_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Owner Reconciled Product Override Preview",
        "",
        "已吸收 Owner 对 295 条 stale 记录的审核，并处理 2574845/spec 的既有覆盖冲突。该产物仍为预览，不执行生产写入。",
        "",
        f"- rows: {manifest['row_count']}",
        f"- apply actions: {manifest['apply_actions']}",
        f"- owner stale approvals: {manifest['owner_rebase_approved']}",
        f"- current hash recheck failures: {manifest['current_source_hash_recheck_failures']}",
        f"- production writes: {manifest['production_writes']}",
        f"- status: {manifest['status']}",
    ]
    (out / "TM_V1_OWNER_RECONCILED_PRODUCT_OVERRIDE_PREVIEW.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
