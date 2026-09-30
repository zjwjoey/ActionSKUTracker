"""Create a non-writing Dictionary Apply Preview for the TM V1 Shadow termbase."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--termbase", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    termbase_path = args.termbase.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(termbase_path)

    preview = []
    for row in rows:
        preview.append({
            "tm_id": row["tm_id"],
            "field_name": row["field_name"],
            "source_value_raw": row["source_value_raw"],
            "target_value": row["target_value"],
            "source_hash": row["source_hash"],
            "pair_hash": row["pair_hash"],
            "termbase_scope": row["termbase_scope"],
            "apply_action": "WOULD_REGISTER_SHADOW",
            "conflict_status": "NONE",
            "target_snapshot_status": "NOT_CAPTURED",
            "production_write": False,
        })

    preview_path = out / "tm_v1_dictionary_apply_preview.jsonl"
    preview_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in preview), encoding="utf-8")
    manifest = {
        "artifact_type": "ACTION_TM_V1_DICTIONARY_APPLY_PREVIEW",
        "mode": "SHADOW_PREVIEW_ONLY",
        "source_termbase": str(termbase_path),
        "source_termbase_sha256": sha256_file(termbase_path),
        "rows": len(preview),
        "global_rows": sum(1 for row in preview if row["termbase_scope"] == "EXACT_GLOBAL_TM"),
        "context_only_rows": sum(1 for row in preview if row["termbase_scope"] == "SKU_FIELD_SOURCE_HASH_ONLY"),
        "apply_action_counts": {"WOULD_REGISTER_SHADOW": len(preview)},
        "conflict_status_counts": {"NONE": len(preview)},
        "target_snapshot_status": "NOT_CAPTURED",
        "preflight_status": "BLOCKED_TARGET_SNAPSHOT_REQUIRED",
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "next_required_owner_gate": "Capture current Dictionary/SQLite target snapshot and re-run exact hash freshness before any production apply.",
    }
    (out / "tm_v1_dictionary_apply_preview_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Dictionary Apply Preview — 2026-09-15",
        "",
        "本文件是只读预览，不执行 Dictionary、SQLite 或 Master 写入。",
        "",
        f"- preview rows: {len(preview)}",
        f"- global TM: {manifest['global_rows']}",
        f"- context-only: {manifest['context_only_rows']}",
        "- conflicts in frozen Shadow source: 0",
        "- target snapshot: NOT_CAPTURED",
        "- preflight: BLOCKED_TARGET_SNAPSHOT_REQUIRED",
    ]
    (out / "TM_V1_DICTIONARY_APPLY_PREVIEW.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
