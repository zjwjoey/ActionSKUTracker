"""Create a read-only Stage 4 data/gate audit report.

The audit does not alter model, Master, SQLite, or dictionary artifacts.  It
separates owner-reviewed evidence from formal training Gold and reports every
remaining release blocker, including disagreements between closure reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runtime" / "training" / "qwen3_8b" / "20260911"


def load(name: str) -> dict[str, Any]:
    with (RUN / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    out = Path(args.output_dir) if args.output_dir else ROOT / "runtime" / "training" / "qwen3_8b" / "20260913" / "stage4_audit"
    out.mkdir(parents=True, exist_ok=True)

    # Prefer the current versioned source-bound closure when present.  The
    # older 2026-09-12 report remains historical evidence and must not
    # override the current release decision.
    current_closure = ROOT / "runtime/training/qwen3_8b/20260914/stage4_source_bound_closure/stage4_source_bound_closure_report_20260914.json"
    current_state = RUN / "stage4_recovery_state.json"
    if current_closure.exists() and current_state.exists():
        closure = json.loads(current_closure.read_text(encoding="utf-8"))
        state = json.loads(current_state.read_text(encoding="utf-8"))
        release = bool(closure.get("FULL_STAGE4_RELEASE")) and bool(state.get("gate_results", {}).get("FULL_STAGE4_RELEASE"))
        report = {
            "report_id": "stage4-data-audit-current-source-bound-20260914-v1",
            "read_only": True,
            "source_artifacts": {
                "current_closure": {"path": str(current_closure), "sha256": digest(current_closure)},
                "recovery_state": {"path": str(current_state), "sha256": digest(current_state)},
            },
            "state": state.get("state"),
            "gate_results": state.get("gate_results", {}),
            "release_decision": "RELEASED" if release else "BLOCKED",
            "frozen_full_eval": closure.get("full_eval", {}),
            "findings": closure.get("findings", {}),
            "release_mode": closure.get("release_mode"),
            "blockers": [] if release else closure.get("blocked_reasons", []),
            "safety": closure.get("safety", {}),
        }
        json_path = out / "stage4_data_audit_current.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md = [
            "# Stage 4 Data Audit — Current",
            "",
            f"结论：`{report['release_decision']}`。当前放行模式：`{report['release_mode']}`。",
            "",
            f"- 状态：`{report['state']}`",
            f"- P0：{report['findings'].get('confirmed_model_p0_count', 0)}；P1：{report['findings'].get('confirmed_model_p1_count', 0)}",
            f"- FULL_STAGE4_RELEASE：`{release}`",
            "- 生产写入：`false`",
            "",
            "历史 2026-09-12 审计文件保留为历史证据，不覆盖当前 versioned closure。",
        ]
        (out / "STAGE4_DATA_AUDIT_CURRENT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(json.dumps({"json": str(json_path), "markdown": str(out / 'STAGE4_DATA_AUDIT_CURRENT.md'), "release_decision": report["release_decision"], "p0": report["findings"].get("confirmed_model_p0_count", 0), "p1": report["findings"].get("confirmed_model_p1_count", 0)}, ensure_ascii=False))
        return 0

    state = load("stage4_recovery_state.json")
    owner = load("stage4_owner_confirmed_closure_20260912.json")
    full = load("stage4_stage5_owner_signed_full_closure_recheck_20260912.json")
    final = load("stage4_final_failure_certification.json")
    recheck = load("stage4_closure_recheck_20260912.json")
    remediation = load("stage4_remediation200_final_model_approved_20260912.manifest.json")
    screening = load("stage4_retraining_selection_100_screening_v2_20260912.json")

    owner_conflicts = set(owner.get("findings", {}).get("source_conflict_skus", []))
    recheck_conflicts = set(recheck.get("source_conflict_skus", []))
    full_conflicts = set(full.get("owner_signed_closure", {}).get("stage4_source_conflict_skus", []))
    disagreement = sorted((owner_conflicts | recheck_conflicts | full_conflicts) - (owner_conflicts & recheck_conflicts & full_conflicts))
    p0 = final.get("p0_count", 0)
    p1 = final.get("p1_count", 0)
    report = {
        "report_id": "stage4-data-audit-20260913-v1",
        "read_only": True,
        "source_artifacts": {
            name: {"path": str(RUN / name), "sha256": digest(RUN / name)}
            for name in [
                "stage4_recovery_state.json",
                "stage4_owner_confirmed_closure_20260912.json",
                "stage4_stage5_owner_signed_full_closure_recheck_20260912.json",
                "stage4_final_failure_certification.json",
                "stage4_closure_recheck_20260912.json",
                "stage4_remediation200_final_model_approved_20260912.manifest.json",
                "stage4_retraining_selection_100_screening_v2_20260912.json",
            ]
        },
        "state": state.get("state"),
        "gate_results": state.get("gate_results", {}),
        "owner_review": {
            "silver_rows_reviewed": owner.get("silver", {}).get("owner_confirmed"),
            "silver_pending": owner.get("silver", {}).get("pending"),
            "owner_gold_rows": full.get("owner_signed_closure", {}).get("stage4_owner_approved_gold"),
            "source_conflicts_by_owner_closure": sorted(owner_conflicts),
            "source_conflicts_by_recheck": sorted(recheck_conflicts),
            "source_conflicts_by_full_closure": sorted(full_conflicts),
            "source_conflict_id_disagreement": disagreement,
        },
        "frozen_full_eval": {
            "rows": full.get("full_eval", {}).get("rows"),
            "field_values": full.get("full_eval", {}).get("field_values"),
            "json_parse_rate": full.get("full_eval", {}).get("json_parse_rate"),
            "schema_pass_rate": full.get("full_eval", {}).get("schema_pass_rate"),
            "category_valid_rate": full.get("full_eval", {}).get("category_valid_rate"),
            "numeric_preservation_rate": full.get("full_eval", {}).get("numeric_preservation_rate"),
            "numeric_hallucination_rate": full.get("full_eval", {}).get("numeric_hallucination_rate"),
            "spanish_residual_rate": full.get("full_eval", {}).get("spanish_residual_rate"),
            "checker_issue_sku_count": full.get("full_eval", {}).get("checker_issue_sku_count"),
            "confirmed_model_p0_count": len(full.get("findings", {}).get("confirmed_model_p0", [])),
            "p1_count": len(full.get("findings", {}).get("p1_followups", [])),
        },
        "remediation": {
            "rows": remediation.get("rows"),
            "approved_gold_rows": remediation.get("gold_rows"),
            "training_eligible_rows": remediation.get("training_eligible_rows"),
            "human_confirmation_required": remediation.get("human_confirmation_required"),
            "selection_screening_rows": screening.get("rows"),
            "selection_disjoint_candidates": screening.get("disjoint_candidates"),
            "selection_historical_overlap_reference_only": screening.get("historical_overlap_reference_only"),
            "selection_training_eligible_rows": screening.get("training_eligible_rows"),
        },
        "release_decision": "BLOCKED",
        "blockers": [
            "FULL_EVAL_HARD_FACT_ZERO=false; frozen 485-row evaluation still has 10 confirmed model P0 and 1 P1.",
            "Stage4 full release=false; training_authorized=false; production_writes_authorized=false.",
            "Remediation 200 has 0 approved Gold and 0 training-eligible rows; owner decisions remain pending.",
            "Source-conflict SKU lists disagree across closure reports; reconcile before any Gold promotion.",
        ],
        "safety": {"master_changed": False, "sqlite_changed": False, "dictionary_changed": False, "production_writes": False},
    }
    json_path = out / "stage4_data_audit_20260913.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = [
        "# Stage 4 Data Audit — 2026-09-13",
        "",
        "结论：Stage 4 仍不能作为正式训练/生产输入；本报告只读，不修改任何数据。",
        "",
        f"- 状态：`{report['state']}`",
        f"- 冻结全量评估：{report['frozen_full_eval']['rows']} 行 / {report['frozen_full_eval']['field_values']} 字段值",
        f"- 确认的模型 P0：{report['frozen_full_eval']['confirmed_model_p0_count']}；P1：{report['frozen_full_eval']['p1_count']}",
        f"- Owner Gold：{report['owner_review']['owner_gold_rows']}；Remediation Gold：{report['remediation']['approved_gold_rows']}",
        f"- Stage 4 FULL_RELEASE：`{state.get('gate_results', {}).get('FULL_STAGE4_RELEASE')}`",
        "",
        "## 仍需处理",
        "",
        *[f"- {item}" for item in report["blockers"]],
        "",
        "## 特别发现",
        "",
        f"不同报告中的 source-conflict SKU：owner closure={sorted(owner_conflicts)}；recheck={sorted(recheck_conflicts)}；full closure={sorted(full_conflicts)}。差异={disagreement}。",
        "",
        "## 下一步",
        "",
        "1. 先为 10 个 P0 建立不与冻结 485 重叠的人工 Gold 修复集。",
        "2. 补齐 remediation 的 owner decision，并重新计算 source-hash/字段事实门禁。",
        "3. 在 P0=0、Gold 足够且冲突清单统一前，只允许 Stage 5 offline shadow，不允许训练或生产写入。",
    ]
    (out / "STAGE4_DATA_AUDIT_20260913.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(out / 'STAGE4_DATA_AUDIT_20260913.md'), "release_decision": "BLOCKED", "p0": p0, "p1": p1, "source_conflict_disagreement": disagreement}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
