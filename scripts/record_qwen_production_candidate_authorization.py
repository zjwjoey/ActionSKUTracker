"""Record Owner authorization without bypassing the Stage 4 release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--gold-audit", type=Path, required=True)
    parser.add_argument("--recovery-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    release = json.loads(args.release_manifest.read_text(encoding="utf-8"))
    gold = json.loads(args.gold_audit.read_text(encoding="utf-8"))
    recovery = json.loads(args.recovery_state.read_text(encoding="utf-8"))
    release_checks_pass = release.get("release_state") == "READY_FOR_OWNER_RELEASE_AUTHORIZATION"
    gold_pass = gold.get("result") == "PASS"
    full_release = bool(recovery.get("gate_results", {}).get("FULL_STAGE4_RELEASE"))
    production_switch_allowed = release_checks_pass and gold_pass and full_release
    output = {
        "artifact_type": "QWEN_PRODUCTION_CANDIDATE_OWNER_AUTHORIZATION",
        "artifact_version": "qwen-production-candidate-authorization-v1",
        "authorized_at": datetime.now(timezone.utc).isoformat(),
        "owner_authorization": "CONFIRMED",
        "candidate": release.get("candidate", {}),
        "release_manifest": {"path": str(args.release_manifest.resolve()), "sha256": sha256_file(args.release_manifest)},
        "gold_audit": {"path": str(args.gold_audit.resolve()), "sha256": sha256_file(args.gold_audit), "result": gold.get("result")},
        "stage4_recovery": {
            "path": str(args.recovery_state.resolve()),
            "sha256": sha256_file(args.recovery_state),
            "state": recovery.get("state"),
            "full_stage4_release": full_release,
        },
        "checks": {
            "release_manifest_ready": release_checks_pass,
            "gold_final_audit_pass": gold_pass,
            "stage4_full_release": full_release,
        },
        "production_candidate_authorized": release_checks_pass and gold_pass,
        "production_switch_allowed": production_switch_allowed,
        "production_switch_performed": False,
        "production_writes": False,
        "decision": (
            "AUTHORIZED_AND_READY_FOR_PRODUCTION_SWITCH"
            if production_switch_allowed else
            "AUTHORIZED_CANDIDATE_BLOCKED_BY_STAGE4_FULL_RELEASE_FALSE"
        ),
        "required_next_gate": "STAGE4_FULL_RELEASE_TRUE_BEFORE_MODEL_SWITCH",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "decision": output["decision"], "production_switch": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
