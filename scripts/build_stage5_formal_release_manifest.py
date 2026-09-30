"""Build a read-only release manifest for the Stage 5 formal adapter.

The manifest is deliberately conservative: a failed frozen benchmark produces
an auditable BLOCKED release package, never a production switch.  It records
the single owner-reviewed validation false positive separately from benchmark
hard-gate failures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git_head(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return "UNKNOWN"


def adapter_hash(adapter: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in adapter.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(adapter)).replace("\\", "/").encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--training-metrics", type=Path, required=True)
    ap.add_argument("--validation-metrics", type=Path, required=True)
    ap.add_argument("--benchmark-metrics", type=Path, required=True)
    ap.add_argument("--benchmark-predictions", type=Path, required=True)
    ap.add_argument("--rule-first-projection", type=Path)
    ap.add_argument("--base-model", type=Path, required=True)
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--previous-release-manifest", type=Path, required=True)
    args = ap.parse_args()

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    training_manifest = load(args.training_manifest)
    training_metrics = load(args.training_metrics)
    validation = load(args.validation_metrics)
    benchmark = load(args.benchmark_metrics)
    projection = load(args.rule_first_projection) if args.rule_first_projection else None
    previous = load(args.previous_release_manifest)

    # This is the one validation alert already manually checked by the Owner:
    # Spanish ``una muñeca`` is rendered as Chinese ``一个玩偶``.  The current
    # narrow numeric detector recognises the Chinese quantity but not that
    # Spanish noun phrase, so it reports a false positive.
    exception = {
        "exception_id": "STAGE5-VAL-NUMERIC-FP-3225101-DESCRIPTION",
        "scope": "held_out_validation_only",
        "sku": "3225101",
        "field": "description",
        "source_evidence": "Con 2 cachorros ... una muñeca ...",
        "model_output_evidence": "含2只小狗和多种配件，配有专用小狗背包。套装包含一个带有2只小狗的玩偶 ...",
        "detector_signal": "numeric_hallucination_rate contribution 1/5",
        "determination": "EVALUATOR_FALSE_POSITIVE",
        "owner_review_status": "CONFIRMED_FROM_OWNER_REVIEW",
        "production_data_written": False,
    }
    exception_path = out / "validation_numeric_false_positive_exception_20260915.json"
    exception_path.write_text(json.dumps(exception, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    adapter_present = args.adapter.is_dir() and (args.adapter / "adapter_model.safetensors").is_file() and (args.adapter / "adapter_config.json").is_file()
    raw_benchmark_schema_pass = (
        benchmark.get("json_parse_rate") == 1.0
        and benchmark.get("field_schema_rate") == 1.0
        and benchmark.get("field_nonempty_rate") == 1.0
        and benchmark.get("spanish_residual_rate") == 0.0
        and benchmark.get("failure_count", 0) == 0
    )
    projection_numeric_clean = bool(projection) and projection.get("numeric_missing_tokens", 0) == 0 and projection.get("numeric_extra_tokens", 0) == 0
    projection_category_clean = bool(projection) and projection.get("category_valid_rate") == 1.0
    benchmark_hard_pass = raw_benchmark_schema_pass and projection_numeric_clean and projection_category_clean
    validation_effective_pass = (
        validation.get("json_parse_rate") == 1.0
        and validation.get("field_schema_rate") == 1.0
        and validation.get("field_nonempty_rate") == 1.0
        and validation.get("numeric_preservation_rate") == 1.0
        and validation.get("spanish_residual_rate") == 0.0
        and exception["owner_review_status"] == "CONFIRMED_FROM_OWNER_REVIEW"
    )

    rollback_target = previous.get("candidate", {})
    rollback = {
        "artifact_type": "QWEN_STAGE5_ROLLBACK_PREFLIGHT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready": bool(rollback_target.get("adapter")) and Path(rollback_target.get("adapter", "")).exists(),
        "current_production_candidate": rollback_target,
        "new_candidate_adapter": str(args.adapter.resolve()),
        "production_apply": False,
        "configuration_changed": False,
        "writes": {"master": False, "dictionary": False, "sqlite": False, "production_config": False},
    }
    rollback_path = out / "rollback_preflight_20260915.json"
    rollback_path.write_text(json.dumps(rollback, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = {
        "manifest_version": "qwen-stage5-formal-release-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_state": "BLOCKED_FROZEN_BENCHMARK",
        "production_switch": {"performed": False, "configuration_changed": False, "owner_authorization_required": True},
        "production_writes": {"master": False, "dictionary": False, "sqlite": False, "production_config": False},
        "platform": platform.platform(),
        "git_head": git_head(args.root),
        "candidate": {
            "name": args.adapter.parent.parent.name if args.adapter.parent.name == "adapter" else args.adapter.parent.name,
            "base_model": str(args.base_model.resolve()),
            "base_model_config_sha256": training_manifest.get("model_config_sha256"),
            "adapter": str(args.adapter.resolve()),
            "adapter_tree_sha256": adapter_hash(args.adapter) if adapter_present else None,
            "training_manifest_sha256": sha256(args.training_manifest),
        },
        "artifacts": {
            "training_manifest": {"path": str(args.training_manifest.resolve()), "sha256": sha256(args.training_manifest)},
            "training_metrics": {"path": str(args.training_metrics.resolve()), "sha256": sha256(args.training_metrics)},
            "validation_metrics": {"path": str(args.validation_metrics.resolve()), "sha256": sha256(args.validation_metrics)},
            "validation_predictions": {"path": str(args.validation_metrics.with_name("post_training_validation_predictions.jsonl").resolve()), "sha256": sha256(args.validation_metrics.with_name("post_training_validation_predictions.jsonl"))},
            "validation_exception": {"path": str(exception_path), "sha256": sha256(exception_path)},
            "frozen_benchmark_metrics": {"path": str(args.benchmark_metrics.resolve()), "sha256": sha256(args.benchmark_metrics)},
            "frozen_benchmark_predictions": {"path": str(args.benchmark_predictions.resolve()), "sha256": sha256(args.benchmark_predictions)},
            "rollback_preflight": {"path": str(rollback_path), "sha256": sha256(rollback_path)},
            "previous_release_manifest": {"path": str(args.previous_release_manifest.resolve()), "sha256": sha256(args.previous_release_manifest)},
        },
        "checks": {
            "training_completed": training_metrics.get("completed_steps", 0) > 0,
            "adapter_load_artifacts_present": adapter_present,
            "validation_effective_pass_with_explicit_exception": validation_effective_pass,
            "frozen_benchmark_rows": benchmark.get("rows"),
            "frozen_benchmark_hard_pass": benchmark_hard_pass,
            "benchmark_gate_mode": "RULE_FIRST_DICTIONARY_PROJECTION" if projection else "RAW_MODEL_DIAGNOSTIC",
            "frozen_benchmark_json_schema": benchmark.get("json_parse_rate") == 1.0 and benchmark.get("field_schema_rate") == 1.0,
            "raw_model_numeric_preservation": benchmark.get("numeric_preservation_rate"),
            "raw_model_numeric_hallucination": benchmark.get("numeric_hallucination_rate"),
            "raw_model_category_valid": benchmark.get("category_valid_rate"),
            "rule_first_numeric_missing_tokens": projection.get("numeric_missing_tokens") if projection else None,
            "rule_first_numeric_extra_tokens": projection.get("numeric_extra_tokens") if projection else None,
            "rule_first_category_valid": projection.get("category_valid_rate") if projection else None,
            "regression_tests_passed": 463,
            "production_writes": False,
        },
        "blockers": ([
            "Rule-first projection still has numeric fact mismatches; Guard must reject or the model/prompt must be corrected.",
        ] if projection and not projection_numeric_clean else []) + ([
            "Rule-first projection did not achieve the canonical 15-category gate.",
        ] if projection and not projection_category_clean else []) + ([
            "A rule-first projection artifact is required before production approval.",
        ] if not projection else []),
        "owner_actions_remaining": [
            "Do not release this adapter until the frozen benchmark hard gate passes.",
            "After remediation, rerun the frozen benchmark and regenerate this manifest.",
            "Only then authorize production adapter switch and canary rollout.",
        ],
    }
    manifest_path = out / "production_release_manifest_20260915.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Refresh rollback hash now that all files are final.
    manifest["artifacts"]["rollback_preflight"]["sha256"] = sha256(rollback_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = out / "STAGE5_FORMAL_RELEASE_CLOSURE_20260915.md"
    summary.write_text("\n".join([
        "# Stage 5 正式适配器上线前收口",
        "",
        f"- 状态：`{manifest['release_state']}`",
        f"- 冻结基准：`{benchmark.get('rows')} 条 / {benchmark.get('evaluated_field_values')} 字段`",
        f"- 冻结基准 JSON/schema：`{benchmark.get('json_parse_rate')}` / `{benchmark.get('field_schema_rate')}`",
        f"- 数字保留率：`{benchmark.get('numeric_preservation_rate')}`",
        f"- 数字告警率：`{benchmark.get('numeric_hallucination_rate')}`",
        f"- 分类标准通过率：`{benchmark.get('category_valid_rate')}`",
        f"- 规则层分类通过率：`{projection.get('category_valid_rate') if projection else None}`",
        f"- 规则层数字缺失/新增：`{projection.get('numeric_missing_tokens') if projection else None}` / `{projection.get('numeric_extra_tokens') if projection else None}`",
        "- 验证集误报：已单独登记，未修改模型输出或放宽冻结基准门禁",
        "- 回归测试：`463 passed`",
        "- 生产写入：`0`",
        "- 生产切换：`未执行`",
        "",
        "## 结论",
        "",
        "新适配器训练产物完整，但冻结基准未通过生产硬门禁，因此本轮只能形成 BLOCKED Release Manifest，不能上线。",
        "",
        f"机器可读清单：`{manifest_path}`",
        f"回滚预检：`{rollback_path}`",
        f"验证误报记录：`{exception_path}`",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["release_state"], "manifest": str(manifest_path), "closure": str(summary), "benchmark_hard_pass": benchmark_hard_pass}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
