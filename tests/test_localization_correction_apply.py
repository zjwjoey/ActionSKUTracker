import sqlite3
from pathlib import Path

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.immutable_patches import (
    append_patch_event,
    create_localization_patch,
    validate_patch_apply,
)
from action_tracker.database.production import ProductionDatabaseError, apply_approved_localization_patches
from action_tracker.database.provenance import sync_localization_field_provenance
from action_tracker.database.schema import migrate_v2
from action_tracker.services.hashing import localization_source_hash


FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")


def _primary(tmp_path: Path) -> Path:
    path = tmp_path / "primary.sqlite"
    migrate_v2(path, role="PRIMARY")
    source_hash = localization_source_hash({"name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Almacenamiento", "spec_es": "2 unidades", "desc_es": "Descripción", "details_es": "Detalles"})
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?)", ('1001','es','Producto','Hogar','Almacenamiento','2 unidades','Descripción','Detalles','now',source_hash))
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?)", ('1001','zh','旧名','家居','收纳','2件','旧描述','旧详情','now',source_hash))
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('r1','2026-09-08','COMMITTED','PASS',0,'2026-09-08T00:00:00+00:00','2026-09-08T00:00:00+00:00','2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','r1','h','2.0.0','2026-09-08T00:00:00+00:00','2026-09-08T00:00:00+00:00','COMMITTED')")
        for field, value, status, freshness in (
            ("name", "旧名", "VERIFIED", "CURRENT"), ("cat1", "家居", "VERIFIED", "CURRENT"),
            ("cat2", "收纳", "PENDING", "STALE"), ("spec", "2件", "VERIFIED", "CURRENT"),
            ("description", "旧描述", "PENDING", "STALE"), ("details", "旧详情", "VERIFIED", "CURRENT"),
        ):
            sync_localization_field_provenance(db, {"official_sku": "1001", "language": "zh", field: value,
                f"{field}_source": "SEED", f"{field}_review_status": status,
                f"{field}_freshness_status": freshness, f"{field}_source_hash": source_hash,
                f"{field}_approved_by": "seed", f"{field}_approved_at": "now", f"{field}_applied_commit_id": "C1"}, commit_id="C1", now="now")
    return path


def _patch(path: Path, patch_id: str, field: str, old: str, new: str, base: str = "C1"):
    with connect(path) as db:
        source_hash = db.execute("SELECT source_hash FROM product_localizations WHERE official_sku='1001' AND language='es'").fetchone()[0]
    create_localization_patch(path, patch_id=patch_id, official_sku="1001", language="zh", field_name=field,
                              old_value=old, new_value=new, source_hash=source_hash, source_allowlist=["MANUAL"], created_by="human",
                              evidence={"field_name": field, "base_commit_id": base})
    append_patch_event(path, patch_id=patch_id, event_type="PATCH_APPROVED", actor="human",
                       evidence={"field_name": field, "base_commit_id": base, "source_name": "MANUAL"})


def test_partial_apply_preserves_other_field_state_and_hash(tmp_path):
    path = _primary(tmp_path)
    _patch(path, "p-desc", "description", "旧描述", "新描述")
    result = apply_approved_localization_patches(path, patch_ids=["p-desc"], expected_base_commit_id="C1", actor="human")
    assert result["applied_fields"] == 1
    with connect(path) as db:
        rows = {row[0]: tuple(row[1:]) for row in db.execute("SELECT field_name,value,review_status,freshness_status,source_hash FROM localization_fields WHERE official_sku='1001' AND language='zh'")}
        assert rows["description"][0] == "新描述"
        assert rows["description"][1:3] == ("APPROVED", "CURRENT")
        assert rows["cat2"] == ("收纳", "PENDING", "STALE", rows["cat2"][3])
        assert rows["details"] == ("旧详情", "VERIFIED", "CURRENT", rows["details"][3])


def test_stale_bundle_and_old_value_are_rejected(tmp_path):
    path = _primary(tmp_path)
    _patch(path, "p1", "description", "旧描述", "新描述")
    _patch(path, "p2", "name", "旧名", "新名")
    apply_approved_localization_patches(path, patch_ids=["p2"], expected_base_commit_id="C1", actor="human")
    with pytest.raises(ProductionDatabaseError, match="STALE_LOCALIZATION_APPLY_BUNDLE"):
        apply_approved_localization_patches(path, patch_ids=["p1"], expected_base_commit_id="C1", actor="human")
    with connect(path) as db:
        assert db.execute("SELECT name FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()[0] == "新名"


def test_active_primary_patch_schema_adapter_supports_full_lifecycle(tmp_path):
    path = tmp_path / "active.sqlite"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','1001','CURRENT')")
        db.execute("DROP TABLE localization_patch_events")
        db.execute("DROP TABLE localization_patches")
        db.executescript("""
            CREATE TABLE localization_patches (
              patch_id TEXT PRIMARY KEY, parent_patch_id TEXT, official_sku TEXT NOT NULL,
              language TEXT NOT NULL, field_name TEXT NOT NULL, old_value TEXT, new_value TEXT,
              source_hash TEXT NOT NULL, reason TEXT NOT NULL, created_by TEXT NOT NULL,
              created_at TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE TABLE localization_patch_events (
              event_id TEXT PRIMARY KEY, patch_id TEXT NOT NULL, event_type TEXT NOT NULL,
              actor TEXT NOT NULL, reason TEXT, event_json TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
        """)
    create_localization_patch(path, patch_id="active-p1", official_sku="1001", language="zh", field_name="name",
                              old_value="旧", new_value="新", source_hash="h", created_by="human", reason="review")
    append_patch_event(path, patch_id="active-p1", event_type="PATCH_APPROVED", actor="human", evidence={"field_name": "name"})
    assert validate_patch_apply(path, patch_id="active-p1", current_source_hash="h", source_name="MANUAL")["status"] == "APPROVED"
    append_patch_event(path, patch_id="active-p1", event_type="PATCH_APPLIED", actor="system", evidence={"commit_id": "C1"})
    append_patch_event(path, patch_id="active-p1", event_type="PATCH_REVOKED", actor="human", evidence={"reason": "correction", "superseding_patch_id": "active-p2"})
    with connect(path) as db:
        assert [row[0] for row in db.execute("SELECT event_type FROM localization_patch_events WHERE patch_id='active-p1' ORDER BY occurred_at")] == ["PATCH_CREATED", "PATCH_APPROVED", "PATCH_APPLIED", "PATCH_REVOKED"]
