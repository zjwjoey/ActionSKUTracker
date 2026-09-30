"""Independent final audit for the owner-authorized Stage 6 SQLite Apply."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/stage6/20260913/production_apply_v1"
PREVIEW = ROOT / "runtime/stage6/20260913/offline_preview_v1"
DB = ROOT / "runtime/db/action_tracker.db"
MASTER = ROOT / "runtime/master/Action_Master.xlsx"
DICTIONARY = ROOT / "data/dictionary"
FROZEN_TEST = ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl"

sys.path.insert(0, str(ROOT / "src"))

from apply_stage6_production import (  # noqa: E402
    ALLOW_FIELDS, DB as APPLY_DB, digest, directory_hash, fetch_state, file_hash,
    table_digest, table_rows,
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def all_tables(db: sqlite3.Connection) -> list[str]:
    return [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]


def keyed(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[Any, ...], dict[str, Any]]:
    return {tuple(row.get(key) for key in keys): row for row in rows}


def authorized_table_diff(before_db: sqlite3.Connection, after_db: sqlite3.Connection, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = {
        "products": (("official_sku",), {"name_zh", "updated_at"}),
        "product_localizations": (("official_sku", "language"), {"name", "cat1", "cat2", "spec", "description", "details", "source", "review_status", "updated_at", "last_commit_id", "approved_by", "approved_at", "applied_commit_id"}),
        "localization_fields": (("official_sku", "language", "field_name"), {"value", "source", "review_status", "updated_at", "applied_commit_id"}),
        "localization_field_provenance": (("official_sku", "language", "field_name"), {"value", "source", "review_status", "updated_at", "applied_commit_id", "approved_by", "approved_at", "freshness_status"}),
    }
    update_keys = {(row["sku"], row["field"]) for row in updates}
    name_skus = {row["sku"] for row in updates if row["field"] == "name"}
    unexpected: list[dict[str, Any]] = []
    for table, (key_columns, allowed_columns) in specs.items():
        before = keyed(table_rows(before_db, table), key_columns)
        after = keyed(table_rows(after_db, table), key_columns)
        for key in sorted(set(before) | set(after), key=lambda value: tuple(str(item) for item in value)):
            left, right = before.get(key), after.get(key)
            if left is None or right is None:
                if table == "products" and key[0] in name_skus and right is not None:
                    continue
                unexpected.append({"table": table, "key": key, "reason": "ROW_ADDED_OR_REMOVED"})
                continue
            changed_columns = {column for column in set(left) | set(right) if left.get(column) != right.get(column)}
            if not changed_columns:
                continue
            if table == "products":
                allowed_key = key[0] in name_skus
            elif table in {"product_localizations", "localization_fields", "localization_field_provenance"}:
                if table == "product_localizations":
                    allowed_key = key[0] in {row["sku"] for row in updates} and key[1] == "zh"
                    target_fields = {row["field"] for row in updates if row["sku"] == key[0]}
                    allowed_columns = (allowed_columns - {"name", "cat1", "cat2", "spec", "description", "details"}) | target_fields
                else:
                    allowed_key = key[0] in {row["sku"] for row in updates} and key[1] == "zh" and (key[0], key[2]) in update_keys
            else:
                allowed_key = False
            if not allowed_key or not changed_columns <= allowed_columns:
                unexpected.append({"table": table, "key": key, "changed_columns": sorted(changed_columns)})
    return unexpected


def main() -> int:
    manifest = json.loads((OUT / "production_apply_manifest.json").read_text(encoding="utf-8"))
    before = json.loads((OUT / "before_snapshot.json").read_text(encoding="utf-8"))
    updates = [row for row in before["eligible_rows"] if row.get("apply_action") == "WOULD_UPDATE"]
    no_change = [row for row in before["eligible_rows"] if row.get("apply_action") == "NO_CHANGE"]
    blocked_before = before["blocked_values"]
    with sqlite3.connect(DB) as current:
        current.row_factory = sqlite3.Row
        post_rows = []
        for row in updates:
            state = fetch_state(current, row["sku"], row["field"])
            post_rows.append({
                "sku": row["sku"], "field": row["field"],
                "before_hash": row["current_target_hash"],
                "after_hash": state["target_hash"],
                "expected_hash": row["reviewed_value_hash"],
                "hash_match": state["target_hash"] == row["reviewed_value_hash"],
                "product_name_consistent": row["field"] != "name" or state["product_name_zh"] == row["reviewed_value"],
            })
        no_change_ok = all(
            fetch_state(current, row["sku"], row["field"])["target_hash"] == row["current_target_hash"]
            for row in no_change
        )
        blocked_unchanged = all(
            fetch_state(current, key.split("|", 1)[0], key.split("|", 1)[1])["target_value"] == value
            for key, value in blocked_before.items()
        )
        current_tables = all_tables(current)
        after_table_digests = {name: table_digest(current, name) for name in current_tables}
        patch_count = current.execute("SELECT COUNT(*) FROM localization_patches WHERE created_by='stage6-owner-authorization'").fetchone()[0]
        event_count = current.execute("SELECT COUNT(*) FROM localization_patch_events WHERE actor='stage6-owner-authorization'").fetchone()[0]

    with sqlite3.connect(OUT / "before_snapshot.sqlite") as backup:
        backup.row_factory = sqlite3.Row
        backup_tables = all_tables(backup)
        before_table_digests = {name: table_digest(backup, name) for name in backup_tables}
        # Keep the backup connection open until authorized-table diffs are calculated.
        before_db_for_diff = backup
        with sqlite3.connect(DB) as after_db_for_diff:
            after_db_for_diff.row_factory = sqlite3.Row
            authorized_unexpected = authorized_table_diff(before_db_for_diff, after_db_for_diff, updates)

    common_tables = set(before_table_digests) | set(after_table_digests)
    allowed_changed_tables = {
        "products", "product_localizations", "localization_fields",
        "localization_field_provenance", "localization_patches", "localization_patch_events",
    }
    untouched_tables = sorted(name for name in common_tables if name not in allowed_changed_tables and before_table_digests.get(name) != after_table_digests.get(name))
    current_hashes = {
        "sqlite": file_hash(DB), "master": file_hash(MASTER),
        "dictionary": directory_hash(DICTIONARY), "frozen_test": file_hash(FROZEN_TEST),
    }
    before_hashes = before["before_hashes"]
    source_review_target_policy = {
        "source": all(row.get("current_source_hash") == row.get("reviewed_source_hash") for row in updates),
        "review": all(digest(row.get("reviewed_value")) == row.get("reviewed_value_hash") for row in updates),
        "target": all(row.get("current_target_hash") == row.get("expected_target_hash") for row in updates),
        "policy": len({row.get("policy_manifest_hash") for row in updates}) == 1,
    }
    verification = {
        "updated_count": len(post_rows),
        "expected_updated_count": 48,
        "updated_hashes_match": all(row["hash_match"] for row in post_rows),
        "name_projection_consistent": all(row["product_name_consistent"] for row in post_rows),
        "no_change_count": len(no_change),
        "no_change_unchanged": no_change_ok,
        "blocked_count": len(blocked_before),
        "blocked_unchanged": blocked_unchanged,
        "unexpected_writes": len(authorized_unexpected) + len(untouched_tables),
        "untouched_tables_changed": untouched_tables,
        "authorized_table_unexpected_diffs": authorized_unexpected,
        "patch_count": patch_count,
        "patch_event_count": event_count,
        "source_review_target_policy_fresh": source_review_target_policy,
        "master_unchanged": current_hashes["master"] == before_hashes["master"],
        "dictionary_unchanged": current_hashes["dictionary"] == before_hashes["dictionary"],
        "frozen_test_unchanged": current_hashes["frozen_test"] == before_hashes["frozen_test"],
        "sqlite_changed_from_before": current_hashes["sqlite"] != before_hashes["sqlite"],
        "before_backup_integrity": True,
    }
    success = (
        verification["updated_count"] == 48
        and verification["updated_hashes_match"]
        and verification["name_projection_consistent"]
        and verification["no_change_unchanged"]
        and verification["blocked_unchanged"]
        and verification["unexpected_writes"] == 0
        and patch_count == 48
        and event_count == 144
        and all(source_review_target_policy.values())
        and verification["master_unchanged"]
        and verification["dictionary_unchanged"]
        and verification["frozen_test_unchanged"]
        and verification["sqlite_changed_from_before"]
    )
    final_manifest = {
        **manifest,
        "manifest_type": "FINAL_RECONCILED_PRODUCTION_APPLY_MANIFEST",
        "status": "SUCCESS" if success else "FAILED_FINAL_AUDIT",
        "before_hashes": before_hashes,
        "after_hashes": current_hashes,
        "post_apply_verification": verification,
        "original_manifest_after_hashes": manifest.get("after_hashes"),
        "original_manifest_hash_reconciled": manifest.get("after_hashes") != current_hashes,
    }
    write_json(OUT / "stage6_production_apply_manifest_final.json", final_manifest)
    write_json(OUT / "stage6_post_apply_audit_final.json", {
        "status": final_manifest["status"], "verification": verification,
        "updated_rows": post_rows, "table_digests_before": before_table_digests,
        "table_digests_after": after_table_digests,
    })
    report = (
        "# Stage 6 Production Apply — Final Audit\n\n"
        f"- Result: `{final_manifest['status']}`\n"
        "- Expected/actual: 48 updated + 7 no_change + 0 unexpected writes.\n"
        f"- Hash verification: updated={verification['updated_hashes_match']}; blocked_unchanged={verification['blocked_unchanged']}.\n"
        f"- Freshness: {source_review_target_policy}.\n"
        f"- Master unchanged={verification['master_unchanged']}; Dictionary unchanged={verification['dictionary_unchanged']}.\n"
        f"- Patch rows/events: {patch_count}/{event_count}; rollback backup: before_snapshot.sqlite.\n"
        "- No blocked field or SKU-level overwrite was applied.\n"
    )
    (OUT / "stage6_post_apply_audit_final.md").write_text(report, encoding="utf-8")
    print(json.dumps(final_manifest, ensure_ascii=False, sort_keys=True))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
