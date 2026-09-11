"""Repair known persistence/projection defects without rewriting official facts.

The migration is intentionally narrow: invalidate fabricated original prices,
normalize only observed HTML/UI pollution in Spanish localization fields, then
rebuild the legacy Excel compatibility projection from SQLite PRIMARY.
Historical raw observations and immutable fact versions are not modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from action_tracker.config import load_settings
from action_tracker.database.connection import connect
from action_tracker.database.integration import (
    database_path,
    regenerate_compatibility_exports,
)
from action_tracker.database.production import backup_database
from action_tracker.database.production import _enqueue_import_followups
from action_tracker.database.provenance import sync_localization_field_provenance
from action_tracker.services.hashing import content_hash, localization_source_hash
from action_tracker.services.normalization import normalize_official_text


def repair_database(path: Path, *, import_id: str | None = None) -> dict[str, int]:
    counts = {
        "original_price_cleared": 0,
        "first_seen_backfilled": 0,
        "localization_values_cleaned": 0,
        "field_values_cleaned": 0,
        "field_projection_values_synced": 0,
        "canonical_provenance_rows_synced": 0,
        "category_followups_queued": 0,
        "translation_followups_queued": 0,
        "hashes_recomputed": 0,
    }
    import_id = import_id or f"master-data-repair-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT official_sku,current_price,original_price FROM products "
            "WHERE current_price IS NOT NULL AND original_price IS NOT NULL"
        ).fetchall():
            try:
                invalid = float(row[2]) <= float(row[1])
            except (TypeError, ValueError):
                invalid = False
            if invalid:
                db.execute("UPDATE products SET original_price=NULL,updated_at=CURRENT_TIMESTAMP WHERE official_sku=?", (row[0],))
                counts["original_price_cleared"] += 1

        for row in db.execute(
            "SELECT p.official_sku,l.first_seen_date FROM products p "
            "JOIN lifecycle_state l ON l.official_sku=p.official_sku "
            "WHERE (p.first_seen_at IS NULL OR trim(p.first_seen_at)='') "
            "AND l.first_seen_date IS NOT NULL AND trim(l.first_seen_date)<>''"
        ).fetchall():
            db.execute(
                "UPDATE products SET first_seen_at=?,updated_at=CURRENT_TIMESTAMP WHERE official_sku=?",
                (row[1], row[0]),
            )
            counts["first_seen_backfilled"] += 1

        es_rows = db.execute(
            "SELECT l.official_sku,l.name,l.cat1,l.cat2,l.spec,l.description,l.details,p.product_url,p.image_url,"
            "l.source,l.review_status,l.name_source,l.cat1_source,l.cat2_source,l.spec_source,"
            "l.description_source,l.details_source,l.freshness_status "
            "FROM product_localizations l LEFT JOIN products p ON p.official_sku=l.official_sku "
            "WHERE l.language='es'"
        ).fetchall()
        for row in es_rows:
            sku = str(row[0])
            values = {
                "name": row[1], "cat1": row[2], "cat2": row[3],
                "spec": normalize_official_text(row[4], field="spec"),
                "description": normalize_official_text(row[5], field="description"),
                "details": normalize_official_text(row[6], field="details"),
            }
            old = dict(zip(values, row[1:7]))
            changed = [key for key in values if values[key] != old[key]]
            if changed:
                db.execute(
                    "UPDATE product_localizations SET spec=?,description=?,details=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE official_sku=? AND language='es'",
                    (values["spec"], values["description"], values["details"], sku),
                )
                counts["localization_values_cleaned"] += len(changed)

            fact = {
                "name_es": values["name"], "cat1_es": values["cat1"], "cat2_es": values["cat2"],
                "spec_es": values["spec"], "desc_es": values["description"], "details_es": values["details"],
            }
            source_hash = localization_source_hash(fact)
            db.execute(
                "UPDATE products SET source_hash=?,content_hash=?,updated_at=CURRENT_TIMESTAMP WHERE official_sku=?",
                (source_hash, content_hash({**fact, "product_url": row[7], "image_url": row[8]}), sku),
            )
            db.execute(
                "UPDATE product_localizations SET source_hash=? WHERE official_sku=? AND language IN ('es','zh')",
                (source_hash, sku),
            )
            db.execute(
                "UPDATE localization_fields SET source_hash=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE official_sku=? AND language='es'",
                (source_hash, sku),
            )
            # Values, not just hashes, must match the compatibility row.  The
            # previous repair only copied a new hash, which made a stale
            # description/details projection look valid to the release gate.
            for field_name in ("name", "cat1", "cat2", "spec", "description", "details"):
                db.execute(
                    "UPDATE localization_fields SET value=?,source=?,review_status=?,source_hash=?,updated_at=CURRENT_TIMESTAMP,applied_commit_id=? "
                    "WHERE official_sku=? AND language='es' AND field_name=?",
                    (values[field_name], row[9], row[10], source_hash, import_id, sku, field_name),
                )
                counts["field_projection_values_synced"] += 1
            sync_localization_field_provenance(
                db,
                {"sku": sku, "language": "es", **values, "source": row[9],
                 "review_status": row[10], "source_hash": source_hash,
                 "name_source": row[11], "cat1_source": row[12], "cat2_source": row[13],
                 "spec_source": row[14], "description_source": row[15], "details_source": row[16],
                 "freshness_status": row[17], "applied_commit_id": import_id},
                commit_id=import_id, now=datetime.now().isoformat(),
            )
            followups = _enqueue_import_followups(
                db, sku=sku, merged=values, source_hash=source_hash,
                product_url=str(row[7] or ""), now=datetime.now().isoformat(), import_id=import_id,
            )
            counts["category_followups_queued"] += int(followups["category_queued"])
            counts["translation_followups_queued"] += int(followups["translation_queued"])
            counts["hashes_recomputed"] += 1

        field_rows = db.execute(
            "SELECT official_sku,field_name,value FROM localization_fields WHERE language='es' "
            "AND field_name IN ('spec','description','details')"
        ).fetchall()
        for row in field_rows:
            field = str(row[1])
            value = normalize_official_text(row[2], field=field)
            if value != row[2]:
                db.execute(
                    "UPDATE localization_fields SET value=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE official_sku=? AND language='es' AND field_name=?",
                    (value, row[0], field),
                )
                counts["field_values_cleaned"] += 1
        # Repair and synchronize the Chinese field projections without
        # rewriting any Chinese value.  A detail/source hash change can make
        # an otherwise valid translation stale; the queue above records the
        # fields requiring fresh approval.
        zh_rows = db.execute(
            "SELECT official_sku,name,cat1,cat2,spec,description,details,source,review_status,source_hash,"
            "name_source,cat1_source,cat2_source,spec_source,description_source,details_source,freshness_status "
            "FROM product_localizations WHERE language='zh'"
        ).fetchall()
        for row in zh_rows:
            sku = str(row[0])
            zh = dict(zip(("name", "cat1", "cat2", "spec", "description", "details", "source", "review_status",
                           "source_hash", "name_source", "cat1_source", "cat2_source", "spec_source",
                           "description_source", "details_source", "freshness_status"), row[1:]))
            zh.update({"sku": sku, "language": "zh", "applied_commit_id": import_id})
            sync_localization_field_provenance(db, zh, commit_id=import_id, now=datetime.now().isoformat())
            counts["canonical_provenance_rows_synced"] += 6
        db.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cfg = load_settings()
    db = database_path(cfg)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path(cfg["paths"]["backups"]) / f"action_tracker_before_data_repair_{stamp}.db"
    backup_database(db, backup)
    counts = repair_database(db, import_id=f"master-data-repair-{stamp}")
    from action_tracker.database.repository import ProductionRepository
    head = ProductionRepository(db).current_head()
    if not head:
        raise RuntimeError("SQLITE_PRIMARY_HEAD_MISSING")
    sync = regenerate_compatibility_exports(cfg, head)
    report = {
        "database": str(db), "database_backup": str(backup), "commit_id": head,
        "repair": counts, "compatibility_sync": sync,
    }
    output = args.report or Path(cfg["paths"].get("logs", cfg["paths"]["temp"])) / f"master_data_repair_{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
