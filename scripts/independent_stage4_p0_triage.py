"""Independent, read-only triage of the Stage 4 P0 review package.

This does not decide release eligibility.  It records source-side evidence so
that a human can distinguish a real field loss/hallucination from a surface
counter difference (duplicate values, number words, or units).
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"F:/ActionSKUTracker")
RUN_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
INPUT = RUN_DIR / "stage4_full_eval_owner_signed_p0_review_package_20260912.csv"
OUTPUT = RUN_DIR / "stage4_full_eval_owner_signed_p0_triage_20260912.json"

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
TECH_TOKEN = re.compile(r"\b(?:H7|P21/5W|W5W|C5W|PY21W|[A-Z]{1,4}\d(?:/[A-Z0-9]+)?)\b")


def numbers(value: str) -> list[str]:
    return sorted(x.replace(",", ".") for x in NUMBER.findall(value or ""))


def tech_tokens(value: str) -> list[str]:
    return sorted(set(TECH_TOKEN.findall(value or "")))


def main() -> None:
    rows = list(csv.DictReader(INPUT.open(encoding="utf-8-sig", newline="")))
    findings = []
    for row in rows:
        src = row["source_value"]
        pred = row["prediction_value"]
        src_nums = numbers(src)
        pred_nums = numbers(pred)
        src_tokens = tech_tokens(src)
        pred_tokens = tech_tokens(pred)
        missing_numbers = sorted(set(src_nums) - set(pred_nums))
        extra_numbers = sorted(set(pred_nums) - set(src_nums))
        missing_tokens = sorted(set(src_tokens) - set(pred_tokens))
        if row["severity"] == "P0" and (missing_numbers or extra_numbers or missing_tokens or row["kind"] == "SEMANTIC_TRANSLATION_ERROR"):
            evidence = "SOURCE_FIELD_DIRECT"
        else:
            evidence = "DERIVED_OR_SURFACE"
        findings.append({
            "sku": row["sku"],
            "field": row["field"],
            "severity": row["severity"],
            "kind": row["kind"],
            "evidence": evidence,
            "source_numbers": src_nums,
            "prediction_numbers": pred_nums,
            "missing_numbers": missing_numbers,
            "extra_numbers": extra_numbers,
            "source_technical_tokens": src_tokens,
            "prediction_technical_tokens": pred_tokens,
            "missing_technical_tokens": missing_tokens,
        })
    p0 = [f for f in findings if f["severity"] == "P0"]
    report = {
        "report_version": "stage4-independent-p0-triage-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_only": True,
        "input": str(INPUT),
        "rows": len(findings),
        "p0_rows": len(p0),
        "p0_source_direct_rows": sum(f["evidence"] == "SOURCE_FIELD_DIRECT" for f in p0),
        "p0_findings": p0,
        "p1_followups": [f for f in findings if f["severity"] == "P1"],
        "release_decision": "UNCHANGED_FULL_STAGE4_RELEASE_FALSE",
        "production_writes": False,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "p0_rows": len(p0), "p0_source_direct_rows": report["p0_source_direct_rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
