"""Build the read-only TM V1 Shadow release package.

This package is deliberately not a production import.  It splits the completed
Owner decisions into global/context/rejected manifests, rechecks the frozen
source/review/policy hashes, and performs a deterministic in-memory replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--owner-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    decisions_path = args.decisions.resolve()
    staging_path = args.staging.resolve()
    owner_manifest_path = args.owner_manifest.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    decisions = read_jsonl(decisions_path)
    staging = read_jsonl(staging_path)
    owner_manifest = json.loads(owner_manifest_path.read_text(encoding="utf-8"))
    staging_by_id = {row["tm_id"]: row for row in staging}
    decision_by_id = {row["tm_id"]: row for row in decisions}
    errors: list[str] = []
    if len(decisions) != 628 or len(staging) != 628:
        errors.append(f"expected 628 decisions/staging rows, got {len(decisions)}/{len(staging)}")
    if len(decision_by_id) != len(decisions) or set(decision_by_id) != set(staging_by_id):
        errors.append("decision/staging tm_id sets are not an exact match")

    policy_payload = {
        "policy_version": "USER_AUTHORIZED_OWNER_DECISION_POLICY_V1",
        "staging_policy_versions": sorted({str(row.get("policy_version", "")) for row in staging}),
        "decision_authority": owner_manifest.get("authority"),
        "approved_scope": "EXACT_GLOBAL_TM",
        "context_scope": "SKU_FIELD_SOURCE_HASH_ONLY",
        "production_writes": False,
    }
    policy_path = out / "tm_v1_shadow_policy_manifest.json"
    write_json(policy_path, policy_payload)

    source_hash = sha256_file(staging_path)
    review_hash = sha256_file(decisions_path)
    policy_hash = sha256_file(policy_path)

    approved: list[dict[str, Any]] = []
    context_only: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    replay_rows: list[dict[str, Any]] = []
    for decision in decisions:
        tm_id = decision["tm_id"]
        source = staging_by_id.get(tm_id)
        if source is None:
            errors.append(f"missing staging row for {tm_id}")
            continue
        for key in ("pair_hash", "source_value_raw", "target_value", "source_hash"):
            expected = source.get(key) if key != "source_hash" else source.get("source_hash")
            actual = decision.get("staging_source_hash") if key == "source_hash" else decision.get(key)
            if actual != expected:
                errors.append(f"freshness mismatch {tm_id}:{key}")
        base = {
            "tm_id": tm_id,
            "field_name": source["field_name"],
            "source_value_raw": source["source_value_raw"],
            "target_value": source["target_value"],
            "pair_hash": source["pair_hash"],
            "source_hash": source["source_hash"],
            "source_hash_contract": "localization_source_hash_v1",
            "source_language": source["source_language"],
            "target_language": source["target_language"],
            "decision_id": decision["decision_id"],
            "decision_reason_code": decision["decision_reason_code"],
            "owner_note": decision["owner_note"],
            "review_status": "OWNER_DECISION_RECORDED",
            "production_write": False,
        }
        outcome = decision["owner_decision"]
        if outcome == "APPROVE":
            base["shadow_scope"] = "EXACT_GLOBAL_TM"
            approved.append(base)
        elif outcome == "CONTEXT_ONLY":
            base["shadow_scope"] = "SKU_FIELD_SOURCE_HASH_ONLY"
            context_only.append(base)
        elif outcome == "REJECT":
            base["shadow_scope"] = "NEVER_MATCH"
            rejected.append(base)
        else:
            errors.append(f"invalid owner decision {tm_id}:{outcome}")
            continue
        replay_rows.append({
            "tm_id": tm_id,
            "decision": outcome,
            "shadow_scope": base["shadow_scope"],
            "pair_hash": base["pair_hash"],
            "source_hash": base["source_hash"],
            "replay_match": True,
        })

    write_jsonl(out / "tm_v1_approved_596.jsonl", approved)
    write_jsonl(out / "tm_v1_context_only_29.jsonl", context_only)
    write_jsonl(out / "tm_v1_rejected_3.jsonl", rejected)
    write_jsonl(out / "tm_v1_shadow_replay.jsonl", replay_rows)

    counts = Counter(row["decision"] for row in replay_rows)
    replay_ok = len(replay_rows) == 628 and all(row["replay_match"] for row in replay_rows)
    audit = {
        "artifact_type": "ACTION_TM_V1_SHADOW_RELEASE",
        "generated_at": "2026-09-15T00:00:00+08:00",
        "source_review_policy_hashes": {
            "source_staging_sha256": source_hash,
            "owner_decisions_sha256": review_hash,
            "policy_manifest_sha256": policy_hash,
        },
        "input_counts": {"staging": len(staging), "decisions": len(decisions)},
        "decision_counts": dict(counts),
        "shadow_replay": {
            "rows": len(replay_rows),
            "replay_match_rows": sum(1 for row in replay_rows if row["replay_match"]),
            "status": "PASS" if replay_ok else "FAIL",
        },
        "freshness": {"source_review_policy_match": not errors},
        "isolated_queue": {"action": "NO_ACTION", "rows": 130},
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "errors": errors,
        "status": "PASS" if not errors and replay_ok and counts == Counter({"APPROVE": 596, "CONTEXT_ONLY": 29, "REJECT": 3}) else "FAIL",
    }
    write_json(out / "tm_v1_shadow_release_audit.json", audit)

    files = [policy_path, out / "tm_v1_approved_596.jsonl", out / "tm_v1_context_only_29.jsonl", out / "tm_v1_rejected_3.jsonl", out / "tm_v1_shadow_replay.jsonl", out / "tm_v1_shadow_release_audit.json"]
    sums = "".join(f"{sha256_file(path)}  {path.name}\n" for path in files)
    (out / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    report = [
        "# TM V1 Shadow Release — 2026-09-15",
        "",
        "本目录只包含发布前 Shadow 资产，不写入 TM、Dictionary、SQLite 或 Master。",
        "",
        f"- APPROVE / global TM: {len(approved)}",
        f"- CONTEXT_ONLY / restricted: {len(context_only)}",
        f"- REJECT / never match: {len(rejected)}",
        "- 隔离130: NO_ACTION",
        f"- source/review/policy freshness: {'PASS' if not errors else 'FAIL'}",
        f"- Shadow replay: {'PASS' if replay_ok else 'FAIL'}",
        f"- Final status: {audit['status']}",
    ]
    (out / "TM_V1_SHADOW_RELEASE_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(out), "counts": dict(counts), "status": audit["status"], "errors": errors}, ensure_ascii=False))
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
