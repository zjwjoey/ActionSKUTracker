"""Stage-4 recovery state machine and gate recorder.

This controller records evidence and authorizes only offline shadow actions.
It never trains, writes Master/SQLite/dictionaries, or promotes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATE_DIR = ROOT / "runtime/training/qwen3_8b/20260911"
STATE_PATH = DATE_DIR / "stage4_recovery_state.json"
EVIDENCE_PATH = DATE_DIR / "stage4_evidence_freeze_manifest.json"
FULL_CLOSURE_PATH = DATE_DIR / "stage4_stage5_owner_signed_full_closure_recheck_20260912.json"
AI_OWNER_READY_PATH = DATE_DIR / "stage4_ai_owner_ready_recheck_20260912.json"
OWNER_CONFIRMED_PATH = DATE_DIR / "stage4_owner_confirmed_closure_20260912.json"
SOURCE_BOUND_CLOSURE_PATH = ROOT / "runtime/training/qwen3_8b/20260914/stage4_source_bound_closure/stage4_source_bound_closure_report_20260914.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(item for item in path.rglob("*") if item.is_file()):
        rel = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(8, "big")); digest.update(rel); digest.update(bytes.fromhex(sha(item)))
    return digest.hexdigest()


def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def closure_gate_snapshot() -> dict[str, Any] | None:
    """Return the versioned closure gates when a current report is available.

    The historical strict audit is intentionally not used as the current
    release decision: it contains the superseded NUMERIC_DROPPED diagnosis.
    This helper keeps the controller read-only while making the current
    evidence report authoritative for the closure-specific gates.
    """

    if SOURCE_BOUND_CLOSURE_PATH.exists():
        current_path = SOURCE_BOUND_CLOSURE_PATH
    elif OWNER_CONFIRMED_PATH.exists():
        current_path = OWNER_CONFIRMED_PATH
    elif AI_OWNER_READY_PATH.exists():
        current_path = AI_OWNER_READY_PATH
    else:
        current_path = FULL_CLOSURE_PATH
    if not current_path.exists():
        return None
    report = read_json(current_path)
    gates = report.get("gate_results") or {}
    findings = report.get("findings") or {}
    return {
        "path": str(current_path.resolve()),
        "sha256": sha(current_path),
        "numeric_source_loss_corrected": bool(gates.get("NUMERIC_SOURCE_LOSS_CORRECTED")),
        "full_eval_hard_fact_zero": bool(gates.get("FULL_EVAL_HARD_FACT_ZERO")),
        "full_stage4_release": bool(report.get("FULL_STAGE4_RELEASE")),
        "p0_count": int(findings.get("confirmed_model_p0_count") or 0),
        "silver_coverage_blocked": bool((report.get("silver") or {}).get("owner_coverage_complete") is False) or any("silver" in str(reason).lower() for reason in report.get("blocked_reasons", [])),
        "verdict": report.get("verdict"),
    }


def freeze_evidence() -> dict[str, Any]:
    base = ROOT / "runtime/models/Qwen3-8B"
    combined = DATE_DIR / "combined_gold_incremental/formal_qlora_200_earlystop/adapter"
    field = DATE_DIR / "field_conditioned_v1/formal_qlora_200_earlystop/adapter"
    inputs = {
        "test_only_485": DATE_DIR / "stage4_test_only_485.jsonl",
        "contract": DATE_DIR / "stage4_inference_contract.json",
        "strict_audit": DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_audit.json",
        "audit_rows": DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_rows.csv",
        "smoke20": DATE_DIR / "legacy_adapter_diagnostic_smoke20_v2.json",
        "pipeline_rule_first_smoke": DATE_DIR / "stage5_rule_first_smoke_current.manifest.json",
        "pipeline_entrypoint": ROOT / "scripts/qwen_offline_stage5.py",
    }
    if FULL_CLOSURE_PATH.exists():
        inputs["owner_signed_full_closure"] = FULL_CLOSURE_PATH
    if AI_OWNER_READY_PATH.exists():
        inputs["ai_owner_ready_recheck"] = AI_OWNER_READY_PATH
    if OWNER_CONFIRMED_PATH.exists():
        inputs["owner_confirmed_closure"] = OWNER_CONFIRMED_PATH
    if SOURCE_BOUND_CLOSURE_PATH.exists():
        inputs["source_bound_closure"] = SOURCE_BOUND_CLOSURE_PATH
    missing = [str(path) for path in inputs.values() if not path.exists()]
    if missing:
        raise SystemExit("EVIDENCE_MISSING: " + "; ".join(missing))
    document = {
        "manifest_version": "stage4-evidence-freeze-v2-rule-first-shadow",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "git": {"commit": git("rev-parse", "HEAD"), "worktree_clean": git("status", "--porcelain") == ""},
        "runtime": {"system_python": sys.executable, "python": platform.python_version(), "platform": platform.platform()},
        "base_model": {"path": str(base.resolve()), "config_sha256": sha(base / "config.json")},
        "adapters": {
            "combined": {"path": str(combined.resolve()), "tree_sha256": tree_sha(combined)},
            "field_conditioned": {"path": str(field.resolve()), "tree_sha256": tree_sha(field)},
        },
        "inputs": {key: {"path": str(path.resolve()), "sha256": sha(path)} for key, path in inputs.items()},
        "scope": "offline evidence only; no production write; no training authorization",
    }
    EVIDENCE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def pipeline_shadow_ok(summary: dict[str, Any]) -> bool:
    """Validate only the safety shape of an offline rule-first smoke run."""

    rows = summary.get("rows")
    accepted = summary.get("accepted_by_guard")
    review = summary.get("review_required")
    model_gaps = summary.get("model_gap_fields")
    model_calls = summary.get("model_inference_fields")
    return (
        isinstance(rows, int)
        and rows >= 1
        and isinstance(accepted, int)
        and isinstance(review, int)
        and accepted + review == rows
        and summary.get("json_parse_failures") == 0
        and summary.get("production_writes") is False
        and isinstance(model_gaps, int)
        and model_gaps >= 0
        and model_calls == model_gaps
    )


def build_state(evidence: dict[str, Any]) -> dict[str, Any]:
    strict = read_json(DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_audit.json")
    smoke = read_json(DATE_DIR / "legacy_adapter_diagnostic_smoke20_v2.json")
    pipeline = read_json(DATE_DIR / "stage5_rule_first_smoke_current.manifest.json")
    strict_ok = (
        strict.get("candidate_count") == 500
        and strict.get("source_hash_match_count") == 500
        and strict.get("split_overlap_count") == 0
    )
    contract_ok = Path(evidence["inputs"]["contract"]["path"]).exists()
    smoke_ok = (
        smoke.get("rows") == 20
        and smoke.get("json_parse_rate") == 1.0
        and smoke.get("field_schema_rate") == 1.0
        and smoke.get("category_valid_rate") == 1.0
    )
    pipeline_summary = pipeline.get("summary") or {}
    pipeline_ok = pipeline_shadow_ok(pipeline_summary)
    closure = closure_gate_snapshot()
    gates = {
        "EVIDENCE_FREEZE": evidence.get("git", {}).get("commit") is not None,
        "DATASET_CERTIFICATION": strict_ok,
        "CONTRACT_FREEZE": contract_ok,
        "ADAPTER_SMOKE": smoke_ok,
        "PIPELINE_SHADOW_SMOKE": pipeline_ok,
        "NUMERIC_SOURCE_LOSS_CORRECTED": bool(closure and closure["numeric_source_loss_corrected"]),
        "FULL_EVAL_HARD_FACT_ZERO": bool(closure and closure["full_eval_hard_fact_zero"]),
        "FULL_STAGE4_RELEASE": bool(closure and closure["full_stage4_release"]),
    }
    if gates.get("FULL_STAGE4_RELEASE") and gates.get("FULL_EVAL_HARD_FACT_ZERO"):
        state = "STAGE4_RELEASED_SOURCE_BOUND_RESOLVER"
        next_actions = ["run Stage 5 under the source-bound resolver contract", "keep training authorization false until its independent gate passes", "keep production writes false until their independent gate passes"]
    elif all(gates[key] for key in ("EVIDENCE_FREEZE", "DATASET_CERTIFICATION", "CONTRACT_FREEZE", "ADAPTER_SMOKE", "PIPELINE_SHADOW_SMOKE")):
        state = "READY_FOR_STAGE5_OFFLINE_SHADOW"
        next_actions = ["run dictionary/rule resolver on NEW/source_hash_changed/NEEDS_REVIEW", "send only resolver gaps to adapter", "route every Guard failure to Review Queue", "do not write Master or production dictionary"]
    else:
        state = "BLOCKED_BEFORE_STAGE5"
        next_actions = ["repair failed gate and rerun controller"]
    blocked_reasons = []
    if closure is None:
        blocked_reasons.append("current versioned Stage 4 closure report is missing")
    else:
        if not closure["numeric_source_loss_corrected"]:
            blocked_reasons.append("current closure has unresolved source numeric-loss evidence")
        if not closure["full_eval_hard_fact_zero"]:
            blocked_reasons.append(f"current full evaluation has {closure['p0_count']} field-level model P0 findings")
        if closure["silver_coverage_blocked"]:
            blocked_reasons.append("full release coverage remains incomplete because the test-only Silver set is not owner-signed Gold")
        if closure["full_stage4_release"] is False and not blocked_reasons:
            blocked_reasons.append("current closure report does not authorize FULL_STAGE4_RELEASE")
    document = {
        "state_machine": "BASELINE_UNFROZEN -> EVIDENCE_FROZEN -> DATASET_CERTIFIED -> CONTRACT_FROZEN -> ADAPTER_REEVALUATED -> READY_FOR_STAGE5_OFFLINE_SHADOW",
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "input_manifest_hashes": {"evidence": sha(EVIDENCE_PATH), "strict_audit": sha(DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_audit.json"), "contract": sha(DATE_DIR / "stage4_inference_contract.json"), **({"current_closure": closure["sha256"]} if closure else {})},
        "gate_results": gates,
        "diagnostic_metrics": {
            "adapter_smoke20": smoke,
            "pipeline_rule_first_smoke": pipeline_summary,
            "strict_test_audit": strict,
            "strict_audit_interpretation": {
                "NUMERIC_DROPPED": "HISTORICAL_ONLY_NOT_CURRENT_RELEASE_BLOCKER",
                "current_numeric_source_loss_count": 0 if closure and closure["numeric_source_loss_corrected"] else None,
                "authority": "current_full_closure_report",
            },
            "current_full_closure": closure,
        },
        "training_authorized": False,
        "production_writes_authorized": False,
        "next_allowed_actions": next_actions,
        "blocked_reasons": blocked_reasons,
    }
    STATE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    evidence = freeze_evidence() if args.freeze else read_json(EVIDENCE_PATH)
    state = build_state(evidence)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
