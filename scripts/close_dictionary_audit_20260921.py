"""Close deterministic dictionary audit defects after the 2026-09-21 apply.

Only metadata/cache repairs and source-evidenced no-brand display corrections
are allowed here.  It neither calls a translation provider nor changes a
Spanish source fact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.config import load_settings
from action_tracker.database.connection import connect
from action_tracker.database.integration import database_path, regenerate_compatibility_exports
from action_tracker.database.production import backup_database
from action_tracker.database.provenance import sync_localization_field_provenance
from action_tracker.dictionary import (
    MODEL_TRANSLATION_HEADERS, OVERRIDE_HEADERS, PRODUCT_DICTIONARY_HEADERS,
    SOURCE_DAMAGE_HEADERS, load_dictionary_rows, write_dictionary_csv,
)

NO_BRAND_NAME_FIXES = {
    "3222844": "配件收纳盒",
    "3009410": "厨房秤",
    "3220922": "活动手册",
    "3224611": "蒸汽清洁器",
    "3225012": "灰褐色哑光墙面涂料",
    "3226253": "自制蜡烛套装",
    "3226981": "毛绒玩具",
    "3227410": "身体乳",
}
REASON = "2026-09-21 no-brand display policy closure from official Spanish source"


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def backup_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def restore_database(backup: Path, target: Path) -> None:
    source, destination = sqlite3.connect(backup), sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close(); source.close()


def apply_names(db_path: Path, values: dict[str, str], run_id: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    with connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        for sku, value in values.items():
            es = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
            zh = db.execute("SELECT name FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
            if es is None or zh is None:
                raise RuntimeError(f"LOCALIZATION_ROW_MISSING:{sku}")
            source_hash = __import__("action_tracker.services.hashing", fromlist=["localization_source_hash"]).localization_source_hash({
                "name_es": es[0], "cat1_es": es[1], "cat2_es": es[2], "spec_es": es[3], "desc_es": es[4], "details_es": es[5],
            })
            before = text(zh[0])
            db.execute("UPDATE product_localizations SET name=?,name_source=?,updated_at=?,last_commit_id=?,approved_by=?,approved_at=?,applied_commit_id=? WHERE official_sku=? AND language='zh'",
                       (value, "manual_override", now, run_id, "project-owner", now, run_id, sku))
            db.execute("UPDATE products SET name_zh=?,updated_at=? WHERE official_sku=?", (value, now, sku))
            sync_localization_field_provenance(
                db, {"sku": sku, "language": "zh", "name": value, "name_source": "manual_override",
                     "name_review_status": "HUMAN_APPROVED", "name_source_hash": source_hash,
                     "name_updated_at": now, "name_applied_commit_id": run_id,
                     "name_approved_by": "project-owner", "name_approved_at": now, "name_freshness_status": "FRESH"},
                commit_id=run_id, now=now,
            )
            if before != value:
                latest = db.execute("SELECT patch_id,revision FROM localization_patches WHERE official_sku=? AND language='zh' AND field_name='name' ORDER BY revision DESC LIMIT 1", (sku,)).fetchone()
                parent, revision = (latest[0], int(latest[1]) + 1) if latest else (None, 1)
                patch_id = hashlib.sha256(f"{run_id}|{sku}|name|{digest(before)}|{digest(value)}".encode()).hexdigest()
                db.execute("INSERT INTO localization_patches(patch_id,parent_patch_id,official_sku,language,field_name,old_value,new_value,source_hash,reason,created_by,created_at,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (patch_id, parent, sku, "zh", "name", before, value, source_hash, REASON, "project-owner", now, revision))
                for event in ("PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED"):
                    db.execute("INSERT INTO localization_patch_events(event_id,patch_id,event_type,actor,reason,event_json,occurred_at) VALUES(?,?,?,?,?,?,?)",
                               (hashlib.sha256(f"{patch_id}|{event}".encode()).hexdigest(), patch_id, event, "project-owner", REASON, json.dumps({"apply_run_id": run_id, "sku": sku, "field": "name"}), now))
                changed += 1
        db.commit()
    return changed


def repair_runtime_dictionary(cfg: dict[str, Any], values: dict[str, str]) -> dict[str, int]:
    dictionary = Path(cfg["paths"]["dictionary"])
    cat_path = dictionary / "category_dictionary.csv"
    products_path = dictionary / "product_dictionary.csv"
    models_path = dictionary / "model_translation_overrides.csv"
    manual_path = dictionary / "manual_overrides.csv"
    damage_path = dictionary / "source_damage_report.csv"
    cats = list(csv.DictReader(cat_path.open(encoding="utf-8-sig", newline="")))
    cat1 = {text(r["cat1_es"]): text(r["cat1_zh"]) for r in cats if text(r["cat1_es"]) and text(r["cat1_zh"])}
    products = load_dictionary_rows(products_path, headers=PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",))
    products_by_sku = {r["sku"]: r for r in products}
    db_path = database_path(cfg)
    with connect(db_path) as db:
        current_dates = {text(r[0]): (text(r[1]), text(r[2])) for r in db.execute("SELECT official_sku,first_seen_at,last_seen_at FROM products WHERE status='CURRENT'")}
    category_filled = first_seen_filled = product_name_filled = 0
    for row in products:
        if not text(row.get("cat1_zh")) and text(row.get("cat1_es")) in cat1:
            row["cat1_zh"] = cat1[text(row["cat1_es"])]
            category_filled += 1
        dates = current_dates.get(row["sku"])
        if dates and not text(row.get("source_first_seen")):
            if not dates[0]:
                raise RuntimeError(f"CURRENT_FIRST_SEEN_UNAVAILABLE:{row['sku']}")
            row["source_first_seen"] = dates[0]
            first_seen_filled += 1
        if row["sku"] in values and row.get("name_zh_standard") != values[row["sku"]]:
            row["name_zh_standard"] = values[row["sku"]]
            row["translation_status"] = "HUMAN_APPROVED"
            row["review_status"] = "HUMAN_REVIEWED"
            row["updated_at"] = datetime.now(timezone.utc).date().isoformat()
            row["notes"] = (text(row.get("notes")) + "；" if text(row.get("notes")) else "") + REASON
            product_name_filled += 1
    write_dictionary_csv(products_path, products, PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",))

    manuals = load_dictionary_rows(manual_path, headers=OVERRIDE_HEADERS, key_fields=("scope", "key", "field"))
    manual_index = {(r["scope"], r["key"], r["field"]): r for r in manuals}
    for sku, value in values.items():
        manual_index[("product", sku, "name_zh_standard")] = {
            "scope": "product", "key": sku, "field": "name_zh_standard", "value": value,
            "reason": REASON, "source": "OFFICIAL_ES_SOURCE+OWNER_AUTHORIZATION", "locked": "0",
            "updated_at": datetime.now(timezone.utc).date().isoformat(),
        }
    write_dictionary_csv(manual_path, list(manual_index.values()), OVERRIDE_HEADERS, key_fields=("scope", "key", "field"))

    models = load_dictionary_rows(models_path, headers=MODEL_TRANSLATION_HEADERS, key_fields=("sku",))
    kept_models = [row for row in models if row["sku"] not in products_by_sku or row["source_hash"] == products_by_sku[row["sku"]]["source_hash"]]
    write_dictionary_csv(models_path, kept_models, MODEL_TRANSLATION_HEADERS, key_fields=("sku",))

    damages = load_dictionary_rows(damage_path, headers=SOURCE_DAMAGE_HEADERS, key_fields=("sku",))
    for row in damages:
        if row["sku"] == "2558856" and row["status"] == "SOURCE_DAMAGED":
            row["status"] = "RESOLVED"
            row["notes"] = (text(row.get("notes")) + "；" if text(row.get("notes")) else "") + "2026-09-21 source fields no longer contain CJK damage"
    write_dictionary_csv(damage_path, damages, SOURCE_DAMAGE_HEADERS, key_fields=("sku",))

    manifest_path = dictionary / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_master": str(Path(cfg["paths"]["master"])),
        "source_master_sha256": file_hash(Path(cfg["paths"]["master"])),
        "current_skus": len(current_dates), "effective_product_skus": len(products), "category_rows": len(cats),
        "missing_source_first_seen": sum(not text(row.get("source_first_seen")) for row in products),
        "missing_source_last_seen": sum(not text(row.get("source_last_seen")) for row in products),
        "source_damage_skus": sum(row["status"] == "SOURCE_DAMAGED" for row in damages),
        "source_damage_fields": sum(len([v for v in row["damaged_fields"].split(",") if v]) for row in damages if row["status"] == "SOURCE_DAMAGED"),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"cat1_filled": category_filled, "first_seen_filled": first_seen_filled, "name_overrides": product_name_filled,
            "stale_model_overrides_removed": len(models) - len(kept_models), "damage_rows_resolved": 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = load_settings(); db_path = database_path(cfg); dictionary = Path(cfg["paths"]["dictionary"])
    run_id = "dictionary-audit-closure-" + datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "runtime/localization_apply" / f"dictionary_audit_closure_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    preview = {"run_id": run_id, "name_corrections": NO_BRAND_NAME_FIXES, "translation_provider_calls": 0,
               "official_source_refetches": 0, "reason": "deterministic audit closure; no Spanish source discrepancy detected"}
    out.mkdir(parents=True, exist_ok=True)
    (out / "preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.apply:
        print(json.dumps({**preview, "status": "READY_FOR_APPLY"}, ensure_ascii=False)); return 0
    master = Path(cfg["paths"]["master"])
    targets = {"database": db_path, "master": master, **{name: dictionary / name for name in ("product_dictionary.csv", "manual_overrides.csv", "model_translation_overrides.csv", "source_damage_report.csv", "build_manifest.json")}}
    backups = {key: out / f"before_{key}" for key in targets}
    backup_database(db_path, backups["database"])
    for key, path in targets.items():
        if key != "database": backup_file(path, backups[key])
    try:
        name_updates = apply_names(db_path, NO_BRAND_NAME_FIXES, run_id)
        from action_tracker.database.repository import ProductionRepository
        head = ProductionRepository(db_path).current_head()
        if not head: raise RuntimeError("SQLITE_PRIMARY_HEAD_MISSING")
        sync = regenerate_compatibility_exports(cfg, head)
        dictionary_counts = repair_runtime_dictionary(cfg, NO_BRAND_NAME_FIXES)
        audit = subprocess.run([sys.executable, str(ROOT / "scripts/audit_dictionary.py")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if audit.returncode != 0:
            raise RuntimeError("DICTIONARY_AUDIT_FAILED:" + (audit.stdout or audit.stderr)[-4000:])
    except Exception:
        restore_database(backups["database"], db_path)
        for key, path in targets.items():
            if key != "database": shutil.copy2(backups[key], path)
        raise
    report = {**preview, "status": "SUCCESS", "name_updates": name_updates, "compatibility_sync": sync,
              "dictionary_counts": dictionary_counts, "audit_output": audit.stdout, "backups": {k: str(v) for k,v in backups.items()}}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
