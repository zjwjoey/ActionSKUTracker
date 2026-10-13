"""Narrow official-price reconciliation, independent of daily Presence."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse
import uuid
from zoneinfo import ZoneInfo

from .connection import connect
from .production import ProductionDatabaseError, supersede_older_export_sync


def validate_original_price_absence(item: dict, product: dict, business_date: str) -> None:
    if item.get("method") != "OFFICIAL_BROWSER_PRICE_REGION" or item.get("region") is not True:
        raise ProductionDatabaseError("PRICE_ABSENCE_EVIDENCE_INCOMPLETE")
    if item.get("articles") != [str(product["official_sku"])]:
        raise ProductionDatabaseError("PRICE_ABSENCE_SKU_MISMATCH")
    url = str(item.get("url") or "")
    if url != product["product_url"] or urlparse(url).hostname != "www.action.com":
        raise ProductionDatabaseError("PRICE_ABSENCE_URL_MISMATCH")
    if any(type(item.get(key)) is not int or item[key] != 0 for key in ("original_count", "discount_count")):
        raise ProductionDatabaseError("PRICE_ABSENCE_NOT_CONFIRMED")
    if item.get("original") != "" or item.get("discount") != "":
        raise ProductionDatabaseError("PRICE_ABSENCE_NOT_CONFIRMED")
    observed = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.astimezone(ZoneInfo("Europe/Madrid")).date().isoformat() != business_date:
        raise ProductionDatabaseError("PRICE_ABSENCE_DATE_MISMATCH")
    price = float(str(item["whole"]) + "." + str(item["fraction"]))
    if not math.isfinite(price) or price <= 0 or abs(price - float(product["current_price"])) > 1e-9:
        raise ProductionDatabaseError("PRICE_ABSENCE_CURRENT_PRICE_MISMATCH")
    if product["status"] != "CURRENT":
        raise ProductionDatabaseError("PRICE_ABSENCE_SKU_NOT_CURRENT")


def apply_verified_original_price_absence(path: Path, *, items: list[dict],
        expected_base_commit_id: str, business_date: str, dry_run: bool = False) -> dict:
    """Only original_price may change; evidence/QA/base are checked atomically."""
    with connect(Path(path)) as db:
        role = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
        if not role or role[0] != "PRIMARY":
            raise ProductionDatabaseError("PRICE_CORRECTION_REQUIRES_PRIMARY")
        db.execute("BEGIN IMMEDIATE")
        head = db.execute("SELECT c.commit_id,c.run_id,r.run_date,r.qa_state,r.dry_run FROM commit_batches c JOIN runs r ON r.run_id=c.run_id WHERE c.status='COMMITTED' ORDER BY c.committed_at DESC,c.commit_id DESC LIMIT 1").fetchone()
        if not head or head[2] != business_date or head[3] not in {"PASS", "PASS_PRESENCE_ONLY"} or head[4]:
            raise ProductionDatabaseError("PRICE_CORRECTION_PARENT_NOT_FORMAL")
        pending, seen = [], set()
        for item in items:
            sku = item["sku"]
            if sku in seen:
                raise ProductionDatabaseError("PRICE_CORRECTION_DUPLICATE_SKU")
            seen.add(sku)
            row = db.execute("SELECT canonical_id,official_sku,status,current_price,original_price,product_url FROM products WHERE official_sku=?", (sku,)).fetchone()
            if not row:
                raise ProductionDatabaseError("PRICE_CORRECTION_SKU_MISSING")
            product = dict(row)
            validate_original_price_absence(item, product, business_date)
            original = product["original_price"]
            if original in (None, ""):
                continue
            expected_original = float(item["expected_original_price"])
            if (not math.isfinite(float(original)) or not math.isfinite(expected_original)
                    or float(original) <= 0 or expected_original <= 0
                    or abs(float(original)-expected_original) > 1e-9):
                raise ProductionDatabaseError("PRICE_CORRECTION_ORIGINAL_CHANGED")
            pending.append(product)
        if not pending:
            db.rollback()
            return {"status": "NOOP", "commit_id": head[0], "applied_fields": 0, "applied_skus": 0}
        if head[0] != expected_base_commit_id:
            raise ProductionDatabaseError("PRICE_CORRECTION_BASE_CHANGED")
        payload = {"operation": "VERIFIED_ORIGINAL_PRICE_CLEAR", "business_date": business_date,
                   "base_commit_id": head[0], "evidence": items,
                   "changes": [{"sku": p["official_sku"], "field": "original_price", "before": p["original_price"], "after": None} for p in pending],
                   "qa_state": "PASS", "current_price_changed": False, "presence_changed": False}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if dry_run:
            db.rollback()
            return {"status": "PREVIEW", "ready_fields": len(pending), "production_writes": 0, **payload}
        now = datetime.now(timezone.utc).isoformat()
        run_id = f"original_price_clear_{business_date.replace('-', '')}_{digest[:12]}"
        commit_id = f"{business_date}_{run_id}_{digest[:12]}"
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?,'COMMITTED','PASS',0,?,?,'2.0.0')", (run_id,business_date,now,now))
        db.execute("INSERT INTO run_evidence(run_id,snapshot_path,snapshot_hash,evidence_json) VALUES(?,NULL,?,?)", (run_id,digest,json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        db.execute("INSERT INTO commit_batches(commit_id,run_id,base_commit_id,bundle_hash,schema_version,started_at,committed_at,product_count,observation_count,price_event_count,event_count,status) VALUES(?,?,?,?,'2.0.0',?,?,?,0,0,?,'COMMITTED')", (commit_id,run_id,head[0],digest,now,now,len(pending),len(pending)))
        for product in pending:
            sku = product["official_sku"]
            db.execute("UPDATE products SET original_price=NULL,updated_at=? WHERE official_sku=?", (now,sku))
            # This is an audited correction, not a simulated price drop or
            # weekly-promotion lifecycle event. Existing history is untouched.
            db.execute("INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type,old_value,new_value,run_id,evidence,event_key) VALUES(?,?,?,'ORIGINAL_PRICE_CORRECTION',?,NULL,?,?,?)", (product["canonical_id"],sku,business_date,str(product["original_price"]),run_id,"OFFICIAL_BROWSER_CONFIRMED_ABSENCE",f"original-price-clear:{run_id}:{sku}"))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ProductionDatabaseError("PRICE_CORRECTION_INTEGRITY_FAILED")
        db.execute("INSERT INTO export_sync(commit_id,status) VALUES(?,'PENDING')",(commit_id,))
        supersede_older_export_sync(db,commit_id)
        db.commit()
        return {"status": "SUCCESS", "commit_id": commit_id, "run_id": run_id,
                "applied_fields": len(pending), "applied_skus": len(pending), "price_history_events": 0}
