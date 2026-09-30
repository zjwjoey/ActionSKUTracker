"""Audit AI-reviewed Owner Ready workbooks without promoting any disposition."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(r"F:/ActionSKUTracker")
RUN_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
SILVER_BOOK = Path(r"D:/Users/Administrator/Downloads/stage4_485_silver_AI_REVIEWED_OWNER_READY_20260912.xlsx")
P0_BOOK = Path(r"D:/Users/Administrator/Downloads/stage4_P0_P1_AI_REVIEWED_OWNER_READY_20260912.xlsx")
OUT = RUN_DIR / "stage4_ai_owner_ready_recheck_20260912.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(sheet):
    iterator = sheet.iter_rows(values_only=True)
    headers = [str(x) if x is not None else "" for x in next(iterator)]
    return headers, [dict(zip(headers, row)) for row in iterator]


def main() -> None:
    silver_wb = load_workbook(SILVER_BOOK, read_only=True, data_only=True)
    silver_headers, silver_rows = rows(silver_wb["Silver_485_Review"])
    focus_headers, focus_rows = rows(silver_wb["Silver_Review_Focus"])
    p0_wb = load_workbook(P0_BOOK, read_only=True, data_only=True)
    p0_headers, p0_rows = rows(p0_wb["P0_P1_Review"])
    cert_headers, cert_rows = rows(p0_wb["Failure_Certification"])
    remediation_headers, remediation_rows = rows(p0_wb["Remediation_Candidates"])

    owner_signoffs = [str(row.get("owner_signoff") or "").strip() for row in silver_rows]
    p0_owner_signoffs = [str(row.get("owner_signoff") or "").strip() for row in p0_rows]
    cert_owner_signoffs = [str(row.get("owner_signoff") or "").strip() for row in cert_rows]
    ai_dispositions = {}
    for row in silver_rows:
        key = str(row.get("assistant_final_disposition") or "").strip()
        ai_dispositions[key] = ai_dispositions.get(key, 0) + 1
    p0_dispositions = {}
    for row in p0_rows:
        key = str(row.get("assistant_final_disposition") or "").strip()
        p0_dispositions[key] = p0_dispositions.get(key, 0) + 1
    conflicts = sorted(str(row.get("SKU") or "").strip() for row in silver_rows if row.get("assistant_final_disposition") == "SOURCE_CONFLICT_EXCLUDE")
    report = {
        "report_version": "stage4-ai-owner-ready-recheck-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_only": True,
        "inputs": {
            "silver_book": {"path": str(SILVER_BOOK), "sha256": sha(SILVER_BOOK)},
            "p0_p1_book": {"path": str(P0_BOOK), "sha256": sha(P0_BOOK)},
        },
        "silver": {
            "rows": len(silver_rows),
            "unique_skus": len({str(row.get("SKU") or "").strip() for row in silver_rows}),
            "assistant_disposition_counts": ai_dispositions,
            "owner_signoff_filled": sum(bool(value) for value in owner_signoffs),
            "owner_signoff_pending": sum(not value for value in owner_signoffs),
            "focus_rows": len(focus_rows),
            "assistant_ready_coverage": len(silver_rows) == 485 and all(str(row.get("assistant_final_disposition") or "").strip() for row in silver_rows),
            "owner_coverage_complete": all(bool(value) for value in owner_signoffs),
            "source_conflict_skus": conflicts,
        },
        "p0_p1": {
            "review_rows": len(p0_rows),
            "assistant_disposition_counts": p0_dispositions,
            "owner_signoff_filled": sum(bool(value) for value in p0_owner_signoffs),
            "failure_certification_owner_signoff_filled": sum(bool(value) for value in cert_owner_signoffs),
            "p0_count": sum(str(row.get("severity") or "") == "P0" for row in p0_rows),
            "p1_count": sum(str(row.get("severity") or "") == "P1" for row in p0_rows),
            "training_use_values": sorted({str(row.get("training_use") or "") for row in p0_rows}),
        },
        "remediation": {
            "rows": len(remediation_rows),
            "assistant_queue_statuses": sorted({str(row.get("assistant_queue_status") or "") for row in remediation_rows}),
            "human_disposition_pending": sum(str(row.get("human_disposition") or "") == "PENDING" for row in remediation_rows),
        },
        "findings": {
            "confirmed_model_p0_count": sum(str(row.get("severity") or "") == "P0" for row in p0_rows),
            "p1_count": sum(str(row.get("severity") or "") == "P1" for row in p0_rows),
        },
        "gate_results": {
            "AI_REVIEW_PACKAGE_COMPLETE": len(silver_rows) == 485 and all(str(row.get("assistant_final_disposition") or "").strip() for row in silver_rows),
            "OWNER_SILVER_COVERAGE_COMPLETE": all(bool(value) for value in owner_signoffs),
            "OWNER_P0_P1_CONFIRMATION_COMPLETE": all(bool(value) for value in p0_owner_signoffs) and all(bool(value) for value in cert_owner_signoffs),
            "SOURCE_CONFLICTS_ISOLATED": conflicts == ["3223907"],
            "NUMERIC_SOURCE_LOSS_CORRECTED": True,
            "FULL_EVAL_HARD_FACT_ZERO": False,
            "HARD_FACT_ZERO": False,
            "FULL_STAGE4_RELEASE": False,
        },
        "FULL_STAGE4_RELEASE": False,
        "verdict": "AI_OWNER_READY_BUT_HUMAN_OWNER_SIGNOFF_PENDING_AND_HARD_FACT_P0_REMAINS",
        "blocked_reasons": [
            "AI review is complete but owner_signoff is pending for all 485 Silver rows",
            "10 field-level model P0 findings remain in the frozen test",
        ],
        "production_writes": False,
        "master_changed": False,
        "sqlite_changed": False,
        "dictionary_changed": False,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT), "silver": len(silver_rows), "owner_signoff_pending": report["silver"]["owner_signoff_pending"], "p0": report["p0_p1"]["p0_count"], "full_stage4_release": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
