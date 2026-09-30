"""Build an evidence-only Stage 4 closure report.

This combines the owner-signed review package with the frozen 485-row adapter
evaluation.  It intentionally does not apply translations, alter Master, or
change any release state.  The explicit issue map records semantic findings
that the numeric surface checker cannot distinguish from harmless duplicate or
number-word formatting differences.
"""

from __future__ import annotations

import hashlib
import json
import csv
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(r"F:/ActionSKUTracker")
RUN_DIR = REPO / "runtime/training/qwen3_8b/20260911"
OWNER_REPORT = RUN_DIR / "stage4_stage5_owner_signed_recheck_20260912.json"
FULL_EVAL = RUN_DIR / "stage4_full_eval_owner_signed_20260912_v2.json"
PREDICTIONS = RUN_DIR / "stage4_full_eval_owner_signed_20260912_predictions.jsonl"
OUTPUT = RUN_DIR / "stage4_stage5_owner_signed_full_closure_recheck_20260912.json"
REVIEW_CSV = RUN_DIR / "stage4_full_eval_owner_signed_p0_review_package_20260912.csv"
CERT_CSV = RUN_DIR / "stage4_final_failure_certification.csv"
CERT_JSON = RUN_DIR / "stage4_final_failure_certification.json"
REMEDIATION_QUEUE_CSV = RUN_DIR / "stage4_targeted_remediation_review_queue.csv"
REMEDIATION_QUEUE_JSON = RUN_DIR / "stage4_targeted_remediation_review_queue.json"


# Findings requiring repair under the current field-level hard-fact contract.
# They are deliberately kept separate from parser/surface variations below.
HARD_FACT_ISSUES = [
    {
        "sku": "3219003",
        "field": "name",
        "severity": "P0",
        "kind": "BRAND_AND_NUMERIC_LOSS",
        "reason": "品牌 Kate Legwear 与 40 Denier 在模型品名中丢失。",
    },
    {
        "sku": "3220894",
        "field": "description",
        "severity": "P0",
        "kind": "CROSS_FIELD_LEAK",
        "reason": "描述把详情字段中的 6 岁条件搬入描述；当前字段级合同不允许跨字段新增事实。",
    },
    {
        "sku": "3217699",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_HALLUCINATION",
        "reason": "描述新增来源中不存在的 1.25L，并把 2.5L 从其他字段带入。",
    },
    {
        "sku": "3223271",
        "field": "description",
        "severity": "P0",
        "kind": "TECH_TOKEN_LOSS",
        "reason": "车型/灯泡技术 token H7、P21/5W、W5W、C5W、PY21W 未保留。",
    },
    {
        "sku": "3221705",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_LOSS",
        "reason": "来源描述中的 Milka 250g 未保留。",
    },
    {
        "sku": "3221795",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_LOSS",
        "reason": "来源描述中的 14 个灯泡未保留。",
    },
    {
        "sku": "3201998",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_LOSS",
        "reason": "来源描述中的每卷 64 张未保留。",
    },
    {
        "sku": "3213267",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_LOSS",
        "reason": "来源描述中的 27 件未保留。",
    },
    {
        "sku": "3009588",
        "field": "description",
        "severity": "P0",
        "kind": "NUMERIC_LOSS",
        "reason": "来源描述中的最多 5 升未保留。",
    },
    {
        "sku": "3209605",
        "field": "name",
        "severity": "P0",
        "kind": "SEMANTIC_TRANSLATION_ERROR",
        "reason": "La Isla Living 商品名被译成拖鞋垫，期望语义为门垫。",
    },
]

P1_ISSUES = [
    {
        "sku": "3217469",
        "field": "description",
        "severity": "P1",
        "kind": "DERIVED_TOTAL_PRESENTATION",
        "reason": "来源明确给出 12+12/24；模型保留分项但未重复写总数 24。",
    }
]

