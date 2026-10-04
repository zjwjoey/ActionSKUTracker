#!/usr/bin/env python3
"""Read-only production preflight for Workflow V2.

This command deliberately never creates directories, writes SQLite pages, calls
Qwen, claims queue rows, or changes the production checkout.  It inspects a
validated source checkout and the existing PRIMARY data root and emits a JSON
report suitable for a deployment record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(name: str, ok: bool, value: Any = None, detail: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "status": "PASS" if ok else "FAIL"}
    if value is not None:
        item["value"] = value
    if detail:
        item["detail"] = detail
    return item


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _queue_stats(db: sqlite3.Connection, latest_run_id: str | None) -> dict[str, Any]:
    if not _table_exists(db, "translation_queue"):
        return {"available": False}
    grouped = db.execute("SELECT status, COUNT(*) FROM translation_queue GROUP BY status ORDER BY status").fetchall()
    priority = db.execute("SELECT priority, status, COUNT(*) FROM translation_queue GROUP BY priority, status ORDER BY priority, status").fetchall()
    current = []
    if latest_run_id:
        current = db.execute(
            "SELECT status, COUNT(*) FROM translation_queue WHERE run_id=? GROUP BY status ORDER BY status",
            (latest_run_id,),
        ).fetchall()
    pending_total = db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='PENDING'").fetchone()[0]
    pending_current = db.execute("SELECT COUNT(*) FROM translation_queue WHERE run_id=? AND status IN ('PENDING','RETRY','CLAIMED')", (latest_run_id,)).fetchone()[0] if latest_run_id else 0
    # Only explicit scheduler reason values are classified as NEW/CHANGED.
    # Older rows use SOURCE_VERSION_NEW_OR_CHANGED and remain UNKNOWN rather
    # than being counted in both buckets.
    pending_new = db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='PENDING' AND upper(reason) IN ('NEW','NEW_SKU','REAPPEARED','SOURCE_NEW')").fetchone()[0]
    pending_changed = db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='PENDING' AND upper(reason) IN ('CHANGED','SOURCE_CHANGED','DETAIL_CHANGED')").fetchone()[0]
    pending_backlog = db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='PENDING' AND (run_id IS NULL OR run_id<>?)", (latest_run_id or "",)).fetchone()[0]
    review_required = 0
    if _table_exists(db, "translation_revisions"):
        review_required = db.execute("SELECT COUNT(*) FROM translation_revisions WHERE upper(review_status)='REVIEW_REQUIRED'").fetchone()[0]
    return {
        "available": True,
        "by_status": {str(row[0]): int(row[1]) for row in grouped},
        "by_priority_status": [{"priority": row[0], "status": row[1], "count": int(row[2])} for row in priority],
        "latest_run_id": latest_run_id,
        "latest_run_by_status": {str(row[0]): int(row[1]) for row in current},
        "pending_total": int(pending_total),
        "pending_current_run": int(pending_current),
        "pending_new": int(pending_new),
        "pending_changed": int(pending_changed),
        "pending_backlog": int(pending_backlog),
        "retry_count": int(db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='RETRY'").fetchone()[0]),
        "blocked_count": int(db.execute("SELECT COUNT(*) FROM translation_queue WHERE status='BLOCKED'").fetchone()[0]),
        "review_required_count": int(review_required),
        "batch_limit": 50,
        "historical_backlog_isolated": True,
    }


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_root = args.source_root.resolve()
    data_root = args.data_root.resolve()
    config_path = args.config.resolve() if args.config else source_root / "config" / "settings.yaml"
    base_config_path = source_root / "config" / "settings.yaml"
    base = yaml.safe_load(base_config_path.read_text(encoding="utf-8")) or {}
    overlay = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = _deep_merge(base, overlay) if config_path != base_config_path else base
    expected_ref = getattr(args, "expected_ref", None)
    expected_head = args.expected_head
    if expected_ref:
        expected_head = _git(source_root, "rev-parse", f"{expected_ref}^{{commit}}")
    expected_branch = args.expected_branch
    checks: list[dict[str, Any]] = []

    actual_branch = _git(source_root, "branch", "--show-current")
    actual_head = _git(source_root, "rev-parse", "HEAD")
    # GitHub checks out immutable tags in detached-HEAD mode.  In that mode
    # there is no branch name to compare, so the resolved ref/head pair is the
    # authoritative identity.  Keep the branch guard for normal branch runs
    # and for callers that did not provide an explicit audited ref.
    branch_ok = actual_branch == expected_branch
    if expected_ref and not actual_branch:
        branch_ok = actual_head == expected_head
    branch_detail = f"expected {expected_branch}"
    if expected_ref and not actual_branch:
        branch_detail = f"detached audited ref {expected_ref} resolves to {expected_head}"
    checks.append(_check("source_branch", branch_ok, actual_branch or "(detached HEAD)", branch_detail))
    checks.append(_check("source_head", actual_head == expected_head, actual_head, f"expected {expected_head}"))

    workflow = raw.get("workflow_v2") or {}
    shadow = workflow.get("shadow_preflight") or {}
    auto_translation = workflow.get("auto_translation") or {}
    auto_policy = workflow.get("auto_policy_approval") or {}
    auto_export = workflow.get("auto_export") or {}
    detail_retry = workflow.get("detail_retry") or {}
    localization = raw.get("localization") or {}
    localization_ai = localization.get("ai") or {}
    knowledge = raw.get("knowledge") or {}
    legacy_qwen = (raw.get("translation") or {}).get("qwen_mt") or {}
    switch_values = {
        "workflow_v2.enabled": workflow.get("enabled"),
        "workflow_v2.shadow_preflight.enabled": shadow.get("enabled"),
        "workflow_v2.auto_translation.enabled": auto_translation.get("enabled"),
        "workflow_v2.auto_translation.provider": auto_translation.get("provider"),
        "workflow_v2.translation_batch_limit": workflow.get("translation_batch_limit"),
        "workflow_v2.auto_policy_approval.enabled": auto_policy.get("enabled"),
        "workflow_v2.auto_export.enabled": auto_export.get("enabled"),
        "workflow_v2.detail_retry.enabled": detail_retry.get("enabled"),
        "knowledge.production_apply_enabled": knowledge.get("production_apply_enabled"),
        "knowledge.fallback_to_spanish": knowledge.get("fallback_to_spanish"),
        "localization.production_apply_enabled": localization.get("production_apply_enabled"),
        "localization.auto_approval_enabled": localization.get("auto_approval_enabled"),
        "localization.ai.enabled": localization_ai.get("enabled"),
        "localization.ai.provider": localization_ai.get("provider"),
        "localization.ai.model": localization_ai.get("model"),
        "translation.qwen_mt.enabled": legacy_qwen.get("enabled", False),
        "dictionary_apply.production_enabled": (raw.get("dictionary_apply") or {}).get("production_enabled"),
    }
    expected_switches = {
        "workflow_v2.enabled": True,
        "workflow_v2.shadow_preflight.enabled": True,
        "workflow_v2.auto_translation.enabled": True,
        "workflow_v2.auto_translation.provider": "qwen_mt",
        "workflow_v2.translation_batch_limit": 50,
        "workflow_v2.auto_policy_approval.enabled": False,
        "workflow_v2.auto_export.enabled": False,
        "workflow_v2.detail_retry.enabled": False,
        "knowledge.production_apply_enabled": False,
        "knowledge.fallback_to_spanish": False,
        "localization.production_apply_enabled": False,
        "localization.auto_approval_enabled": False,
        "localization.ai.enabled": True,
        "localization.ai.provider": "qwen_mt",
        "localization.ai.model": "qwen-mt-flash",
        "translation.qwen_mt.enabled": False,
        "dictionary_apply.production_enabled": False,
    }
    checks.append(_check("production_switches", switch_values == expected_switches, switch_values, "expected controlled Workflow V2 profile"))

    db_rel = Path((raw.get("storage") or {}).get("db_path", "runtime/db/action_tracker.db"))
    checks.append(_check("storage_mode", (raw.get("storage") or {}).get("mode") == "SQLITE_PRIMARY", (raw.get("storage") or {}).get("mode")))
    db_path = db_rel if db_rel.is_absolute() else data_root / db_rel
    checks.append(_check("primary_database_path", db_path.exists(), str(db_path)))
    db_report: dict[str, Any] = {"path": str(db_path), "sha256": _sha256(db_path)}
    latest_run_id: str | None = None
    # Reject a truncated/non-SQLite file before opening it so preflight emits
    # a normal NOT_READY report instead of exposing a traceback.
    sqlite_header_ok = db_path.exists() and db_path.read_bytes()[:16] == b"SQLite format 3\x00"
    if sqlite_header_ok:
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required_tables = {"schema_metadata", "commit_batches", "products", "product_localizations", "translation_queue", "translation_revisions"}
            checks.append(_check("database_required_tables", required_tables.issubset(tables), sorted(required_tables - tables)))
            metadata = {str(row[0]): str(row[1]) for row in db.execute("SELECT key,value FROM schema_metadata")} if "schema_metadata" in tables else {}
            latest = db.execute("SELECT commit_id,run_id,committed_at,status FROM commit_batches ORDER BY committed_at DESC LIMIT 1").fetchone() if "commit_batches" in tables else None
            latest_run_id = str(latest["run_id"]) if latest and latest["run_id"] else None
            product_count = int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0]) if "products" in tables else 0
            localization_count = int(db.execute("SELECT COUNT(*) FROM product_localizations").fetchone()[0]) if "product_localizations" in tables else 0
            registry_count = int(db.execute("SELECT COUNT(*) FROM translation_revisions").fetchone()[0]) if "translation_revisions" in tables else 0
            db_report.update({"metadata": metadata, "tables": sorted(tables), "latest_commit": dict(latest) if latest else None,
                              "products": product_count, "product_localizations": localization_count,
                              "translation_revisions": registry_count})
            checks.append(_check("commit_batches_latest", bool(latest and latest["commit_id"] and latest["run_id"] and str(latest["status"]).upper() == "COMMITTED"), dict(latest) if latest else None, "latest commit must be readable and COMMITTED"))
            checks.append(_check("products_readable", "products" in tables and product_count >= 0, product_count))
            checks.append(_check("product_localizations_readable", "product_localizations" in tables and localization_count >= 0, localization_count))
            checks.append(_check("translation_registry_readable", "translation_revisions" in tables and registry_count >= 0, registry_count))
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = [dict(row) for row in db.execute("PRAGMA foreign_key_check").fetchall()]
            db_report.update({"integrity_check": integrity, "foreign_key_errors": foreign_keys[:10], "foreign_key_error_count": len(foreign_keys)})
            checks.append(_check("primary_database_role", metadata.get("database_role") == "PRIMARY", metadata.get("database_role"), "database_role must remain PRIMARY"))
            checks.append(_check("database_schema_family", metadata.get("schema_family") == "ACTION_SQLITE_DATA", metadata.get("schema_family")))
            checks.append(_check("database_schema_version", metadata.get("schema_version") == "2.0.0", metadata.get("schema_version"), "expected compatible schema_version 2.0.0"))
            checks.append(_check("database_integrity", integrity == "ok" and not foreign_keys, {"integrity": integrity, "foreign_key_error_count": len(foreign_keys)}))
            db_report["queue"] = _queue_stats(db, latest_run_id)
            queue_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(translation_queue)")} if "translation_queue" in tables else set()
            queue_contract = {"priority", "created_at", "run_id", "status"}.issubset(queue_columns)
            checks.append(_check("queue_scheduling_contract", queue_contract, sorted(queue_columns), "priority/run_id/created_at/status are required"))
            if "export_sync" in tables:
                rows = db.execute("SELECT status, COUNT(*) FROM export_sync GROUP BY status ORDER BY status").fetchall()
                db_report["export_sync_by_status"] = {str(row[0]): int(row[1]) for row in rows}
    else:
        checks.extend([_check("database_required_tables", False), _check("commit_batches_latest", False), _check("products_readable", False), _check("product_localizations_readable", False), _check("translation_registry_readable", False), _check("queue_scheduling_contract", False), _check("primary_database_role", False, None, "database is missing"), _check("database_schema_family", False), _check("database_schema_version", False), _check("database_integrity", False)])

    dirs = [data_root / Path(str(value)) for key, value in (raw.get("paths") or {}).items() if key != "master" and isinstance(value, str) and not Path(value).is_absolute()]
    missing_dirs = [str(path) for path in dirs if not path.exists()]
    checks.append(_check("runtime_path_mapping", not missing_dirs, {"data_root": str(data_root), "missing": missing_dirs}))
    usage = shutil.disk_usage(data_root)
    min_free = int((raw.get("operations") or {}).get("min_free_disk_bytes", 0) or 0)
    checks.append(_check("disk_space", usage.free >= min_free, {"free_bytes": usage.free, "required_bytes": min_free}))

    key_name = str(localization_ai.get("api_key_env") or "DASHSCOPE_API_KEY")
    key_present = bool(os.environ.get(key_name, "").strip())
    checks.append(_check("qwen_provider_config", _bool(localization_ai.get("enabled")) and localization_ai.get("provider") == "qwen_mt" and localization_ai.get("model") == "qwen-mt-flash", {"enabled": localization_ai.get("enabled"), "provider": localization_ai.get("provider"), "model": localization_ai.get("model")}))
    checks.append(_check("qwen_api_key", key_present, {"env": key_name, "status": "SET" if key_present else "NOT_SET"}))

    return {
        "report": "WORKFLOW_V2_PRODUCTION_PREFLIGHT",
        "source_root": str(source_root),
        "data_root": str(data_root),
        "config": str(config_path),
        "audited_ref": expected_ref,
        "audited_content_head": actual_head,
        "database": db_report,
        "checks": checks,
        "qwen_call_performed": False,
        "production_data_modified": False,
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "NOT_READY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="explicit deployment profile overlay")
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--expected-head")
    parser.add_argument("--expected-ref", help="git ref/tag whose resolved commit is the audited content head")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()
    if not args.expected_head and not args.expected_ref:
        parser.error("one of --expected-head or --expected-ref is required")
    try:
        report = run(args)
    except Exception as exc:  # preflight must return an actionable report, not a traceback
        report = {"report": "WORKFLOW_V2_PRODUCTION_PREFLIGHT", "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
