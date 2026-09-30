"""Reconcile TM V1 Shadow candidates with current dictionary snapshots, read-only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--termbase", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--target-db-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    termbase_path = args.termbase.resolve()
    dictionary_dir = args.dictionary_dir.resolve()
    db_snapshot = args.target_db_snapshot.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(termbase_path)
    term_rows = read_csv(dictionary_dir / "term_dictionary.csv")
    category_rows = read_csv(dictionary_dir / "category_dictionary.csv")
    target_db_snapshot_sha256 = sha256_file(db_snapshot)
    term_map: dict[str, set[str]] = {}
    for row in term_rows:
        term_map.setdefault(row.get("term_es", ""), set()).add(row.get("term_zh", ""))
    category_map: dict[tuple[str, str], set[str]] = {}
    for row in category_rows:
        category_map.setdefault((row.get("cat1_es", ""), row.get("cat2_es", "")), set()).add(row.get("cat2_zh", "") or row.get("cat1_zh", ""))

    preview: list[dict[str, Any]] = []
    for row in rows:
        field = row["field_name"]
        source = row["source_value_raw"]
        target = row["target_value"]
        if field == "cat1":
            existing = {r.get("cat1_zh", "") for r in category_rows if r.get("cat1_es", "") == source}
        elif field == "cat2":
            existing = {r.get("cat2_zh", "") for r in category_rows if r.get("cat2_es", "") == source}
        else:
            existing = term_map.get(source, set())
        existing.discard("")
        if target in existing and len(existing) == 1:
            action = "NO_CHANGE"
            conflict = "NONE"
        elif target in existing and len(existing) > 1:
            action = "NO_CHANGE"
            conflict = "EXISTING_TARGET_AMBIGUITY"
        elif existing:
            action = "WOULD_UPDATE"
            conflict = "EXISTING_TARGET_CONFLICT"
        else:
            action = "WOULD_ADD"
            conflict = "NONE"
        preview.append({
            **row,
            "apply_action": action,
            "conflict_status": conflict,
            "existing_targets": sorted(existing),
            "target_snapshot_sha256": target_db_snapshot_sha256,
            "production_write": False,
        })

    out_jsonl = out / "tm_v1_dictionary_reconciled_apply_preview.jsonl"
    out_jsonl.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in preview), encoding="utf-8")
    from collections import Counter
    actions = Counter(row["apply_action"] for row in preview)
    conflicts = Counter(row["conflict_status"] for row in preview)
    manifest = {
        "artifact_type": "ACTION_TM_V1_DICTIONARY_RECONCILED_APPLY_PREVIEW",
        "mode": "READ_ONLY_PREVIEW",
        "termbase_sha256": sha256_file(termbase_path),
        "target_db_snapshot_sha256": target_db_snapshot_sha256,
        "dictionary_files": {p.name: sha256_file(p) for p in sorted(dictionary_dir.iterdir()) if p.is_file()},
        "rows": len(preview),
        "apply_action_counts": dict(actions),
        "conflict_status_counts": dict(conflicts),
        "production_writes": False,
        "dictionary_writes": False,
        "sqlite_writes": False,
        "master_writes": False,
        "status": "PASS" if not conflicts.get("EXISTING_TARGET_CONFLICT") else "REVIEW",
        "owner_gate": "Production apply remains unauthorized; review WOULD_UPDATE and conflicts first.",
    }
    (out / "tm_v1_dictionary_reconciled_apply_preview_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Dictionary Reconciled Apply Preview — 2026-09-15",
        "",
        "对当前 Dictionary/SQLite 快照只读比对，不执行生产写入。",
        "",
        f"- rows: {len(preview)}",
        f"- actions: {dict(actions)}",
        f"- conflicts: {dict(conflicts)}",
        f"- status: {manifest['status']}",
    ]
    (out / "TM_V1_DICTIONARY_RECONCILED_APPLY_PREVIEW.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
