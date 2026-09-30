"""Build a read-only acceptance snapshot for a Stage 5 adapter candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_head(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def model_metrics(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in report["models"] if item.get("name") == name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--default-benchmark", type=Path, required=True)
    parser.add_argument("--production-prompt-benchmark", type=Path, required=True)
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    benchmark = load(args.benchmark)
    default_benchmark = load(args.default_benchmark)
    production_prompt = load(args.production_prompt_benchmark)
    triage = load(args.triage)
    shadow = load(args.shadow_manifest)
    candidate = model_metrics(benchmark, "field_conditioned_qlora")
    default_candidate = model_metrics(default_benchmark, "field_conditioned_qlora")
    production_candidate = model_metrics(production_prompt, "field_conditioned_qlora")

    blockers: list[str] = []
    if candidate.get("hard_error_count") != 0:
        blockers.append(f"FROZEN_BENCHMARK_HARD_ERRORS={candidate.get('hard_error_count')}")
    if candidate.get("numeric_preservation_rate") != 1:
        blockers.append("FROZEN_BENCHMARK_NUMERIC_PRESERVATION_NOT_1")
    if candidate.get("numeric_hallucination_rate") != 0:
        blockers.append("FROZEN_BENCHMARK_NUMERIC_HALLUCINATION_NOT_0")
    if candidate.get("json_parse_rate") != 1 or candidate.get("field_schema_rate") != 1:
        blockers.append("FROZEN_BENCHMARK_SCHEMA_NOT_100")
    if shadow.get("summary", {}).get("review_required", 0) > 0:
        blockers.append(f"SHADOW_REVIEW_REQUIRED={shadow['summary']['review_required']}")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "artifact_type": "STAGE5_CANDIDATE_ACCEPTANCE_SNAPSHOT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head(args.root),
        "candidate_adapter": benchmark.get("adapter_path"),
        "benchmark_prompt_mode": benchmark.get("prompt_mode", "dataset_system"),
        "frozen_benchmark": {
            "rows": benchmark.get("rows"),
            "candidate_metrics": candidate,
            "default_prompt_candidate_metrics": default_candidate,
            "production_prompt_diagnostic_metrics": production_candidate,
        },
        "triage": {
            "path": str(args.triage.resolve()),
            "sha256": sha256(args.triage),
            "issue_count": triage.get("issue_count"),
            "candidate_hard_error_count": triage.get("candidate_hard_error_count"),
            "disposition_counts": triage.get("disposition_counts"),
            "production_gate_override": False,
        },
        "shadow": {
            "path": str(args.shadow_manifest.resolve()),
            "sha256": sha256(args.shadow_manifest),
            "summary": shadow.get("summary"),
            "master_written": shadow.get("master_written", False),
            "dictionary_written": shadow.get("dictionary_written", False),
            "sqlite_written": shadow.get("sqlite_written", False),
        },
        "guard_source": {
            "path": str((args.root / "src/action_tracker/translation/model_guard.py").resolve()),
            "sha256": sha256(args.root / "src/action_tracker/translation/model_guard.py"),
        },
        "production": {
            "release_state": "BLOCKED_FROZEN_BENCHMARK" if blockers else "READY_FOR_OWNER_APPROVAL",
            "production_switch": False,
            "production_writes": False,
            "blockers": blockers,
        },
    }
    path = output / "stage5_candidate_acceptance_snapshot.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = output / "STAGE5_CANDIDATE_ACCEPTANCE_SNAPSHOT.md"
    lines = [
        "# Stage 5 候选适配器验收快照",
        "",
        f"- 状态：`{snapshot['production']['release_state']}`",
        f"- 冻结基准：`{benchmark.get('rows')}` 行",
        f"- 候选硬错误：`{candidate.get('hard_error_count')}`",
        f"- 数字保留率/新增率：`{candidate.get('numeric_preservation_rate')}` / `{candidate.get('numeric_hallucination_rate')}`",
        f"- Shadow：`{shadow.get('summary', {}).get('accepted_by_guard')}` 自动通过，`{shadow.get('summary', {}).get('review_required')}` 待审核",
        f"- 生产写入：`{snapshot['production']['production_writes']}`",
        "",
        "## 阻断项",
        "",
    ]
    lines.extend(f"- `{item}`" for item in blockers) or lines.append("- 无")
    lines += [
        "",
        "## 结论",
        "",
        "该快照不修改任何生产数据，也不覆盖候选适配器。只有冻结基准通过、Shadow 审核策略闭合并重新生成 release manifest 后，才可请求 Owner 授权正式切换。",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"snapshot": str(path), "report": str(report), "release_state": snapshot["production"]["release_state"], "blockers": blockers}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
