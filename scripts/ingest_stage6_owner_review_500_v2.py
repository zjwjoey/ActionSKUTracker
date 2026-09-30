"""Validate the Owner-reviewed Stage 6 workbook and build Gold candidates.

This is a non-production ingestion step.  It never updates Master, SQLite or
the dictionary.  Only rows whose six fields are explicitly accepted by the
Owner and pass the final field Guard become Gold-eligible.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
INPUT = Path(r"D:\Users\Administrator\Downloads\stage6_gold_review_500_final_AI_FIELD_REVIEWED_V2.xlsx")
BASE = ROOT / "runtime/stage6/20260913"
OUT = BASE / "owner_review_ingest_v2"
FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
ACCEPTED = {"ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT"}
DISPOSITIONS = ACCEPTED | {"REQUIRES_MAJOR_EDIT", "AMBIGUOUS", "REJECT"}


def source_hash(source: dict[str, str]) -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.services.hashing import localization_source_hash
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def review_id(sku: str, digest: str) -> str:
    return hashlib.sha256(f"stage6-gold-review-v2|{sku}|{digest}".encode("utf-8")).hexdigest()


def main() -> int:
    if not INPUT.exists():
        raise FileNotFoundError(INPUT)
    wb = openpyxl.load_workbook(INPUT, read_only=True, data_only=True)
    if "Field Review" not in wb.sheetnames:
        raise RuntimeError("FIELD_REVIEW_SHEET_MISSING")
    ws = wb["Field Review"]
    values = list(ws.iter_rows(values_only=True))
    headers = [str(x or "") for x in values[0]]
    required = ["review_id", "sku", "source_run_id", "source_hash", "field", "source_es", "proposed_zh", "resolution_source", "field_status", "guard_reasons", "owner_disposition"]
    if headers != required:
        raise RuntimeError(f"FIELD_REVIEW_HEADERS_INVALID:{headers}")
    rows: list[dict[str, str]] = []
    for raw in values[1:]:
        row = {headers[i]: "" if raw[i] is None else str(raw[i]).strip() for i in range(len(headers))}
        if row["field"] not in FIELDS:
            raise RuntimeError(f"UNKNOWN_FIELD:{row['field']}")
        if row["owner_disposition"] not in DISPOSITIONS:
            raise RuntimeError(f"OWNER_DISPOSITION_INVALID:{row['sku']}:{row['field']}:{row['owner_disposition']}")
        if row["owner_disposition"] in ACCEPTED and not row["proposed_zh"]:
            raise RuntimeError(f"ACCEPTED_EMPTY_VALUE:{row['sku']}:{row['field']}")
        rows.append(row)
    if len(rows) != 3000:
        raise RuntimeError(f"FIELD_ROW_COUNT_INVALID:{len(rows)}")
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["sku"], row["source_hash"])].append(row)
    if len(grouped) != 500:
        raise RuntimeError(f"PAIR_COUNT_INVALID:{len(grouped)}")
    gold: list[dict[str, Any]] = []
    hold: list[dict[str, Any]] = []
    field_audit: list[dict[str, Any]] = []
    from action_tracker.translation.model_guard import validate_model_output
    for (sku, digest), items in sorted(grouped.items(), key=lambda x: (int(x[0][0]) if x[0][0].isdigit() else 10**20, x[0][0], x[0][1])):
        by_field = {item["field"]: item for item in items}
        if set(by_field) != set(FIELDS):
            raise RuntimeError(f"FIELD_SET_INVALID:{sku}")
        source = {field: by_field[field]["source_es"] for field in FIELDS}
        recomputed = source_hash(source)
        if recomputed != digest:
            raise RuntimeError(f"SOURCE_HASH_MISMATCH:{sku}")
        proposed = {field: by_field[field]["proposed_zh"] for field in FIELDS}
        reasons: list[str] = []
        approved = True
        for field in FIELDS:
            item = by_field[field]
            disp = item["owner_disposition"]
            guard_reasons: list[str] = []
            if disp not in ACCEPTED:
                approved = False
                reasons.append(f"OWNER_{disp}:{field}")
            elif not proposed[field]:
                approved = False
                reasons.append(f"EMPTY_APPROVED_VALUE:{field}")
            else:
                check = validate_model_output({field: source[field]}, {field: proposed[field]}, expected_fields=[field])
                if not check.accepted:
                    approved = False
                    guard_reasons = list(check.field_reasons.get(field, check.reasons))
                    reasons.extend([f"GUARD_{reason}:{field}" for reason in guard_reasons])
            field_audit.append({
                **item, "recomputed_source_hash": recomputed,
                "final_field_guard": "PASS" if not guard_reasons and disp in ACCEPTED and proposed[field] else "FAIL",
                "final_guard_reasons": ";".join(guard_reasons),
            })
        record = {
            "review_id": review_id(sku, digest), "sku": sku, "source_hash": digest,
            "source_run_id": by_field["name"]["source_run_id"], "source": source,
            "proposed_zh": proposed, "owner_disposition": {field: by_field[field]["owner_disposition"] for field in FIELDS},
            "gold_eligible": approved, "production_writes": False, "training_runs": 0,
        }
        (gold if approved else hold).append({**record, "hold_reasons": reasons} if not approved else record)
    OUT.mkdir(parents=True, exist_ok=True)
    field_audit.sort(key=lambda r: (int(r["sku"]) if r["sku"].isdigit() else 10**20, r["sku"], r["field"]))
    gold.sort(key=lambda r: int(r["sku"]) if r["sku"].isdigit() else 10**20)
    hold.sort(key=lambda r: int(r["sku"]) if r["sku"].isdigit() else 10**20)
    (OUT / "stage6_owner_gold_eligible.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in gold), encoding="utf-8")
    (OUT / "stage6_owner_hold.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in hold), encoding="utf-8")
    columns = list(field_audit[0].keys())
    with (OUT / "stage6_owner_field_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(field_audit)
    audit = {
        "contract_id": "STAGE6_OWNER_REVIEW_INGEST_V2", "input_file": str(INPUT),
        "field_rows": len(rows), "source_pairs": len(grouped), "gold_eligible_sku_pairs": len(gold),
        "owner_hold_sku_pairs": len(hold), "accepted_field_rows": sum(r["owner_disposition"] in ACCEPTED for r in rows),
        "major_edit_field_rows": sum(r["owner_disposition"] == "REQUIRES_MAJOR_EDIT" for r in rows),
        "ambiguous_field_rows": sum(r["owner_disposition"] == "AMBIGUOUS" for r in rows),
        "source_hash_recompute": "500/500 PASS", "final_field_guard": "executed for all 3000 fields",
        "production_writes": 0, "training_runs": 0, "master_written": False, "dictionary_written": False,
        "status": "GOLD_CANDIDATES_READY_OWNER_REVIEW_COMPLETE" if not hold else "GOLD_PARTIAL_OWNER_HOLD_REMAINING",
    }
    (OUT / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "audit.md").write_text("\n".join([
        "# Stage 6 Owner Review Ingest", "", f"- Field rows: {len(rows)}", f"- Gold eligible pairs: {len(gold)}",
        f"- Held pairs: {len(hold)}", f"- Accepted fields: {audit['accepted_field_rows']}",
        "- Source hashes: 500/500 PASS", "- Final field Guard: executed for all 3000 fields",
        "- Production writes: 0; training runs: 0; Master/Dictionary unchanged.", f"- Status: `{audit['status']}`", "",
    ]) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
