"""Apply finite browser-verified official detail rows through the existing API.

Default is a read-only preview. Commit uses the daily lock, verified backup,
current-head/source guards and normal compatibility export synchronization.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from action_tracker.config import load_settings
from action_tracker.database.integration import database_path, regenerate_compatibility_exports
from action_tracker.database.production import apply_detail_corrections
from action_tracker.database.repository import ProductionRepository
from action_tracker.exporting.service import validate_source_records, validate_spanish_source_fields
from action_tracker.localization.history_audit import verified_backup
from action_tracker.products.parser import normalize_verified_specification_rows
from action_tracker.services.runtime import RunLock
from action_tracker.services.hashing import localization_source_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    cfg = load_settings()
    db_path = database_path(cfg)
    repo = ProductionRepository(db_path)
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    if evidence.get("method") != "OFFICIAL_BROWSER_DOM_CELLS" or not evidence.get("authorization_text"):
        raise ValueError("OFFICIAL_BROWSER_EVIDENCE_AND_AUTHORIZATION_REQUIRED")
    lock = RunLock(Path(cfg["paths"]["state"]))
    lock.acquire("official-detail-evidence", command="official-detail-evidence-apply")
    try:
        head = repo.latest_commit_info()
        if head["commit_id"] != evidence["expected_base_commit_id"]:
            raise ValueError("OFFICIAL_DETAIL_EVIDENCE_BASE_CHANGED")
        records = {r["sku"]: r for r in repo.load_current_export_records()}
        details, changes, seen = {}, [], set()
        for item in evidence["items"]:
            sku = item["sku"]
            if sku in seen or sku not in records:
                raise ValueError("OFFICIAL_DETAIL_DUPLICATE_OR_NONCURRENT_SKU")
            seen.add(sku)
            record = records[sku]
            if item["url"] != record["product_url"] or urlparse(item["url"]).hostname != "www.action.com":
                raise ValueError("OFFICIAL_DETAIL_URL_MISMATCH")
            if item["expected_source_hash"] != localization_source_hash(record):
                raise ValueError("OFFICIAL_DETAIL_SOURCE_CHANGED")
            text = normalize_verified_specification_rows(item["rows"], expected_sku=sku)
            after = {**record, "details_es": text}
            validate_source_records([after], export_date=head["run_date"])
            validate_spanish_source_fields([after])
            if text != record["details_es"]:
                details[sku] = {"details_es": text}
                changes.append({"sku": sku, "field": "details_es", "before": record["details_es"], "after": text,
                                "official_url": item["url"], "qa": "PASS"})
        preview = {"base_commit_id": head["commit_id"], "evidence_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
                   "captured_evidence": evidence, "changes": changes, "qa": "PASS", "production_writes": 0}
        (args.output / "preflight.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "PREFLIGHT_PASS", "changed_fields": len(changes), "commit": args.commit}), flush=True)
        if not args.commit or not details:
            return
        backup_path = Path(cfg["paths"]["backups"]) / "official_detail" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
        backup = verified_backup(db_path, backup_path)
        (args.output / "backup.json").write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "BACKUP_RESTORE_VERIFIED"}), flush=True)
        # Revalidate the head and source bindings after the backup. A normal
        # official source update may invalidate Chinese freshness, never text.
        if repo.latest_commit_info()["commit_id"] != head["commit_id"]:
            raise ValueError("OFFICIAL_DETAIL_BASE_CHANGED_AFTER_BACKUP")
        result = apply_detail_corrections(db_path, parent_run_id=head["run_id"], details_by_sku=details, mode="APPLY")
        (args.output / "apply_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "DETAIL_APPLIED", **result}), flush=True)
        sync = regenerate_compatibility_exports(cfg, result["commit_id"])
        (args.output / "export_sync.json").write_text(json.dumps(sync, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"phase": "EXPORT_SYNC", **sync}, ensure_ascii=False, default=str), flush=True)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