SURFACE_VARIATIONS = [
    "duplicate_numeric_tokens_collapsed",
    "number_words_rendered_as_chinese_digits",
    "unit_or_measure_word_surface_parser_mismatch",
    "allowlisted_proper_name_flagged_as_spanish_residual",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    owner = json.loads(OWNER_REPORT.read_text(encoding="utf-8"))
    full_eval = json.loads(FULL_EVAL.read_text(encoding="utf-8"))
    prediction_rows = [
        json.loads(line)
        for line in PREDICTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checker_issue_rows = []
    for row in prediction_rows:
        checks = row.get("field_checks") or {}
        if any(
            value.get("numeric_missing")
            or value.get("numeric_extra")
            or value.get("spanish_residual")
            for value in checks.values()
            if isinstance(value, dict)
        ):
            checker_issue_rows.append(row["sku"])

    hard_fact_skus = sorted({item["sku"] for item in HARD_FACT_ISSUES})
    p1_skus = sorted({item["sku"] for item in P1_ISSUES})
    prediction_by_sku = {row["sku"]: row for row in prediction_rows}
    review_rows = []
    for finding in HARD_FACT_ISSUES + P1_ISSUES:
        row = prediction_by_sku.get(finding["sku"], {})
        source = row.get("source") or {}
        expected = row.get("expected") or {}
        prediction = row.get("prediction") or {}
        field = finding["field"]
        review_rows.append(
            {
                **finding,
                "source_value": source.get(field, ""),
                "expected_value": expected.get(field, ""),
                "prediction_value": prediction.get(field, ""),
            }
        )
    with REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "field", "severity", "kind", "reason", "source_value", "expected_value", "prediction_value"])
        writer.writeheader()
        writer.writerows(review_rows)

    certification_rows = []
    for finding, review in zip(HARD_FACT_ISSUES + P1_ISSUES, review_rows):
        is_p0 = finding["severity"] == "P0"
        validator_status = "DETECTOR_FLAGGED" if finding["kind"] in {"NUMERIC_LOSS", "NUMERIC_HALLUCINATION", "TECH_TOKEN_LOSS", "BRAND_AND_NUMERIC_LOSS"} else "MANUAL_SOURCE_COMPARISON"
        certification_rows.append({
            "failure_id": f"STAGE4-20260912-{finding['sku']}-{finding['field']}",
            "sku": finding["sku"],
            "field": finding["field"],
            "source": review["source_value"],
            "gold_reference": review["expected_value"],
            "prediction": review["prediction_value"],
            "failure_type": finding["kind"],
            "severity": finding["severity"],
            "source_status": "VERIFIED_DIRECT_SOURCE",
            "gold_status": "MODEL_REVIEWED_SILVER_NOT_HUMAN_GOLD",
            "split_status": "PASS_IMMUTABLE_TEST_ONLY_NO_OVERLAP",
            "contract_status": "PASS_FIELD_CONTRACT",
            "validator_status": validator_status,
            "model_status": "CONFIRMED_MODEL_FAILURE" if is_p0 else "MODEL_QUALITY_P1",
            "final_root_cause": "MODEL_OUTPUT_FIELD_FIDELITY" if is_p0 else "MODEL_OUTPUT_EXPRESSION_QUALITY",
            "evidence": finding["reason"],
            "remediation": "Use disjoint, source-verified remediation Gold; keep frozen 485 out of train; prefer field-conditioned targeted repair." if is_p0 else "Handle as separate P1 quality follow-up; do not promote to P0.",
            "retrain_required": "YES" if is_p0 else "NO",
        })
    cert_fields = list(certification_rows[0])
    with CERT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cert_fields)
        writer.writeheader()
        writer.writerows(certification_rows)
    CERT_JSON.write_text(json.dumps({
        "report_version": "stage4-final-failure-certification-2026-09-12-v1",
        "evidence_only": True,
        "rows": certification_rows,
        "p0_count": len(HARD_FACT_ISSUES),
        "p1_count": len(P1_ISSUES),
        "production_writes": False,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "report_version": "stage4-stage5-owner-signed-full-closure-recheck-2026-09-12-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evidence_only": True,
        "inputs": {
            "owner_signed_report": {"path": str(OWNER_REPORT), "sha256": sha256(OWNER_REPORT)},
            "full_eval": {"path": str(FULL_EVAL), "sha256": sha256(FULL_EVAL)},
            "predictions": {"path": str(PREDICTIONS), "sha256": sha256(PREDICTIONS)},
        },
        "owner_signed_closure": {
            "stage4_owner_approved_gold": owner["stage4"]["owner_approved_gold"],
            "stage4_source_conflict_skus": owner["stage4"]["source_conflict_skus"],
            "stage5_rows": owner["stage5"]["rows"],
            "stage5_apply_eligible_rows": owner["stage5"]["stage6_apply_eligible_rows"],
            "signed_lineage_match": owner["lineage"]["signed_vs_assistant_source_columns_match"],
        },
        "full_eval": {
            "rows": full_eval["rows"],
            "field_values": full_eval["evaluated_field_values"],
            "json_parse_rate": full_eval["json_parse_rate"],
            "schema_pass_rate": full_eval["field_schema_rate"],
            "nonempty_rate": full_eval["field_nonempty_rate"],
            "category_valid_rate": full_eval.get("category_valid_rate"),
            "numeric_preservation_rate": full_eval["numeric_preservation_rate"],
            "numeric_hallucination_rate": full_eval["numeric_hallucination_rate"],
            "spanish_residual_rate": full_eval["spanish_residual_rate"],
            "checker_issue_sku_count": len(set(checker_issue_rows)),
            "checker_issue_row_count": len(checker_issue_rows),
            "batch_size": full_eval.get("batch_size"),
            "production_writes": False,
        },
        "findings": {
            "confirmed_model_p0": HARD_FACT_ISSUES,
            "confirmed_model_p0_count": len(HARD_FACT_ISSUES),
            "p1_followups": P1_ISSUES,
            "surface_variations_not_p0": SURFACE_VARIATIONS,
            "allowlisted_proper_name_skus": ["3221087", "3216938"],
            "human_review_package": {"path": str(REVIEW_CSV), "sha256": sha256(REVIEW_CSV), "rows": len(review_rows)},
            "failure_certification": {
                "csv_path": str(CERT_CSV),
                "csv_sha256": sha256(CERT_CSV),
                "json_path": str(CERT_JSON),
                "json_sha256": sha256(CERT_JSON),
                "rows": len(certification_rows),
            },
            "targeted_remediation_queue": {
                "csv_path": str(REMEDIATION_QUEUE_CSV),
                "json_path": str(REMEDIATION_QUEUE_JSON),
                "exists": REMEDIATION_QUEUE_CSV.exists() and REMEDIATION_QUEUE_JSON.exists(),
                "human_confirmation_required": True,
            },
        },
        "gate_results": {
            "OWNER_SIGNED_STAGE4_13": owner["stage4"]["owner_approved_gold"] == 13,
            "SOURCE_CONFLICTS_ISOLATED": owner["stage4"]["owner_held_source_conflict"] == 2,
            "NUMERIC_SOURCE_LOSS_CORRECTED": owner["strict_test_closure"]["corrected_numeric_source_loss_count"] == 0,
            "FULL_EVAL_STRUCTURE": full_eval["json_parse_rate"] == 1.0 and full_eval["field_schema_rate"] == 1.0 and full_eval["field_nonempty_rate"] == 1.0,
            "FULL_EVAL_CATEGORY": full_eval.get("category_valid_rate") == 1.0,
            "FULL_EVAL_HARD_FACT_ZERO": len(HARD_FACT_ISSUES) == 0,
            "FULL_STAGE4_RELEASE": False,
        },
        "FULL_STAGE4_RELEASE": False,
        "verdict": "STAGE4_OWNER_SIGNOFF_CLOSED_BUT_FULL_EVAL_HAS_FIELD_LEVEL_P0_AND_SILVER_COVERAGE_REMAINS_INCOMPLETE",
        "blocked_reasons": [
            f"full 485-row frozen adapter evaluation identified {len(HARD_FACT_ISSUES)} field-level P0 findings requiring repair",
            "strict closure still contains 485 TEST_ONLY_MODEL_REVIEWED_SILVER rows; 13 owner-signed Gold rows are not full release coverage",
            "source conflicts 3006792 and 3224748 remain isolated and are not Gold",
        ],
        "safety": {
            "master_changed": False,
            "sqlite_changed": False,
            "dictionary_changed": False,
            "model_artifacts_overwritten": False,
        },
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "p0": len(HARD_FACT_ISSUES), "p1": len(P1_ISSUES), "full_stage4_release": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
