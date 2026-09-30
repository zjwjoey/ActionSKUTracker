"""Apply TM V1 SKU-scoped product overrides through a fail-closed gate.

Default mode is preview-only.  Commit requires an explicit flag and the
existing ``dictionary_apply.production_enabled`` configuration switch.  Every
row is rechecked against the current SQLite localization source hash and the
current manual-overrides hash before the atomic replacement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


HEADERS = ["scope", "key", "field", "value", "reason", "source", "locked", "updated_at"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_preview(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--dictionary-dir", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--settings", type=Path, default=Path(r"F:\ActionSKUTracker\config\settings.yaml"))
    args = parser.parse_args()
    preview_path = args.preview.resolve()
    dictionary_dir = args.dictionary_dir.resolve()
    source_db = args.source_db.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows = read_preview(preview_path)
    eligible = [row for row in rows if row.get("apply_action") == "WOULD_APPLY"]
    conflicts = [row for row in rows if row.get("apply_action") == "CONFLICT_EXISTING_OVERRIDE"]
    stale = [row for row in rows if row.get("apply_action") == "BLOCKED_STALE"]
    if conflicts or stale:
        gate_status = "BLOCKED_PREVIEW_NOT_CLEAN"
    else:
        gate_status = "READY_FOR_EXPLICIT_COMMIT"

    before_path = dictionary_dir / "manual_overrides.csv"
    before_hash = sha256_file(before_path)
    before_snapshot = out / "manual_overrides.before.csv"
    if not before_snapshot.exists():
        shutil.copy2(before_path, before_snapshot)

    plan = {
        "artifact_type": "ACTION_TM_V1_PRODUCT_OVERRIDE_APPLY",
        "preview_sha256": sha256_file(preview_path),
        "manual_overrides_before_sha256": before_hash,
        "eligible_rows": len(eligible),
        "blocked_stale_rows": len(stale),
        "conflict_rows": len(conflicts),
        "commit_requested": bool(args.commit),
        "production_write": False,
        "status": gate_status,
    }
    settings = yaml.safe_load(args.settings.read_text(encoding="utf-8")) or {}
    production_enabled = bool((settings.get("dictionary_apply") or {}).get("production_enabled", False))
    plan["production_enabled"] = production_enabled
    if args.commit:
        if not production_enabled:
            plan["status"] = "BLOCKED_PRODUCTION_GATE_CLOSED"
            plan["reason"] = "dictionary_apply.production_enabled=false; no production write performed"
        elif gate_status != "READY_FOR_EXPLICIT_COMMIT":
            plan["status"] = "BLOCKED_PREVIEW_NOT_CLEAN"
            plan["reason"] = "stale or conflict rows remain; no production write performed"
        else:
            with sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True) as db:
                current_hashes = {str(row[0]): str(row[1] or "") for row in db.execute("SELECT official_sku, source_hash FROM product_localizations WHERE language='es'")}
            freshness_failures = [
                row["key"]
                for row in eligible
                if current_hashes.get(row["key"]) != (row.get("current_source_hash_rechecked") or row.get("source_hash"))
            ]
            current_before_hash = sha256_file(before_path)
            if current_before_hash != before_hash or freshness_failures:
                plan["status"] = "BLOCKED_FRESHNESS_CHANGED"
                plan["reason"] = "target or source hash changed during preflight; no production write performed"
                plan["freshness_failures"] = freshness_failures
            else:
                with before_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    existing_rows = list(csv.DictReader(handle))
                existing_keys = {(row.get("scope", ""), row.get("key", ""), row.get("field", "")) for row in existing_rows}
                add_rows = []
                replace_rows = []
                duplicate_keys = []
                for row in eligible:
                    key = ("product", row["key"], row["field"])
                    if key in existing_keys:
                        if row.get("owner_decision") == "APPROVE_REPLACE_EXISTING":
                            replace_rows.append(row)
                        else:
                            duplicate_keys.append(key)
                        continue
                    add_rows.append({header: row.get(header, "") for header in HEADERS})
                if duplicate_keys:
                    plan["status"] = "BLOCKED_TARGET_KEY_CONFLICT"
                    plan["duplicate_keys"] = duplicate_keys
                else:
                    backup = out / f"manual_overrides.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
                    shutil.copy2(before_path, backup)
                    staged = out / f"manual_overrides.{uuid.uuid4().hex}.tmp"
                    with staged.open("w", encoding="utf-8-sig", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=HEADERS)
                        writer.writeheader()
                        replacements = {
                            ("product", row["key"], row["field"]): row
                            for row in replace_rows
                        }
                        final_rows = []
                        for existing in existing_rows:
                            existing_key = (existing.get("scope", ""), existing.get("key", ""), existing.get("field", ""))
                            replacement = replacements.get(existing_key)
                            if replacement is None:
                                final_rows.append(existing)
                            else:
                                final_rows.append({header: replacement.get(header, "") for header in HEADERS})
                        writer.writerows(final_rows + add_rows)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(staged, before_path)
                    plan.update({
                        "status": "COMMITTED",
                        "production_write": True,
                        "applied_rows": len(add_rows) + len(replace_rows),
                        "added_rows": len(add_rows),
                        "replaced_rows": len(replace_rows),
                        "backup_path": str(backup),
                        "manual_overrides_after_sha256": sha256_file(before_path),
                    })
    plan_path = out / "tm_v1_product_override_apply_manifest.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# TM V1 Product Override Apply — 2026-09-15",
        "",
        "本次执行了原子生产写入。" if plan.get("production_write") else "本次为只读预览，未执行生产写入。",
        "",
        f"- eligible rows: {len(eligible)}",
        f"- stale rows: {len(stale)}",
        f"- conflict rows: {len(conflicts)}",
        f"- status: {plan['status']}",
        f"- before snapshot: {before_snapshot}",
    ]
    (out / "TM_V1_PRODUCT_OVERRIDE_APPLY.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
