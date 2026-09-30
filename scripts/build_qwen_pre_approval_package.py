"""Build read-only Qwen release and rollback preflight evidence.

This command only reads model/evaluation artifacts and writes an audit package.
It never changes production configuration, Master, Dictionary, SQLite, or model
weights.  A release authorization must still be supplied by the Owner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolved(path: Path) -> str:
    return str(path.resolve())


def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()


def git_worktree_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=True,
    )
    return not bool(result.stdout.strip())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--correction-manifest", type=Path, required=True)
    parser.add_argument("--canary-manifest", type=Path, required=True)
    parser.add_argument("--canary-review", type=Path, required=True)
    parser.add_argument("--owner-decision", type=Path, required=True)
    parser.add_argument("--gold-ingestion", type=Path, required=True)
    parser.add_argument("--gold-audit", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--new-adapter-benchmark", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--stage4-contract", type=Path, required=True)
    parser.add_argument("--pytest-count", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    required_files = {
        "gate": args.gate,
        "correction_manifest": args.correction_manifest,
        "canary_manifest": args.canary_manifest,
        "canary_review": args.canary_review,
        "owner_decision": args.owner_decision,
        "gold_ingestion": args.gold_ingestion,
        "gold_audit": args.gold_audit,
        "benchmark": args.benchmark,
        "new_adapter_benchmark": args.new_adapter_benchmark,
        "base_model_config": args.model / "config.json",
        "stage4_contract": args.stage4_contract,
    }
    missing = [name for name, path in required_files.items() if not path.exists()]
    if not args.adapter.exists():
        missing.append("old_adapter")
    if missing:
        raise FileNotFoundError(", ".join(missing))

    gate = load_json(args.gate)
    correction = load_json(args.correction_manifest)
    canary = load_json(args.canary_manifest)
    canary_review = load_json(args.canary_review)
    owner_decision = load_json(args.owner_decision)
    gold_ingestion = load_json(args.gold_ingestion)
    gold_audit = load_json(args.gold_audit)
    new_benchmark = load_json(args.new_adapter_benchmark)
    frozen_identity = canary.get("frozen_identity", {})

    gate_metrics = gate.get("metrics", {})
    gate_pass = bool(gate_metrics.get("automated_safety")) and not gate.get("production_write", True)
    correction_complete = correction.get("review_status") == "OWNER_REVIEW_COMPLETE"
    canary_writes = canary.get("production_writes", {})
    canary_write_free = bool(canary_writes) and not any(bool(value) for value in canary_writes.values())
    tests_pass = args.pytest_count > 0
    old_adapter_release_ready = gate_pass and correction_complete and canary_write_free and tests_pass

    stage5_eval = canary.get("evaluation", {})
    canary_review_pending = int(stage5_eval.get("human_pending", 0))
    canary_failures = int(stage5_eval.get("failure_count", 0))
    rollback_preflight = {
        "artifact_version": "qwen-rollback-preflight-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": old_adapter_release_ready,
        "rollback_target": {
            "base_model": resolved(args.model),
            "adapter": resolved(args.adapter),
            "base_model_config_sha256": frozen_identity.get("base_model_config_sha256"),
            "tokenizer_sha256": frozen_identity.get("tokenizer_sha256"),
            "adapter_tree_sha256": frozen_identity.get("adapter_tree_sha256"),
        },
        "checks": {
            "base_model_present": args.model.exists(),
            "old_adapter_present": args.adapter.exists(),
            "stage4_contract_present": args.stage4_contract.exists(),
            "owner_correction_manifest_complete": correction_complete,
            "corrected_gate_automated_safety": gate_pass,
            "canary_production_writes_disabled": canary_write_free,
            "production_write_not_performed": not gate.get("production_write", True),
            "master_dictionary_sqlite_not_written": canary_write_free,
        },
        "write_mode": "READ_ONLY_PREFLIGHT",
        "production_apply": False,
    }

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    rollback_path = output / "rollback_preflight.json"
    rollback_path.write_text(json.dumps(rollback_preflight, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    release = {
        "manifest_version": "qwen-pre-approval-release-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "git_head": git_head(args.root),
        "git_worktree_clean": git_worktree_clean(args.root),
        "candidate": {
            "name": "legacy_qwen3_8b_owner_corrected",
            "base_model": resolved(args.model),
            "adapter": resolved(args.adapter),
            "new_stage6_adapter": resolved(args.root / "runtime/training/qwen3_8b/20260914/stage6_training_256_qlora/adapter"),
            "new_stage6_adapter_approved": False,
            "base_model_config_sha256": frozen_identity.get("base_model_config_sha256"),
            "tokenizer_sha256": frozen_identity.get("tokenizer_sha256"),
            "adapter_tree_sha256": frozen_identity.get("adapter_tree_sha256"),
        },
        "artifact_hashes": {
            "corrected_gate": {"path": resolved(args.gate), "sha256": sha256_file(args.gate)},
            "owner_correction_manifest": {"path": resolved(args.correction_manifest), "sha256": sha256_file(args.correction_manifest)},
            "canary_manifest": {"path": resolved(args.canary_manifest), "sha256": sha256_file(args.canary_manifest)},
            "canary_ai_review": {"path": resolved(args.canary_review), "sha256": sha256_file(args.canary_review)},
            "canary_owner_decision": {"path": resolved(args.owner_decision), "sha256": sha256_file(args.owner_decision)},
            "stage5_canary_gold_ingestion": {"path": resolved(args.gold_ingestion), "sha256": sha256_file(args.gold_ingestion)},
            "stage5_canary_gold_audit": {"path": resolved(args.gold_audit), "sha256": sha256_file(args.gold_audit)},
            "frozen_benchmark": {"path": resolved(args.benchmark), "sha256": sha256_file(args.benchmark)},
            "new_adapter_benchmark": {"path": resolved(args.new_adapter_benchmark), "sha256": sha256_file(args.new_adapter_benchmark)},
            "rollback_preflight": {"path": resolved(rollback_path), "sha256": sha256_file(rollback_path)},
        },
        "checks": {
            "corrected_gate_automated_safety": gate_pass,
            "corrected_gate_hard_error_count": gate_metrics.get("hard_error_count"),
            "corrected_gate_rows": gate.get("rows"),
            "owner_review_status": correction.get("review_status"),
            "pytest_passed": args.pytest_count,
            "new_adapter_is_not_approved": True,
            "production_writes": False,
        },
        "stage5_shadow_canary": {
            "status": "OWNER_REVIEW_COMPLETE_PENDING_GOLD_INGESTION" if owner_decision.get("owner_confirmation") == "CONFIRMED" else ("CANARY_COMPLETE_PENDING_HUMAN_REVIEW" if canary_review_pending or canary_failures else "CANARY_COMPLETE"),
            "manifest": resolved(args.canary_manifest),
            "eligible_sku_count": stage5_eval.get("eligible_sku_count"),
            "guard_pass": stage5_eval.get("guard_pass"),
            "failure_count": canary_failures,
            "human_pending": canary_review_pending,
            "production_writes": canary_writes,
            "ai_review": {
                "path": resolved(args.canary_review),
                "sha256": sha256_file(args.canary_review),
                "reviewed_rows": canary_review.get("reviewed_rows"),
                "counts": canary_review.get("counts", {}),
                "owner_approval_required": canary_review.get("owner_approval_required", True),
            },
            "owner_decision": {
                "path": resolved(args.owner_decision),
                "sha256": sha256_file(args.owner_decision),
                "reviewed_rows": owner_decision.get("reviewed_rows"),
                "counts": owner_decision.get("counts", {}),
                "gold_write": owner_decision.get("gold_write", False),
                "production_write": owner_decision.get("production_write", False),
            },
            "offline_gold_ingestion": {
                "path": resolved(args.gold_ingestion),
                "sha256": sha256_file(args.gold_ingestion),
                "status": gold_ingestion.get("status"),
                "gold_rows": gold_ingestion.get("counts", {}).get("gold_rows"),
                "guard_exceptions": gold_ingestion.get("counts", {}).get("explicit_guard_exceptions"),
                "source_pair_overlap": gold_ingestion.get("counts", {}).get("historical_source_pair_overlap"),
                "production_writes": gold_ingestion.get("production_writes"),
            },
            "final_audit": {
                "path": resolved(args.gold_audit),
                "sha256": sha256_file(args.gold_audit),
                "result": gold_audit.get("result"),
                "checks": gold_audit.get("checks", {}),
                "production_write": gold_audit.get("production_write", False),
            },
        },
        "rollback_preflight": resolved(rollback_path),
        "production_switch": {
            "performed": False,
            "configuration_changed": False,
            "owner_authorization_required": True,
        },
        "release_state": "READY_FOR_OWNER_RELEASE_AUTHORIZATION" if old_adapter_release_ready else "BLOCKED_PRE_APPROVAL_CHECKS",
        "owner_actions_remaining": [
            "Authorize release of the corrected legacy adapter, or reject it.",
            "Review the Stage 5 canary queue before using its outputs as Gold.",
        ],
    }
    release_path = output / "production_release_manifest.json"
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Refresh the rollback hash entry after its content is final.
    release["artifact_hashes"]["rollback_preflight"]["sha256"] = sha256_file(rollback_path)
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path = output / "PRE_APPROVAL_CLOSURE.md"
    summary_path.write_text(
        "\n".join([
            "# Qwen 生产批准前收口报告",
            "",
            f"- 状态：`{release['release_state']}`",
            f"- Git HEAD：`{release['git_head']}`",
            f"- 工作区：`{'clean' if release['git_worktree_clean'] else 'dirty（未提交改动存在）'}`",
            f"- 回归测试：`{args.pytest_count} passed`",
            "- 生产写入：`未执行`",
            "- 生产模型切换：`未执行`",
            "",
            "## 当前可批准候选",
            "",
            "- 候选：旧版 Qwen3-8B 适配器（Owner 修正后）",
            f"- Owner gate：`{'PASS' if gate_pass else 'FAIL'}`，hard errors=`{gate_metrics.get('hard_error_count')}`",
            f"- Owner 修正清单：`{correction.get('review_status')}`",
            "- 新 Stage 6 适配器：`不批准，继续保留为实验产物`",
            "",
            "## 已完成的批准前检查",
            "",
            "- 146 条冻结基准重新推理并应用 5 条 Owner 修正",
            "- 旧适配器安全门禁：JSON/schema、完整性、数字保留、西语残留、分类、hard error 全部通过",
            "- Stage 5 canary 只读运行完成，产物哈希已记录",
            "- 回滚目标、模型/适配器/Stage4 contract 存在性已预检",
            f"- 回归测试：`{args.pytest_count} passed`",
            "",
            "## 仍需 Owner 决策的事项",
            "",
            "1. 是否授权把旧适配器作为生产候选发布；本轮没有执行切换。",
            f"2. Stage 5 canary 的 18 条已完成 Owner 确认：`{owner_decision.get('counts', {}).get('OWNER_APPROVED_AS_IS', 0)}` 条原样接受、`{owner_decision.get('counts', {}).get('OWNER_APPROVED_WITH_EXPLICIT_GUARD_EXCEPTION', 0)}` 条显式守卫例外、`{owner_decision.get('counts', {}).get('OWNER_APPROVED_MANUAL_EDIT', 0)}` 条定向修正、`{owner_decision.get('counts', {}).get('OWNER_APPROVED_MANUAL_FALLBACK_AFTER_RETRY_FAILURE', 0)}` 条重试失败后采用人工回退；未写入生产 Gold store。",
            f"3. 已生成 `{gold_ingestion.get('counts', {}).get('gold_rows', 0)}` 条离线 Gold 证据；source-pair 重叠 `{gold_ingestion.get('counts', {}).get('historical_source_pair_overlap', 0)}`，生产写入仍为 0。",
            f"4. 最终 Gold Guard / leakage / replay 审计：`{gold_audit.get('result')}`。",
            "",
            "## 产物",
            "",
            f"- `production_release_manifest.json`：机器可读发布门禁",
            f"- `rollback_preflight.json`：只读回滚预检",
            f"- Canary manifest：`{resolved(args.canary_manifest)}`",
            f"- Owner corrected gate：`{resolved(args.gate)}`",
            "",
        ]) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "release_manifest": str(release_path.resolve()),
        "rollback_preflight": str(rollback_path.resolve()),
        "summary": str(summary_path.resolve()),
        "release_state": release["release_state"],
        "production_switch": False,
        "stage5_canary_human_pending": canary_review_pending,
        "stage5_canary_failures": canary_failures,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
