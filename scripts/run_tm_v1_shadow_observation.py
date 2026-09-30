"""Observe TM V1 Shadow Termbase against the complete Phase A candidate queue."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--termbase", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    termbase_path = args.termbase.resolve()
    queue_path = args.queue.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    termbase = read_jsonl(termbase_path)
    queue = read_jsonl(queue_path)

    global_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    context_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in termbase:
        if row["termbase_scope"] == "EXACT_GLOBAL_TM":
            global_map.setdefault((row["field_name"], row["source_value_raw"]), []).append(row)
        else:
            context_map[(row["field_name"], row["source_value_raw"], row["source_hash"])] = row

    observations: list[dict[str, Any]] = []
    for row in queue:
        field = row.get("field", row.get("field_name"))
        source = row.get("source_value_raw")
        target = row.get("target_value")
        candidates = global_map.get((field, source), [])
        context_hit = context_map.get((field, source, row.get("source_hash")))
        if len({c["target_value"] for c in candidates}) > 1:
            outcome = "CONFLICT_BLOCKED"
            matched_target = None
        elif context_hit is not None:
            outcome = "CONTEXT_ONLY_HIT"
            matched_target = context_hit["target_value"]
        elif candidates:
            matched_target = candidates[0]["target_value"]
            outcome = "GLOBAL_HIT" if matched_target == target else "GLOBAL_TARGET_MISMATCH"
        else:
            matched_target = None
            outcome = "NO_SHADOW_HIT"
        observations.append({
            "sku": row.get("sku"),
            "field": field,
            "source_hash": row.get("source_hash"),
            "source_value_raw": source,
            "queue_target_value": target,
            "shadow_target_value": matched_target,
            "outcome": outcome,
            "candidate_status": row.get("candidate_status"),
            "shadow_only": True,
            "production_write": False,
        })

    counts = Counter(row["outcome"] for row in observations)
    write_path = out / "tm_v1_shadow_observations.jsonl"
    write_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in observations), encoding="utf-8")
    audit = {
        "artifact_type": "ACTION_TM_V1_SHADOW_OBSERVATION",
        "queue_path": str(queue_path),
        "queue_sha256": sha256_file(queue_path),
        "termbase_path": str(termbase_path),
        "termbase_sha256": sha256_file(termbase_path),
        "queue_rows": len(queue),
        "observation_rows": len(observations),
        "outcome_counts": dict(counts),
        "unexpected_target_mismatch": counts.get("GLOBAL_TARGET_MISMATCH", 0),
        "conflict_blocked": counts.get("CONFLICT_BLOCKED", 0),
        "isolated_or_unmatched": counts.get("NO_SHADOW_HIT", 0),
        "production_writes": False,
        "status": "PASS" if len(queue) == len(observations) and counts.get("GLOBAL_TARGET_MISMATCH", 0) == 0 and counts.get("CONFLICT_BLOCKED", 0) == 0 else "REVIEW",
    }
    (out / "tm_v1_shadow_observation_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Shadow Observation — 2026-09-15",
        "",
        "对完整 Phase A 候选队列执行只读 Shadow 观测，不产生生产写入。",
        "",
        f"- queue rows: {len(queue)}",
        f"- outcomes: {dict(counts)}",
        f"- target mismatch: {audit['unexpected_target_mismatch']}",
        f"- conflict blocked: {audit['conflict_blocked']}",
        f"- status: {audit['status']}",
    ]
    (out / "TM_V1_SHADOW_OBSERVATION_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
