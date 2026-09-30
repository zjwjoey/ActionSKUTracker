"""Build a read-only Stage 4 P0 remediation queue.

This artifact is deliberately review-only.  It copies source/evaluation
evidence for the confirmed P0 findings and records the current release gate;
it never edits model, Gold, Master, SQLite, dictionary, or production state.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runtime" / "training" / "qwen3_8b" / "20260911"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_predictions(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sku = str(row.get("sku") or "")
        if sku:
            result[sku] = row
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    out = Path(args.output_dir) if args.output_dir else ROOT / "runtime" / "training" / "qwen3_8b" / "20260914" / "stage4_p0_remediation"
    out.mkdir(parents=True, exist_ok=True)

    triage_path = RUN / "stage4_full_eval_owner_signed_p0_triage_20260912.json"
    recovery_path = RUN / "stage4_recovery_state.json"
    closure_path = RUN / "stage4_owner_confirmed_closure_20260912.json"
    predictions_path = RUN / "stage4_full_eval_owner_signed_20260912_predictions.jsonl"
    auth_path = ROOT / "runtime" / "training" / "qwen3_8b" / "20260914" / "pre_approval_release" / "production_candidate_authorization.json"

    triage = load(triage_path)
    recovery = load(recovery_path)
    closure = load(closure_path)
    predictions = find_predictions(predictions_path)
    authorization = load(auth_path) if auth_path.exists() else {}

    rows: list[dict[str, Any]] = []
    for finding in triage.get("p0_findings", []):
        sku = str(finding["sku"])
        evidence = predictions.get(sku, {})
        source = evidence.get("source", {})
        prediction = evidence.get("prediction", {})
        expected = evidence.get("expected", {})
        field = finding.get("field")
        rows.append(
            {
                "sku": sku,
                "field": field,
                "severity": "P0",
                "kind": finding.get("kind"),
                "review_status": "PENDING_OWNER_REMEDIATION",
                "source_value": source.get(field) if field else None,
                "current_prediction": prediction.get(field) if field else None,
                "reference_value": expected.get(field) if field else None,
                "source_numbers": finding.get("source_numbers", []),
                "prediction_numbers": finding.get("prediction_numbers", []),
                "missing_numbers": finding.get("missing_numbers", []),
                "extra_numbers": finding.get("extra_numbers", []),
                "source_technical_tokens": finding.get("source_technical_tokens", []),
                "missing_technical_tokens": finding.get("missing_technical_tokens", []),
                "owner_final_value": None,
                "training_eligible": False,
                "production_writes": False,
            }
        )

    report = {
        "report_id": "stage4-p0-remediation-queue-20260914-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "queue_scope": "10 confirmed P0 findings from immutable 485-row Stage 4 evaluation",
        "source_artifacts": {
            "triage": {"path": str(triage_path), "sha256": sha256(triage_path)},
            "recovery": {"path": str(recovery_path), "sha256": sha256(recovery_path)},
            "closure": {"path": str(closure_path), "sha256": sha256(closure_path)},
            "predictions": {"path": str(predictions_path), "sha256": sha256(predictions_path)},
        },
        "gate": {
            "stage4_state": recovery.get("state"),
            "full_eval_hard_fact_zero": recovery.get("gate_results", {}).get("FULL_EVAL_HARD_FACT_ZERO"),
            "full_stage4_release": recovery.get("gate_results", {}).get("FULL_STAGE4_RELEASE"),
            "production_candidate_authorized": authorization.get("production_candidate_authorized", False),
            "production_switch_allowed": authorization.get("production_switch_allowed", False),
            "production_switch_performed": authorization.get("production_switch_performed", False),
        },
        "confirmed_p0_count": len(rows),
        "remediation_candidates_pending_gold": closure.get("remediation_candidates", {}).get("pending_human_gold_confirmation", 0),
        "rows": rows,
        "safety": {
            "model_changed": False,
            "gold_changed": False,
            "master_changed": False,
            "sqlite_changed": False,
            "dictionary_changed": False,
            "production_writes": False,
        },
        "next_gate": "Owner must supply corrected values and independent source/hash review; then rerun the same frozen 485-row evaluation. Do not set FULL_STAGE4_RELEASE=true before P0=0 and all regression gates pass.",
    }

    json_path = out / "stage4_p0_remediation_queue.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = out / "stage4_p0_remediation_queue.csv"
    fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})

    md_lines = [
        "# Stage 4 P0 Remediation Queue — 2026-09-14",
        "",
        "只读队列：不修改模型、Gold、Master、SQLite、字典或生产开关。",
        "",
        f"- Stage 4 状态：`{report['gate']['stage4_state']}`",
        f"- FULL_EVAL_HARD_FACT_ZERO：`{report['gate']['full_eval_hard_fact_zero']}`",
        f"- FULL_STAGE4_RELEASE：`{report['gate']['full_stage4_release']}`",
        f"- 已授权候选但允许切换：`{report['gate']['production_switch_allowed']}`",
        f"- 已确认 P0：**{report['confirmed_p0_count']}**",
        f"- 待人工 Gold 的 remediation 候选：**{report['remediation_candidates_pending_gold']}**",
        "",
        "## P0 队列",
        "",
        "| SKU | 字段 | 类型 | 当前状态 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        md_lines.append(f"| {row['sku']} | {row['field']} | {row['kind']} | `{row['review_status']}` |")
    md_lines += [
        "",
        "## 放行条件",
        "",
        "1. Owner 为每条 P0 提供修正值并确认源事实；",
        "2. 修正集独立通过 source/hash、数字/技术 token、中文质量和泄漏检查；",
        "3. 在同一冻结 485 条测试集上重跑，P0 必须为 0；",
        "4. 所有回归门禁通过后，才允许重新生成 Stage 4 closure 并评估是否设置 `FULL_STAGE4_RELEASE=true`。",
    ]
    (out / "STAGE4_P0_REMEDIATION_QUEUE.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "markdown": str(out / "STAGE4_P0_REMEDIATION_QUEUE.md"), "p0": len(rows), "pending_gold": report["remediation_candidates_pending_gold"], "production_writes": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
