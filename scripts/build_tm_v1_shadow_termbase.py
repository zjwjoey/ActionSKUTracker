"""Build and replay a read-only Shadow Termbase from TM V1 release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    release = args.release_dir.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    approved = read_jsonl(release / "tm_v1_approved_596.jsonl")
    context = read_jsonl(release / "tm_v1_context_only_29.jsonl")
    source_audit = json.loads((release / "tm_v1_shadow_release_audit.json").read_text(encoding="utf-8"))
    input_hashes = {
        "release_audit_sha256": sha256_file(release / "tm_v1_shadow_release_audit.json"),
        "approved_sha256": sha256_file(release / "tm_v1_approved_596.jsonl"),
        "context_only_sha256": sha256_file(release / "tm_v1_context_only_29.jsonl"),
    }

    collisions: list[dict[str, Any]] = []
    global_index: dict[tuple[str, str], dict[str, Any]] = {}
    context_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for row in approved:
        key = (row["field_name"], row["source_value_raw"])
        if key in global_index and global_index[key]["target_value"] != row["target_value"]:
            collisions.append({"scope": "EXACT_GLOBAL_TM", "key": key, "existing": global_index[key]["target_value"], "incoming": row["target_value"]})
        global_index[key] = row
        entries.append({**row, "owner_decision": "APPROVE", "termbase_scope": "EXACT_GLOBAL_TM", "active": True})
    for row in context:
        key = (row["field_name"], row["source_value_raw"], row["source_hash"])
        context_index[key] = row
        entries.append({**row, "owner_decision": "CONTEXT_ONLY", "termbase_scope": "SKU_FIELD_SOURCE_HASH_ONLY", "active": True})

    termbase_path = out / "tm_v1_shadow_termbase.jsonl"
    write_jsonl(termbase_path, entries)
    replay: list[dict[str, Any]] = []
    for row in entries:
        if row["termbase_scope"] == "EXACT_GLOBAL_TM":
            hit = global_index.get((row["field_name"], row["source_value_raw"]))
        else:
            hit = context_index.get((row["field_name"], row["source_value_raw"], row["source_hash"]))
        replay.append({
            "tm_id": row["tm_id"],
            "scope": row["termbase_scope"],
            "replay_target": hit["target_value"] if hit else None,
            "expected_target": row["target_value"],
            "match": bool(hit and hit["target_value"] == row["target_value"]),
        })

    replay_path = out / "tm_v1_shadow_termbase_replay.jsonl"
    write_jsonl(replay_path, replay)
    manifest = {
        "artifact_type": "ACTION_TM_V1_SHADOW_TERMBASE",
        "release_status": "SHADOW_ONLY",
        "source_release_audit_status": source_audit.get("status"),
        "entries": len(entries),
        "global_entries": len(approved),
        "context_only_entries": len(context),
        "replay_rows": len(replay),
        "replay_matches": sum(1 for row in replay if row["match"]),
        "collision_count": len(collisions),
        "input_hashes": input_hashes,
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "isolation_policy": "130 isolated rows remain outside this termbase",
        "status": "PASS" if len(entries) == 625 and len(replay) == 625 and all(row["match"] for row in replay) and not collisions else "FAIL",
    }
    write_json(out / "tm_v1_shadow_termbase_manifest.json", manifest)
    write_json(out / "tm_v1_shadow_termbase_collisions.json", collisions)
    report = [
        "# TM V1 Shadow Termbase — 2026-09-15",
        "",
        "只读 Shadow 资产；不写入生产 Dictionary、SQLite 或 Master。",
        "",
        f"- global entries: {len(approved)}",
        f"- context-only entries: {len(context)}",
        f"- replay: {manifest['replay_matches']}/{manifest['replay_rows']}",
        f"- collisions: {len(collisions)}",
        f"- status: {manifest['status']}",
    ]
    (out / "TM_V1_SHADOW_TERMBASE_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
