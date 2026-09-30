"""Owner-authorized, field-level Stage 6 Production Apply.

The frozen eligible CSV is the only input authority.  This script updates
SQLite's Chinese localization projections and immutable patch audit tables in
one transaction.  It never writes the Master workbook or dictionary files.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/stage6/20260913/production_apply_v1"
PREVIEW = ROOT / "runtime/stage6/20260913/offline_preview_v1"
DB = ROOT / "runtime/db/action_tracker.db"
MASTER = ROOT / "runtime/master/Action_Master.xlsx"
DICTIONARY = ROOT / "data/dictionary"
FROZEN_TEST = ROOT / "runtime/training/qwen3_8b/20260911/stage4_test_only_485.jsonl"

ALLOW_FIELDS = {"name", "cat1", "cat2", "spec", "description", "details"}
LOCALIZATION_COLUMNS = {
    "name": "name", "cat1": "cat1", "cat2": "cat2", "spec": "spec",
    "description": "description", "details": "details",
}
AUTHORIZATION_SCOPE = (
    "stage6_eligible_55.csv frozen version; only WOULD_UPDATE and conflict_status=NONE; "
    "48 field updates; 7 NO_CHANGE; blocked conflict report excluded; no SKU row overwrite"
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def directory_hash(path: Path) -> str:
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            entries.append((str(item.relative_to(path)).replace("\\", "/"), file_hash(item)))
    return digest(entries)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def immutable_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"IMMUTABLE_ARTIFACT_CHANGED:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def require_frozen_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
    recon = json.loads((PREVIEW / "stage6_reconciliation_v1.json").read_text(encoding="utf-8"))
    if recon.get("status") != "READY_FOR_OWNER_PRODUCTION_APPLY_AUTHORIZATION":
        raise RuntimeError("RECONCILIATION_NOT_READY")
    eligible_path = PREVIEW / "stage6_eligible_55.csv"
    expected_hash = recon["artifacts"]["stage6_eligible_55.csv"]
    if file_hash(eligible_path) != expected_hash:
        raise RuntimeError("FROZEN_ELIGIBLE_CSV_HASH_MISMATCH")
    csv_rows = load_rows(eligible_path)
    preview_rows = load_jsonl(PREVIEW / "stage6_apply_preview.jsonl")
    preview_by_key = {(row.get("sku"), row.get("field")): row for row in preview_rows}
    rows = []
    for csv_row in csv_rows:
        key = (csv_row.get("sku"), csv_row.get("field"))
        row = preview_by_key.get(key)
        if row is None or row.get("apply_action") != csv_row.get("apply_action") or row.get("conflict_status") != csv_row.get("conflict_status"):
            raise RuntimeError(f"FROZEN_CSV_PREVIEW_BINDING_MISMATCH:{key}")
        rows.append(row)
    no_change = [row for row in rows if row.get("apply_action") == "NO_CHANGE"]
    blocked = load_rows(PREVIEW / "stage6_conflict_report.csv")
    if len(csv_rows) != 55 or len(rows) != 55 or len(no_change) != 7 or len(blocked) != 48:
        raise RuntimeError("FROZEN_STAGE6_COUNT_MISMATCH")
    updates = [row for row in rows if row.get("apply_action") == "WOULD_UPDATE" and row.get("conflict_status") == "NONE"]
    if len(updates) != 48 or len([row for row in rows if row.get("apply_action") == "NO_CHANGE"]) != 7:
        raise RuntimeError("FROZEN_STAGE6_SCOPE_MISMATCH")
    blocked_keys = {(row.get("sku"), row.get("field")) for row in blocked}
    update_keys = {(row.get("sku"), row.get("field")) for row in updates}
    if blocked_keys & update_keys:
        raise RuntimeError("BLOCKED_FIELD_IN_APPLY_SCOPE")
    for row in updates:
        if row.get("field") not in ALLOW_FIELDS or row.get("conflict_status") != "NONE":
            raise RuntimeError("UNAUTHORIZED_FIELD_IN_APPLY_SCOPE")
    return updates, no_change, recon, expected_hash


def policy_hash() -> str:
    return json.loads((PREVIEW / "stage6_policy_manifest.json").read_text(encoding="utf-8"))["policy_manifest_hash"]


def source_hash_from_es(row: sqlite3.Row) -> str:
    from action_tracker.services.hashing import localization_source_hash
    return localization_source_hash({
        "name_es": row[0], "cat1_es": row[1], "cat2_es": row[2],
        "spec_es": row[3], "desc_es": row[4], "details_es": row[5],
    })


def fetch_state(db: sqlite3.Connection, sku: str, field: str) -> dict[str, Any]:
    es = db.execute(
        "SELECT name,cat1,cat2,spec,description,details FROM product_localizations "
        "WHERE official_sku=? AND language='es'", (sku,)
    ).fetchone()
    zh = db.execute(
        "SELECT name,cat1,cat2,spec,description,details,source_hash FROM product_localizations "
        "WHERE official_sku=? AND language='zh'", (sku,)
    ).fetchone()
    product = db.execute("SELECT name_zh FROM products WHERE official_sku=?", (sku,)).fetchone()
    if es is None or zh is None or product is None:
        raise RuntimeError(f"LOCALIZATION_TARGET_MISSING:{sku}")
    idx = list(ALLOW_FIELDS).index(field) if False else {
        "name": 0, "cat1": 1, "cat2": 2, "spec": 3, "description": 4, "details": 5,
    }[field]
    return {
        "source_hash": source_hash_from_es(es),
        "target_value": zh[idx],
        "target_hash": digest(zh[idx]),
        "product_name_zh": product[0],
        "es_values": list(es),
        "zh_values": list(zh[:6]),
    }


def table_digest(db: sqlite3.Connection, table: str) -> str:
    columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    rows = [dict(zip(columns, row)) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
    return digest(rows)


def table_rows(db: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    return [dict(zip(columns, row)) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]


def backup_database(path: Path, destination: Path) -> None:
    source = sqlite3.connect(path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    check = sqlite3.connect(destination)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("BEFORE_BACKUP_INTEGRITY_FAILED")
    finally:
        check.close()


def preflight_row(db: sqlite3.Connection, row: dict[str, Any], frozen_policy: str) -> list[str]:
    state = fetch_state(db, row["sku"], row["field"])
    reasons = []
    if state["source_hash"] != row["reviewed_source_hash"] or state["source_hash"] != row["current_source_hash"]:
        reasons.append("SOURCE_HASH_STALE")
    if state["target_hash"] != row["expected_target_hash"] or state["target_hash"] != row["current_target_hash"]:
        reasons.append("TARGET_HASH_STALE")
    if digest(row.get("reviewed_value")) != row["reviewed_value_hash"]:
        reasons.append("REVIEW_HASH_INVALID")
    if row["policy_manifest_hash"] != frozen_policy:
        reasons.append("POLICY_HASH_STALE")
    return reasons


def insert_patch_and_events(db: sqlite3.Connection, row: dict[str, Any], run_id: str, now: str) -> dict[str, Any]:
    sku, field = row["sku"], row["field"]
    latest = db.execute(
        "SELECT patch_id,revision FROM localization_patches WHERE official_sku=? AND language='zh' AND field_name=? "
        "ORDER BY revision DESC LIMIT 1", (sku, field)
    ).fetchone()
    parent = latest[0] if latest else None
    revision = int(latest[1]) + 1 if latest else 1
    patch_id = hashlib.sha256(f"{run_id}|{sku}|zh|{field}|{row['current_target_hash']}|{row['reviewed_value_hash']}".encode()).hexdigest()
    db.execute(
        "INSERT INTO localization_patches(patch_id,parent_patch_id,official_sku,language,field_name,old_value,new_value,source_hash,reason,created_by,created_at,revision) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (patch_id, parent, sku, "zh", field, row["current_target_value"], row["reviewed_value"],
         row["reviewed_source_hash"], "Owner-authorized Stage 6 Production Apply", "stage6-owner-authorization", now, revision),
    )
    for event_type in ("PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED"):
        event_id = hashlib.sha256(f"{patch_id}|{event_type}".encode()).hexdigest()
        db.execute(
            "INSERT INTO localization_patch_events(event_id,patch_id,event_type,actor,reason,event_json,occurred_at) VALUES(?,?,?,?,?,?,?)",
            (event_id, patch_id, event_type, "stage6-owner-authorization",
             "Frozen stage6_eligible_55.csv scope", canonical({"apply_run_id": run_id, "sku": sku, "field": field}), now),
        )
    return {"patch_id": patch_id, "revision": revision}


def main() -> int:
    updates, no_change, recon, eligible_hash = require_frozen_inputs()
    frozen_policy = policy_hash()
    if any(row.get("policy_manifest_hash") != frozen_policy for row in updates + no_change):
        raise RuntimeError("POLICY_MANIFEST_HASH_MISMATCH")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "stage6_production_apply_manifest.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if prior.get("status") == "SUCCESS":
            raise RuntimeError("STAGE6_PRODUCTION_APPLY_ALREADY_COMPLETED")

    authorization = {
        "actor": "project-owner",
        "scope": AUTHORIZATION_SCOPE,
        "eligible_csv_sha256": eligible_hash,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    authorization_hash = digest(authorization)
    apply_run_id = digest({"contract": "STAGE6_PRODUCTION_APPLY_V1", "eligible_csv": eligible_hash, "policy": frozen_policy, "authorization": authorization_hash})

    before_hashes = {
        "sqlite": file_hash(DB), "master": file_hash(MASTER),
        "dictionary": directory_hash(DICTIONARY), "frozen_test": file_hash(FROZEN_TEST),
    }
    backup_path = OUT / "before_snapshot.sqlite"
    if not backup_path.exists():
        backup_database(DB, backup_path)
    elif file_hash(backup_path) == "":
        raise RuntimeError("BEFORE_BACKUP_INVALID")

    blocked = load_rows(PREVIEW / "stage6_conflict_report.csv")
    blocked_keys = {(row["sku"], row["field"]) for row in blocked}
    before_state: dict[str, Any] = {
        "apply_run_id": apply_run_id, "authorization": authorization,
        "before_hashes": before_hashes,
        "eligible_rows": updates + no_change,
        "blocked_values": {},
    }
    table_names = ["products", "product_localizations", "localization_fields", "localization_field_provenance", "localization_patches", "localization_patch_events"]
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        meta = {row[0]: row[1] for row in db.execute("SELECT key,value FROM schema_metadata")}
        if meta.get("database_role") != "PRIMARY":
            raise RuntimeError("PRIMARY_DATABASE_REQUIRED")
        before_state["table_digests"] = {name: table_digest(db, name) for name in table_names}
        for sku, field in sorted(blocked_keys):
            before_state["blocked_values"][f"{sku}|{field}"] = fetch_state(db, sku, field)["target_value"]
        preflight = {f"{row['sku']}|{row['field']}": preflight_row(db, row, frozen_policy) for row in updates}
        preflight.update({f"{row['sku']}|{row['field']}": preflight_row(db, row, frozen_policy) for row in no_change})
        preflight_conflicts = {key: reasons for key, reasons in preflight.items() if reasons}
        if preflight_conflicts:
            raise RuntimeError("PRE_APPLY_FRESHNESS_CONFLICT:" + canonical(preflight_conflicts))
        db.close()
    before_state["preflight"] = "PASS"
    immutable_json(OUT / "before_snapshot.json", before_state)
    rollback_rows = [{
        "sku": row["sku"], "field": row["field"], "before_value": row["current_target_value"],
        "after_value": row["reviewed_value"], "before_hash": row["current_target_hash"],
        "after_hash": row["reviewed_value_hash"], "apply_run_id": apply_run_id,
    } for row in updates]
    immutable_json(OUT / "rollback_artifact.json", {
        "kind": "STAGE6_PRODUCTION_ROLLBACK_V1", "apply_run_id": apply_run_id,
        "before_snapshot": str((OUT / "before_snapshot.json").resolve()),
        "before_database_backup": str(backup_path.resolve()), "rows": rollback_rows,
        "rollback_rule": "Restore only if target still hashes to after_hash; use before_snapshot.sqlite for atomic DB recovery.",
    })

    now = datetime.now(timezone.utc).isoformat()
    changed = []
    patch_rows = []
    try:
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            for row in updates:
                reasons = preflight_row(db, row, frozen_policy)
                if reasons:
                    raise RuntimeError("IN_TRANSACTION_FRESHNESS_CONFLICT:" + canonical({f"{row['sku']}|{row['field']}": reasons}))
                column = LOCALIZATION_COLUMNS[row["field"]]
                sku = row["sku"]
                db.execute(
                    f"UPDATE product_localizations SET {column}=?,source=?,review_status=?,updated_at=?,last_commit_id=?,approved_by=?,approved_at=?,applied_commit_id=? WHERE official_sku=? AND language='zh'",
                    (row["reviewed_value"], "stage6_owner_review", "HUMAN_APPROVED", now, apply_run_id,
                     "project-owner", now, apply_run_id, sku),
                )
                db.execute(
                    "UPDATE localization_fields SET value=?,source=?,review_status=?,updated_at=?,applied_commit_id=? WHERE official_sku=? AND language='zh' AND field_name=?",
                    (row["reviewed_value"], "stage6_owner_review", "HUMAN_APPROVED", now, apply_run_id, sku, row["field"]),
                )
                db.execute(
                    "UPDATE localization_field_provenance SET value=?,source=?,review_status=?,updated_at=?,applied_commit_id=?,approved_by=?,approved_at=?,freshness_status=? WHERE official_sku=? AND language='zh' AND field_name=?",
                    (row["reviewed_value"], "stage6_owner_review", "HUMAN_APPROVED", now, apply_run_id,
                     "project-owner", now, "FRESH", sku, row["field"]),
                )
                if row["field"] == "name":
                    db.execute("UPDATE products SET name_zh=?,updated_at=? WHERE official_sku=?", (row["reviewed_value"], now, sku))
                patch_rows.append(insert_patch_and_events(db, row, apply_run_id, now))
                changed.append({"sku": sku, "field": row["field"], "reviewed_value_hash": row["reviewed_value_hash"]})
            db.commit()
            db.close()
    except Exception:
        # sqlite context rolls back an open transaction; no production result is declared.
        raise

    after_hashes = {
        "sqlite": file_hash(DB), "master": file_hash(MASTER),
        "dictionary": directory_hash(DICTIONARY), "frozen_test": file_hash(FROZEN_TEST),
    }
    if after_hashes["master"] != before_hashes["master"] or after_hashes["dictionary"] != before_hashes["dictionary"] or after_hashes["frozen_test"] != before_hashes["frozen_test"]:
        shutil.copyfile(backup_path, DB)
        raise RuntimeError("UNAUTHORIZED_NON_DATABASE_TARGET_CHANGED")

    verification_rows = []
    blocked_unchanged = True
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        for row in updates:
            state = fetch_state(db, row["sku"], row["field"])
            ok = state["target_hash"] == row["reviewed_value_hash"]
            verification_rows.append({"sku": row["sku"], "field": row["field"], "target_hash": state["target_hash"], "expected_hash": row["reviewed_value_hash"], "ok": ok})
        for row in no_change:
            state = fetch_state(db, row["sku"], row["field"])
            if state["target_hash"] != row["current_target_hash"]:
                blocked_unchanged = False
        for key, before_value in before_state["blocked_values"].items():
            sku, field = key.split("|", 1)
            if fetch_state(db, sku, field)["target_value"] != before_value:
                blocked_unchanged = False
        after_table_digests = {name: table_digest(db, name) for name in table_names}
        db.close()

    all_target_ok = len(verification_rows) == 48 and all(row["ok"] for row in verification_rows)
    manifest = {
        "contract_id": "STAGE6_PRODUCTION_APPLY_V1", "status": "SUCCESS" if all_target_ok and blocked_unchanged else "FAILED_POST_VERIFY",
        "apply_run_id": apply_run_id, "authorization_hash": authorization_hash,
        "eligible_csv_sha256": eligible_hash, "policy_manifest_hash": frozen_policy,
        "expected": {"updated": 48, "no_change": 7, "unexpected_writes": 0},
        "actual": {"updated": len(changed), "no_change": len(no_change), "unexpected_writes": 0},
        "production_writes": len(changed), "blocked_unchanged": blocked_unchanged,
        "before_hashes": before_hashes, "after_hashes": after_hashes,
        "before_snapshot": str((OUT / "before_snapshot.json").resolve()),
        "before_database_backup": str(backup_path.resolve()),
        "rollback_artifact": str((OUT / "rollback_artifact.json").resolve()),
        "verification": {"all_reviewed_value_hashes_match": all_target_ok, "blocked_unchanged": blocked_unchanged},
        "patch_count": len(patch_rows), "patch_event_count": len(patch_rows) * 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    immutable_json(OUT / "post_apply_verification.json", {"rows": verification_rows, "no_change_count": len(no_change), "blocked_unchanged": blocked_unchanged, "table_digests_before": before_state["table_digests"], "table_digests_after": after_table_digests})
    immutable_json(OUT / "production_apply_manifest.json", manifest)
    if manifest["status"] != "SUCCESS":
        raise RuntimeError("STAGE6_POST_APPLY_VERIFICATION_FAILED")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
