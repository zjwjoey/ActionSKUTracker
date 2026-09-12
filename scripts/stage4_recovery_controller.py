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
    gates = {
        "EVIDENCE_FREEZE": evidence.get("git", {}).get("commit") is not None,
        "DATASET_CERTIFICATION": strict_ok,
        "CONTRACT_FREEZE": contract_ok,
        "ADAPTER_SMOKE": smoke_ok,
        "PIPELINE_SHADOW_SMOKE": pipeline_ok,
        "FULL_STAGE4_RELEASE": False,
    }
    if all(gates[key] for key in ("EVIDENCE_FREEZE", "DATASET_CERTIFICATION", "CONTRACT_FREEZE", "ADAPTER_SMOKE", "PIPELINE_SHADOW_SMOKE")):
        state = "READY_FOR_STAGE5_OFFLINE_SHADOW"
        next_actions = ["run dictionary/rule resolver on NEW/source_hash_changed/NEEDS_REVIEW", "send only resolver gaps to adapter", "route every Guard failure to Review Queue", "do not write Master or production dictionary"]
    else:
        state = "BLOCKED_BEFORE_STAGE5"
        next_actions = ["repair failed gate and rerun controller"]
    document = {
        "state_machine": "BASELINE_UNFROZEN -> EVIDENCE_FROZEN -> DATASET_CERTIFIED -> CONTRACT_FROZEN -> ADAPTER_REEVALUATED -> READY_FOR_STAGE5_OFFLINE_SHADOW",
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "input_manifest_hashes": {"evidence": sha(EVIDENCE_PATH), "strict_audit": sha(DATE_DIR / "qwen_incremental_stage4_release500_strict_test_only_audit.json"), "contract": sha(DATE_DIR / "stage4_inference_contract.json")},
        "gate_results": gates,
        "diagnostic_metrics": {"adapter_smoke20": smoke, "pipeline_rule_first_smoke": pipeline_summary, "strict_test_audit": strict},
        "training_authorized": False,
        "production_writes_authorized": False,
        "next_allowed_actions": next_actions,
        "blocked_reasons": ["FULL_STAGE4_RELEASE remains false: 20-row numeric preservation was 0.991666..., not zero; 15/500 review candidates remain excluded from the strict set; current set is silver, not human Gold."],
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
