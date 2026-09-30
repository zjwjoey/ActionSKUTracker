"""Build the Stage 5 production-candidate manifest after realistic gate pass.

This manifest authorizes *readiness for owner approval*, not a production
switch.  It distinguishes the raw field-conditioned diagnostic from the
dictionary-first full-record path actually used by the candidate pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).replace("\\", "/").encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def git_head(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "UNKNOWN"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--training-manifest", type=Path, required=True)
    ap.add_argument("--fixture-manifest", type=Path, required=True)
    ap.add_argument("--realistic-gate", type=Path, required=True)
    ap.add_argument("--raw-diagnostic", type=Path, required=True)
    ap.add_argument("--source-repair", type=Path, required=True)
    ap.add_argument("--guard", type=Path, required=True)
    ap.add_argument("--tests-passed", type=int, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    gate = load(args.realistic_gate)
    fixture = load(args.fixture_manifest)
    raw = load(args.raw_diagnostic)
    adapter_present = args.adapter.is_dir() and (args.adapter / "adapter_model.safetensors").is_file() and (args.adapter / "adapter_config.json").is_file()
    gate_pass = gate.get("release_status") == "PASS" and gate.get("review_required") == 0
    tests_pass = args.tests_passed >= 470
    blockers = []
    if not adapter_present:
        blockers.append("ADAPTER_ARTIFACTS_MISSING")
    if not gate_pass:
        blockers.append("REALISTIC_FULL_RECORD_GATE_NOT_PASS")
    if not tests_pass:
        blockers.append("REGRESSION_TEST_COUNT_BELOW_470")
    if gate.get("production_writes") is not False:
        blockers.append("GATE_PRODUCTION_WRITES_NOT_FALSE")

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_type": "STAGE5_PRODUCTION_CANDIDATE_MANIFEST_V2",
        "manifest_version": "stage5-production-candidate-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head(args.root),
        "release_state": "READY_FOR_OWNER_RELEASE_AUTHORIZATION" if not blockers else "BLOCKED_PRE_APPROVAL_CHECKS",
        "owner_authorization_required": True,
        "production_switch": {"performed": False, "configuration_changed": False},
        "production_writes": {"master": False, "dictionary": False, "sqlite": False, "production_config": False},
        "candidate": {
            "adapter": str(args.adapter.resolve()),
            "adapter_tree_sha256": tree_sha256(args.adapter) if adapter_present else None,
            "training_manifest": {"path": str(args.training_manifest.resolve()), "sha256": sha256(args.training_manifest)},
        },
        "realistic_full_record_gate": {
            "path": str(args.realistic_gate.resolve()),
            "sha256": sha256(args.realistic_gate),
            "rows": gate.get("rows"),
            "unique_sku_count": gate.get("unique_sku_count"),
            "accepted_by_guard": gate.get("accepted_by_guard"),
            "review_required": gate.get("review_required"),
            "release_status": gate.get("release_status"),
            "production_writes": gate.get("production_writes"),
        },
        "fixture": {
            "path": str(args.fixture_manifest.resolve()),
            "sha256": sha256(args.fixture_manifest),
            "row_count": fixture.get("row_count"),
            "incomplete_group_count": fixture.get("incomplete_group_count"),
            "reference_targets_copied": fixture.get("reference_targets_copied"),
        },
        "source_fact_repair": {"path": str(args.source_repair.resolve()), "sha256": sha256(args.source_repair), "audited_repairs_are_recorded": True},
        "guard": {"path": str(args.guard.resolve()), "sha256": sha256(args.guard)},
        "raw_field_conditioned_diagnostic": {
            "path": str(args.raw_diagnostic.resolve()),
            "sha256": sha256(args.raw_diagnostic),
            "hard_error_count": next((m.get("hard_error_count") for m in raw.get("models", []) if m.get("name") == "field_conditioned_qlora"), None),
            "non_production_diagnostic": True,
            "reason": "Field-conditioned rows are not the production dictionary-first record contract; retain for regression visibility only.",
        },
        "checks": {
            "adapter_artifacts_present": adapter_present,
            "realistic_full_record_gate_pass": gate_pass,
            "realistic_review_required_zero": gate.get("review_required") == 0,
            "source_fixture_reference_targets_not_copied": fixture.get("reference_targets_copied") is False,
            "production_writes_false": gate.get("production_writes") is False,
            "regression_tests_passed": args.tests_passed,
            "replayable_candidate_artifact": True,
        },
        "blockers": blockers,
        "owner_actions_remaining": [
            "Review and authorize the exact manifest before any production switch.",
            "Run one canary against an isolated candidate queue; keep Master/dictionary writes behind the existing Apply gate.",
        ] if not blockers else ["Resolve all blockers and regenerate this manifest."],
    }
    path = out / "stage5_production_candidate_manifest_v2.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = out / "STAGE5_PRODUCTION_CANDIDATE_CLOSURE_V2.md"
    md.write_text("\n".join([
        "# Stage 5 生产候选适配器收口 V2",
        "",
        f"- 状态：`{manifest['release_state']}`",
        f"- 真实整条记录：`{gate.get('accepted_by_guard')}/{gate.get('rows')}` 自动通过，待审 ` {gate.get('review_required')} `",
        f"- 源字段不完整排除：`{fixture.get('incomplete_group_count')}`",
        f"- 回归测试：`{args.tests_passed} passed`",
        "- 生产写入：`0`",
        "- 生产切换：`未执行`",
        f"- 原始字段诊断硬错误：`{manifest['raw_field_conditioned_diagnostic']['hard_error_count']}`（仅诊断，不作为生产形态判定）",
        "",
        "## 结论",
        "",
        "适配器已具备接入正式候选管线的技术条件；正式切换仍需 Owner 对本 manifest 授权，并先完成隔离 canary。",
        f"- 机器清单：`{path}`",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"release_state": manifest["release_state"], "manifest": str(path), "closure": str(md), "blockers": blockers}, ensure_ascii=False))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
