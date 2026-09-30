"""Audit a source/target JSONL export against the versioned regression corpus.

This is read-only.  It reports corpus validity and all same-source
occurrences; it never changes a workbook, Master, Dictionary or database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the standalone script behave the same way as the project test runner.
# Without this, ``python scripts/audit_localization_regressions.py`` fails
# before it can audit any records when the package is not installed.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.translation.regression_cases import load_regression_cases, summarize_occurrences


def _records_from_workbooks(source_path: Path, target_path: Path, sheet: str) -> tuple[list[dict], dict[str, int]]:
    """Build field-bound regression records from existing workbooks read-only."""
    # Reuse the canonical workbook reader so aliases, duplicate SKUs and
    # required-field handling stay identical to the full localization audit.
    from audit_chinese_localization import _read_sheet

    _, source_rows = _read_sheet(source_path, sheet)
    _, target_rows = _read_sheet(target_path, sheet)
    source_skus, target_skus = set(source_rows), set(target_rows)
    records = []
    for sku in sorted(source_skus & target_skus):
        source, target = source_rows[sku], target_rows[sku]
        record = {"sku": sku}
        for field in ("name", "cat1", "cat2", "spec", "description", "details"):
            record[f"{field}_es"] = str(source.get(field) or "")
            record[f"{field}_zh"] = str(target.get(field) or "")
        records.append(record)
    return records, {
        "source_sku_count": len(source_skus),
        "target_sku_count": len(target_skus),
        "matched_sku_count": len(source_skus & target_skus),
        "source_only_sku_count": len(source_skus - target_skus),
        "target_only_sku_count": len(target_skus - source_skus),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "data/qa/localization_regressions_v1.jsonl"))
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--records", help="JSONL rows containing source and target fields")
    input_group.add_argument("--source-workbook", type=Path, help="Spanish source workbook (read-only)")
    parser.add_argument("--target-workbook", type=Path, help="Chinese candidate workbook (required with --source-workbook)")
    parser.add_argument("--sheet", default="商品全量")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cases = load_regression_cases(Path(args.cases))
    if args.source_workbook:
        if not args.target_workbook:
            parser.error("--target-workbook is required with --source-workbook")
        records, sku_counts = _records_from_workbooks(args.source_workbook, args.target_workbook, args.sheet)
    else:
        records = [json.loads(line) for line in Path(args.records).read_text(encoding="utf-8").splitlines() if line.strip()]
        sku_counts = {}
    report = {
        "policy_version": "LOCALIZATION_REGRESSION_V1",
        "case_count": len(cases),
        "summaries": summarize_occurrences(records, cases),
        "read_only": True,
        "master_writes": 0,
        "production_apply": False,
        **sku_counts,
    }
    report["regression_passed"] = all(item["regression_passed"] for item in report["summaries"])
    if sku_counts:
        report["regression_passed"] = report["regression_passed"] and not (
            sku_counts["source_only_sku_count"] or sku_counts["target_only_sku_count"]
        )
    report["wrong_target_count"] = sum(item["wrong_target_count"] for item in report["summaries"])
    report["expected_target_count"] = sum(item["expected_target_count"] for item in report["summaries"])
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": len(cases), "output": args.output, "regression_passed": report["regression_passed"], "wrong_target_count": report["wrong_target_count"]}, ensure_ascii=False))
    return 0 if report["regression_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
