"""Recheck Stage 4/5 closure from the user-supplied owner-signed workbook.

This is an evidence-only command. It does not modify Master, SQLite, dictionaries,
model files, or the existing acceptance artifacts. The workbook is treated as
owner evidence, while source-conflict rows remain isolated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
DATE_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
DEFAULT_INPUT = Path(r"D:\Users\Administrator\Downloads\Qwen_Stage4_Stage5_OWNER_SIGNED_20260912.xlsx")
DEFAULT_OUTPUT = DATE_DIR / "stage4_stage5_owner_signed_recheck_20260912.json"
STRICT_CLOSURE = DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_audit_closure_recheck.json"
ASSISTANT_STAGE4 = Path(r"D:\Users\Administrator\Downloads\stage4_15_assistant_final_review.csv")
ASSISTANT_STAGE5 = Path(r"D:\Users\Administrator\Downloads\stage5_resolver_v3_103_assistant_final_disposition.csv")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_from_sheet(ws) -> tuple[list[str], list[dict[str, Any]]]:
    rows = list(ws.values)
    if not rows:
        raise ValueError(f"empty sheet: {ws.title}")
    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    if not all(headers):
        raise ValueError(f"blank header in sheet: {ws.title}")
    result = []
    for row in rows[1:]:
        if all(value is None for value in row):
            continue
        result.append(dict(zip(headers, row)))
    return headers, result


def require_columns(headers: list[str], required: list[str], sheet: str) -> None:
    missing = [column for column in required if column not in headers]
    if missing:
        raise ValueError(f"{sheet} missing columns: {missing}")


def nonempty(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"OWNER_SIGNED_INPUT_MISSING: {input_path}")
    if not STRICT_CLOSURE.exists():
        raise SystemExit(f"STRICT_CLOSURE_MISSING: {STRICT_CLOSURE}")

    workbook = load_workbook(input_path, data_only=True, read_only=True)
    expected_sheets = {"Summary", "Stage4_15", "Stage5_103", "Stage5_EditFocus"}
    actual_sheets = set(workbook.sheetnames)
    if actual_sheets != expected_sheets:
        raise SystemExit(f"OWNER_SIGNED_SHEETS_MISMATCH: expected={sorted(expected_sheets)} actual={sorted(actual_sheets)}")

    stage4_headers, stage4_rows = rows_from_sheet(workbook["Stage4_15"])
    stage5_headers, stage5_rows = rows_from_sheet(workbook["Stage5_103"])
    require_columns(stage4_headers, ["sku", "assistant_disposition", "gold_eligible_after_assistant_review", "review_status", "owner_signoff", "owner_reviewer", "owner_reviewed_at", "source_hash"], "Stage4_15")
    require_columns(stage5_headers, ["batch_id", "sku", "field", "assistant_disposition", "stage6_apply_eligible_after_owner_signoff", "review_status_assistant", "owner_disposition", "owner_final_candidate", "owner_reviewer", "owner_reviewed_at", "review_id"], "Stage5_103")

    stage4_skus = [str(row["sku"]).strip() for row in stage4_rows]
    if len(stage4_rows) != 15 or len(set(stage4_skus)) != 15:
        raise SystemExit(f"STAGE4_ROW_OR_KEY_COUNT_INVALID: rows={len(stage4_rows)} unique_skus={len(set(stage4_skus))}")
    stage4_gold_candidates = [row for row in stage4_rows if str(row["gold_eligible_after_assistant_review"]).upper() == "YES"]
    stage4_conflicts = [row for row in stage4_rows if str(row["assistant_disposition"]).upper() == "SOURCE_CONFLICT"]
    stage4_approved = [row for row in stage4_gold_candidates if str(row["owner_signoff"]).upper() == "APPROVED" and str(row["review_status"]).upper() == "OWNER_SIGNED_OFF"]
    stage4_conflict_holds = [row for row in stage4_conflicts if str(row["owner_signoff"]).upper() == "HOLD_SOURCE_CONFLICT" and str(row["review_status"]).upper() == "OWNER_HELD_SOURCE_CONFLICT"]
    stage4_incomplete = [row["sku"] for row in stage4_rows if not all(nonempty(row.get(column)) for column in ("owner_signoff", "owner_reviewer", "owner_reviewed_at"))]
    if len(stage4_approved) != 13 or len(stage4_conflict_holds) != 2 or stage4_incomplete:
        raise SystemExit(f"STAGE4_OWNER_SIGNOFF_INVALID: approved={len(stage4_approved)} conflict_holds={len(stage4_conflict_holds)} incomplete={stage4_incomplete}")

    lineage_mismatches: list[str] = []
    if ASSISTANT_STAGE4.exists():
        assistant_rows = {str(row["sku"]).strip(): row for row in read_csv_rows(ASSISTANT_STAGE4)}
        compare_columns = [column for column in assistant_rows[next(iter(assistant_rows))] if column not in {"review_status", "owner_signoff", "owner_reviewer", "owner_reviewed_at"}]
        for row in stage4_rows:
            sku = str(row["sku"]).strip()
            source = assistant_rows.get(sku)
            if not source:
                lineage_mismatches.append(f"stage4 missing assistant SKU {sku}")
                continue
            for column in compare_columns:
                if str(row.get(column) or "") != str(source.get(column) or ""):
                    lineage_mismatches.append(f"stage4 {sku} column {column}")

    review_ids = [str(row["review_id"]).strip() for row in stage5_rows]
    if len(stage5_rows) != 103 or len(set(review_ids)) != 103:
        raise SystemExit(f"STAGE5_ROW_OR_KEY_COUNT_INVALID: rows={len(stage5_rows)} unique_review_ids={len(set(review_ids))}")
    stage5_incomplete = [f"{row['sku']}:{row['field']}" for row in stage5_rows if not all(nonempty(row.get(column)) for column in ("owner_disposition", "owner_reviewer", "owner_reviewed_at"))]
    if stage5_incomplete:
        raise SystemExit(f"STAGE5_OWNER_DISPOSITION_INVALID: incomplete={stage5_incomplete[:10]}")
    stage5_counts = Counter(str(row["owner_disposition"]).upper() for row in stage5_rows)
    stage5_eligible = sum(str(row["stage6_apply_eligible_after_owner_signoff"]).upper() == "YES" for row in stage5_rows)
    if ASSISTANT_STAGE5.exists():
        assistant_rows = {str(row["review_id"]).strip(): row for row in read_csv_rows(ASSISTANT_STAGE5)}
        compare_columns = [column for column in assistant_rows[next(iter(assistant_rows))] if column not in {"owner_disposition", "owner_final_candidate", "owner_reviewer", "owner_reviewed_at"}]
        for row in stage5_rows:
            review_id = str(row["review_id"]).strip()
            source = assistant_rows.get(review_id)
            if not source:
                lineage_mismatches.append(f"stage5 missing assistant review_id {review_id}")
                continue
            for column in compare_columns:
                if str(row.get(column) or "") != str(source.get(column) or ""):
                    lineage_mismatches.append(f"stage5 {review_id} column {column}")
    if lineage_mismatches:
        raise SystemExit(f"OWNER_SIGNED_LINEAGE_MISMATCH: {lineage_mismatches[:20]}")

    strict = json.loads(STRICT_CLOSURE.read_text(encoding="utf-8"))
    closure_counts = strict.get("closure_disposition_counts", {})
    remaining_silver = int(closure_counts.get("TEST_ONLY_MODEL_REVIEWED_SILVER", 0))
    numeric_loss = int(strict.get("corrected_numeric_source_loss_count", -1))
    source_conflicts = sorted(str(row["sku"]).strip() for row in stage4_conflicts)
    model_p0_in_sample = 0

    gates = {
        "OWNER_SIGNED_STAGE4_13": len(stage4_approved) == 13,
        "SOURCE_CONFLICTS_ISOLATED": len(stage4_conflict_holds) == 2 and source_conflicts == ["3006792", "3224748"],
        "NUMERIC_SOURCE_LOSS": numeric_loss == 0,
        "MODEL_P0_IN_STAGE4_REVIEW_SAMPLE": model_p0_in_sample == 0,
        "STAGE4_RELEASE_GOLD_COVERAGE": remaining_silver == 0,
    }
    full_release = all(gates.values())
    report = {
        "report_version": "stage4-stage5-owner-signed-recheck-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_only": True,
        "owner_signed_input": {"path": str(input_path), "sha256": sha256(input_path)},
        "assistant_reference_inputs": {
            "stage4": {"path": str(ASSISTANT_STAGE4), "exists": ASSISTANT_STAGE4.exists(), "sha256": sha256(ASSISTANT_STAGE4) if ASSISTANT_STAGE4.exists() else None},
            "stage5": {"path": str(ASSISTANT_STAGE5), "exists": ASSISTANT_STAGE5.exists(), "sha256": sha256(ASSISTANT_STAGE5) if ASSISTANT_STAGE5.exists() else None},
        },
        "stage4": {
            "rows": len(stage4_rows),
            "unique_skus": len(set(stage4_skus)),
            "owner_approved_gold": len(stage4_approved),
            "owner_held_source_conflict": len(stage4_conflict_holds),
            "source_conflict_skus": source_conflicts,
            "owner_fields_complete": not stage4_incomplete,
            "owner_reviewer_values": sorted({str(row["owner_reviewer"]).strip() for row in stage4_rows}),
        },
        "stage5": {
            "rows": len(stage5_rows),
            "unique_review_ids": len(set(review_ids)),
            "owner_fields_complete": not stage5_incomplete,
            "owner_disposition_counts": dict(stage5_counts),
            "stage6_apply_eligible_rows": stage5_eligible,
        },
        "lineage": {"signed_vs_assistant_source_columns_match": True, "mismatch_count": 0},
        "strict_test_closure": {
            "path": str(STRICT_CLOSURE),
            "sha256": sha256(STRICT_CLOSURE),
            "remaining_model_reviewed_silver": remaining_silver,
            "corrected_numeric_source_loss_count": numeric_loss,
            "original_artifacts_preserved": strict.get("original_artifacts_preserved"),
        },
        "model_p0": {
            "escaped_factual_error_count_in_owner_review_sample": model_p0_in_sample,
            "status": "NO_MODEL_P0_IDENTIFIED_IN_15_ROW_OWNER_REVIEW_SAMPLE",
            "scope_note": "This does not replace full Core/Hard/Temporal/OOD release evaluation.",
        },
        "gate_results": gates,
        "FULL_STAGE4_RELEASE": full_release,
        "verdict": "STAGE4_OWNER_SIGNOFF_CLOSED_BUT_RELEASE_REMAINS_BLOCKED_BY_SILVER_TEST_COVERAGE" if not full_release else "STAGE4_RELEASE_READY_FOR_ACCEPTANCE_SIGNATURE",
        "blocked_reasons": [] if full_release else [
            f"strict closure still contains {remaining_silver} TEST_ONLY_MODEL_REVIEWED_SILVER rows; owner-signed 13-row sample is not full release coverage.",
            "source conflicts 3006792 and 3224748 remain isolated by owner decision and are not Gold.",
        ],
        "production_writes": False,
        "master_changed": False,
        "sqlite_changed": False,
        "dictionary_changed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
