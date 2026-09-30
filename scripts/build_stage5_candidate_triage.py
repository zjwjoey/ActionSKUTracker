"""Build an auditable triage register for a Stage 5 adapter benchmark.

This is deliberately a review artifact, not a gate override.  It preserves
the benchmark evidence verbatim and records the current disposition of each
hard-error sample so a later owner decision can be replayed without changing
the benchmark itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# The map is intentionally explicit.  Unknown samples fail closed as
# OWNER_REVIEW_REQUIRED instead of being silently treated as false positives.
DISPOSITIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("3211585", "name"): ("MODEL_ERROR", "Restore model token SL-300; current Gold also needs owner correction (remove hallucinated True)."),
    ("3211585", "details"): ("EVALUATOR_OR_GOLD_REVIEW", "Technical facts and numbers are preserved; wording difference requires owner review, not gate bypass."),
    ("3216674", "name"): ("POLICY_REVIEW", "Brand/model retention policy is ambiguous; do not auto-approve or discard the source token."),
    ("2529028", "spec"): ("MODEL_ERROR", "Decimal-comma dimensions were split incorrectly; numeric facts are not preserved."),
    ("3008204", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "Semantic output preserves the source facts; TECH_TOKEN signal needs evaluator review."),
    ("3211619", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "36h, ENC, Siri, Google Assistant and Bluetooth 5.4 are preserved; TECH_TOKEN signal appears evaluator-only."),
    ("3211619", "details"): ("EVALUATOR_OR_GOLD_REVIEW", "USB-C, USB, 0.2m and Bluetooth 6.0 are preserved; wording difference needs review."),
    ("3225515", "description"): ("MODEL_ERROR", "2 en 1 was rendered as 双面; the functional numeric fact is dropped."),
    ("3212773", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "The extra 2 corresponds to source 'dos dispositivos'; source/Gold alignment needs review."),
    ("3212773", "details"): ("EVALUATOR_OR_GOLD_REVIEW", "Technical facts are preserved; formatting-only difference needs review."),
    ("2534669", "details"): ("SOURCE_CONFLICT_REVIEW", "Source contains bare 9 and 9 Voltio; unit normalization is ambiguous and must remain blocked."),
    ("3225181", "description"): ("MODEL_ERROR", "repelente al agua was overclaimed as 防水; use 防泼水/拒水 and retain source meaning."),
    ("2564956", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "一/one-size is a source phrase; numeric signal is likely evaluator-only."),
    ("3005109", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "dos puntas / dos lados are faithfully represented; numeric signal is likely evaluator-only."),
    ("3008877", "description"): ("EVALUATOR_OR_GOLD_REVIEW", "Un animal is a source phrase; generic Chinese 一个动物 should not be treated as a hallucinated count."),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    candidate = next(
        model for model in benchmark.get("models", [])
        if model.get("name") == "field_conditioned_qlora"
    )
    issues = candidate.get("issues", [])
    rows: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for issue in issues:
        key = (str(issue.get("sku") or ""), str(issue.get("field") or ""))
        disposition, action = DISPOSITIONS.get(key, ("OWNER_REVIEW_REQUIRED", "No explicit disposition is frozen."))
        row = {
            "sku": key[0],
            "field": key[1],
            "reasons": list(issue.get("reasons") or []),
            "source": issue.get("source", ""),
            "expected": issue.get("expected", ""),
            "prediction": issue.get("prediction", ""),
            "missing_numbers": list(issue.get("missing_numbers") or []),
            "extra_numbers": list(issue.get("extra_numbers") or []),
            "disposition": disposition,
            "owner_action": action,
        }
        rows.append(row)
        if disposition == "OWNER_REVIEW_REQUIRED":
            unknown.append(row)

    summary = Counter(row["disposition"] for row in rows)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    register = output / "stage5_candidate_hard_error_triage.json"
    payload = {
        "artifact_type": "STAGE5_CANDIDATE_HARD_ERROR_TRIAGE_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(args.benchmark.resolve()),
        "benchmark_sha256": sha256(args.benchmark),
        "benchmark_model": candidate.get("name"),
        "benchmark_rows": benchmark.get("rows"),
        "candidate_hard_error_count": candidate.get("hard_error_count"),
        "issue_count": len(rows),
        "disposition_counts": dict(sorted(summary.items())),
        "unknown_disposition_count": len(unknown),
        "production_gate_override": False,
        "production_writes": False,
        "issues": rows,
    }
    register.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    markdown = output / "STAGE5_CANDIDATE_HARD_ERROR_TRIAGE.md"
    lines = [
        "# Stage 5 候选适配器硬错误分流",
        "",
        "本文件只记录审核分流，不修改冻结基准、不放宽生产门禁、不执行生产写入。",
        "",
        f"- 基准行数：`{benchmark.get('rows')}`",
        f"- 候选硬错误：`{candidate.get('hard_error_count')}`",
        f"- 问题记录：`{len(rows)}`",
        f"- 分流统计：`{dict(sorted(summary.items()))}`",
        f"- 未冻结分流：`{len(unknown)}`",
        "",
        "| SKU | 字段 | 原因 | 当前分流 | 处理意见 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        reason = ", ".join(row["reasons"])
        action = row["owner_action"].replace("|", "\\|")
        lines.append(f"| {row['sku']} | {row['field']} | {reason} | `{row['disposition']}` | {action} |")
    lines += [
        "",
        "## 当前结论",
        "",
        "真实模型错误和源数据冲突仍然阻断生产；评估器疑似误报也必须经过 Owner 审核后，才能写入显式例外。",
        "任何分流都不等于 Production PASS。必须在修正 Gold/Guard/策略后重新运行冻结基准并生成新的 release manifest。",
    ]
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "register": str(register),
        "report": str(markdown),
        "issue_count": len(rows),
        "unknown_disposition_count": len(unknown),
        "disposition_counts": dict(sorted(summary.items())),
        "production_gate_override": False,
        "production_writes": False,
    }, ensure_ascii=False))
    return 0 if not unknown else 2


if __name__ == "__main__":
    raise SystemExit(main())
