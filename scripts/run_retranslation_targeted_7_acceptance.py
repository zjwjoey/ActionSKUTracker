"""Generate the seven-SKU regression acceptance for the retranslation batch.

This is read-only: it compares the frozen pre-fix candidate rows with the
derived post-fix rows and never writes Master/PRIMARY.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


CASES = {
    ("2513658", "name"): {
        "root_cause": "BRAND_POLICY + NUMERIC_QA_SCOPE",
        "fix_type": "brand_alias_and_brand_numeric_scope",
        "expected": "no 7Up/七喜; no numeric blocker",
    },
    ("2526033", "name"): {
        "root_cause": "FORMAT_TOKEN_DROPPED",
        "fix_type": "name_identity_fact_preservation",
        "expected": "A4 plus notebook product identity",
    },
    ("2534770", "name"): {
        "root_cause": "FORMAT_TOKEN_DROPPED",
        "fix_type": "name_identity_fact_preservation",
        "expected": "A4 plus notebook product identity",
    },
    ("2544409", "name"): {
        "root_cause": "MODEL_TOKEN_DROPPED",
        "fix_type": "brand_span_model_preservation",
        "expected": "F48 plus wiper product identity",
    },
    ("2544410", "name"): {
        "root_cause": "MODEL_TOKEN_DROPPED",
        "fix_type": "brand_span_model_preservation",
        "expected": "F45 plus wiper product identity",
    },
    ("2544411", "name"): {
        "root_cause": "MODEL_TOKEN_DROPPED",
        "fix_type": "brand_span_model_preservation",
        "expected": "F40 plus wiper product identity",
    },
    ("2548558", "description"): {
        "root_cause": "EMPTY_SOURCE_CONTRACT",
        "fix_type": "no_source_not_required_no_patch",
        "expected": "NOT_REQUIRED, empty candidate, no Owner task",
    },
}


def read(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {(row.get("sku", ""), row.get("field_name", "")): row for row in csv.DictReader(handle)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-dir", required=True)
    parser.add_argument("--after-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before = read(Path(args.before_dir) / "master_localization_pending_fields.csv")
    after = read(Path(args.after_dir) / "master_localization_pending_fields.csv")
    owner = read(Path(args.after_dir) / "owner_review_queue.csv")
    rows: list[dict[str, str]] = []
    for key, meta in CASES.items():
        b = before.get(key, {})
        a = after.get(key, {})
        candidate = a.get("candidate_zh", "")
        findings = a.get("qa_findings", "")
        if key == ("2513658", "name"):
            passed = not any(token in candidate for token in ("7Up", "7UP", "七喜")) and "NUMERIC_DROPPED" not in findings
        elif key == ("2526033", "name"):
            passed = "A4" in candidate and "方格" in candidate and "活页本" in candidate
        elif key == ("2534770", "name"):
            passed = "A4" in candidate and "笔记本" in candidate
        elif key == ("2544409", "name"):
            passed = "F48" in candidate and "雨刷" in candidate
        elif key == ("2544410", "name"):
            passed = "F45" in candidate and "雨刷" in candidate
        elif key == ("2544411", "name"):
            passed = "F40" in candidate and "雨刷" in candidate
        else:
            passed = (
                not candidate.strip()
                and a.get("field_status") == "NO_SOURCE"
                and a.get("ready_for_master") == "NOT_REQUIRED"
                and key not in owner
                and "EMPTY_REQUIRED_FIELD" not in findings
            )
        rows.append({
            "sku": key[0], "field_name": key[1],
            "source_es": a.get("source_es", b.get("source_es", "")),
            "before_candidate": b.get("candidate_zh", ""),
            "after_candidate": candidate,
            "before_qa": b.get("fact_qa_status", b.get("qa_findings", "")),
            "after_qa": a.get("fact_qa_status", ""),
            "root_cause": meta["root_cause"], "fix_type": meta["fix_type"],
            "expected_behavior": meta["expected"],
            "actual_behavior": f"status={a.get('field_status','')}; ready={a.get('ready_for_master','')}; findings={findings or 'none'}",
            "PASS_FAIL": "PASS" if passed else "FAIL",
            "note": "owner queue excluded" if key not in owner else "owner queue present",
        })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(f"targeted={len(rows)} passed={sum(row['PASS_FAIL'] == 'PASS' for row in rows)} failed={sum(row['PASS_FAIL'] == 'FAIL' for row in rows)}")
    return 0 if all(row["PASS_FAIL"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
