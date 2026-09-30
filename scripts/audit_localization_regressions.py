"""Audit a source/target JSONL export against the versioned regression corpus.

This is read-only.  It reports corpus validity and all same-source
occurrences; it never changes a workbook, Master, Dictionary or database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from action_tracker.translation.regression_cases import load_regression_cases, summarize_occurrences


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="data/qa/localization_regressions_v1.jsonl")
    parser.add_argument("--records", required=True, help="JSONL rows containing source and target fields")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cases = load_regression_cases(Path(args.cases))
    records = [json.loads(line) for line in Path(args.records).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = {
        "policy_version": "LOCALIZATION_REGRESSION_V1",
        "case_count": len(cases),
        "summaries": summarize_occurrences(records, cases),
        "read_only": True,
        "master_writes": 0,
        "production_apply": False,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": len(cases), "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
