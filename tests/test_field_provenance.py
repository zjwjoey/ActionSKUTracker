import sqlite3
from pathlib import Path

from action_tracker.database.schema import migrate_v2
from action_tracker.services.hashing import localization_field_source_hash


def test_field_provenance_is_independent_per_field(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    migrate_v2(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
        row = {
            "official_sku": "1001", "language": "zh", "name": "收纳盒", "cat1": "家居布置",
            "cat2": "收纳用品", "spec": "2件", "description": "描述", "details": "详情",
            "source": "LOCALIZATION", "review_status": "PENDING", "freshness_status": "CURRENT",
            "source_hash": "h1", "name_source": "MANUAL", "description_source": "MODEL",
            "approved_by": "reviewer", "approved_at": "2026-09-08T00:00:00Z", "applied_commit_id": "c1",
        }
        from action_tracker.database.provenance import sync_localization_field_provenance
        sync_localization_field_provenance(db, row, commit_id="c1", now="2026-09-08T00:00:00Z")
        rows = db.execute("SELECT field_name,source,review_status FROM localization_field_provenance ORDER BY field_name").fetchall()
        canonical_rows = db.execute("SELECT field_name,source,review_status FROM localization_fields ORDER BY field_name").fetchall()
    assert len(rows) == 6
    assert dict((field, source) for field, source, _ in rows)["name"] == "MANUAL"
    assert dict((field, source) for field, source, _ in rows)["description"] == "MODEL"
    assert canonical_rows == rows


def test_new_projection_uses_field_scoped_source_hash(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    migrate_v2(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
        row = {
            "official_sku": "1001", "language": "zh", "name": "商品",
            "name_es": "Producto", "desc_es": "Para casa",
            "review_status": "APPROVED", "freshness_status": "CURRENT",
        }
        from action_tracker.database.provenance import sync_localization_field_provenance
        sync_localization_field_provenance(db, row, commit_id="c1", now="2026-09-08T00:00:00Z")
        name_hash = db.execute(
            "SELECT source_hash FROM localization_field_provenance WHERE official_sku='1001' AND field_name='name'"
        ).fetchone()[0]
    assert name_hash == localization_field_source_hash(row, "name")
