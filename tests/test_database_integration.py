from pathlib import Path
from types import SimpleNamespace

from action_tracker.database.integration import (
    acknowledge_compatibility_exports,
    build_daily_bundle,
    commit_daily_bundle,
    storage_mode,
)
from action_tracker.database.production import database_status
from action_tracker.database.repository import ProductionRepository


def _cfg(tmp_path: Path, mode: str = "SQLITE_SHADOW"):
    state = tmp_path / "state"
    state.mkdir()
    return {
        "project_root": tmp_path,
        "storage": {"mode": mode, "db_path": tmp_path / "action.db"},
        "paths": {
            "master": tmp_path / "Action_Master.xlsx",
            "state": state,
        },
    }


def _status(sku: str, status: str, *, present=True, valid=True):
    return SimpleNamespace(
        sku=sku, canonical_id=f"ACT{sku.zfill(7)}", status=status,
        source_flag="BOTH" if present else "NONE", sitemap_present=present,
        listing_present=present, nuevo_present=False, promotion_present=False,
        observation_valid=valid, first_seen="2026-08-29", missing_count=0,
    )


def _bundle(cfg, run_id="2026-08-30_010000"):
    statuses = {"1001": _status("1001", "ACTIVE"), "1002": _status("1002", "MISSING_FIRST", present=False)}
    records = {"1001": {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Producto", "current_price": 2.5, "status": "CURRENT"}}
    known = {
        "1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"},
        "1002": {"official_sku": "1002", "canonical_id": "ACT0001002", "last_status": "MISSING", "missing_count": "1"},
    }
    transition = {"known": known, "offline": []}
    return build_daily_bundle(
        run_id=run_id, observation_date="2026-08-30", qa_state="PASS",
        today_records=records, baseline=records, statuses=statuses, known=known,
        transition=transition, today_set={"1001"}, observation_complete=True,
        price_events=[], event_events=[], review_rows=[],
        run_record={"dry_run": False}, snapshot_path=None,
    )


def test_daily_bundle_preserves_current_and_historical_identity(tmp_path: Path):
    cfg = _cfg(tmp_path)
    commit_id = commit_daily_bundle(cfg, _bundle(cfg))
    assert commit_id
    status = database_status(cfg["storage"]["db_path"])
    assert status["products"] == 2
    assert status["lifecycle"] == 2


def test_shadow_export_acknowledgement_is_content_addressed(tmp_path: Path):
    cfg = _cfg(tmp_path)
    cfg["paths"]["master"].write_bytes(b"master")
    (cfg["paths"]["state"] / "known_skus.csv").write_bytes(b"known")
    (cfg["paths"]["state"] / "offline_skus.csv").write_bytes(b"offline")
    commit_id = commit_daily_bundle(cfg, _bundle(cfg))
    result = acknowledge_compatibility_exports(cfg, commit_id)
    assert result["status"] == "SUCCESS"
    assert result["missing"] == []
    assert all(len(value) == 64 for value in result["hashes"].values())


def test_storage_mode_rejects_unknown_value(tmp_path: Path):
    try:
        storage_mode({"storage": {"mode": "MAGIC"}})
    except ValueError as exc:
        assert "STORAGE_MODE_INVALID" in str(exc)
    else:
        raise AssertionError("invalid storage mode was accepted")


def test_primary_writer_sets_primary_identity(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_id = commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    assert commit_id
    assert database_status(cfg["storage"]["db_path"])["metadata"]["database_role"] == "PRIMARY"


def test_role_promotion_is_explicit_and_shadow_writer_cannot_demote(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_SHADOW")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_SHADOW")
    from action_tracker.database.production import ProductionDatabaseError, ProductionWriter, promote_database_role
    result = promote_database_role(cfg["storage"]["db_path"])
    assert result["status"] == "PROMOTED"
    try:
        ProductionWriter(cfg["storage"]["db_path"], role="SHADOW")
    except ProductionDatabaseError as exc:
        assert "EXPLICIT_CUTOVER" in str(exc)
    else:
        raise AssertionError("a shadow writer demoted a primary database")


def test_primary_repository_matches_compatibility_shapes(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    repo = ProductionRepository(cfg["storage"]["db_path"])
    current = repo.load_current_products()
    known = repo.load_known_skus()
    assert set(current) == {"1001"}
    assert set(known) == {"1001", "1002"}
    assert known["1002"]["last_status"] == "MISSING"


def test_image_manifest_metadata_mirrors_without_bytes(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    from action_tracker.images.assets import ImageAssetRecord, ImageManifest
    from action_tracker.database.production import persist_image_manifest
    manifest_path = tmp_path / "images.csv"
    manifest = ImageManifest(manifest_path)
    manifest.upsert(ImageAssetRecord(
        sku="1001", canonical_id="ACT0001001", source_image_url="https://img/1",
        master_image_path="assets/1001/master.png", download_status="AVAILABLE",
        normalize_status="PASS", qa_status="PASS", master_width=250, master_height=250,
    ))
    manifest.save()
    result = persist_image_manifest(cfg["storage"]["db_path"], manifest_path)
    assert result["upserted"] == 1


def test_minimal_historical_rows_do_not_clear_official_facts(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    first = _bundle(cfg, run_id="2026-08-30_010000")
    commit_daily_bundle(cfg, first, mode="SQLITE_PRIMARY")
    statuses = {"1001": _status("1001", "MISSING_FIRST", present=False)}
    known = {"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "MISSING"}}
    second = build_daily_bundle(
        run_id="2026-08-31_010000", observation_date="2026-08-31", qa_state="PASS",
        today_records={}, baseline={}, statuses=statuses, known=known,
        transition={"known": known, "offline": []}, today_set=set(), observation_complete=True,
        price_events=[], event_events=[], review_rows=[], run_record={"dry_run": False}, snapshot_path=None,
    )
    commit_daily_bundle(cfg, second, mode="SQLITE_PRIMARY")
    from action_tracker.database.connection import connect
    with connect(cfg["storage"]["db_path"]) as db:
        assert db.execute("SELECT name_es FROM products WHERE official_sku='1001'").fetchone()[0] == "Producto"


def test_primary_export_source_comes_from_sqlite_head(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    record = {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Producto",
              "name_zh": "商品", "cat1_es": "Hogar", "cat2_es": "Limpieza", "spec_es": "1 unidad",
              "spec_zh": "1件", "cat1_zh": "家居", "cat2_zh": "清洁", "current_price": 2.5,
              "product_url": "https://www.action.com/es-es/p/1001/", "last_seen": "2026-08-30", "status": "CURRENT"}
    from action_tracker.database.integration import build_daily_bundle
    bundle = build_daily_bundle(
        run_id="2026-08-30_020000", observation_date="2026-08-30", qa_state="PASS",
        today_records={"1001": record}, baseline={}, statuses={"1001": _status("1001", "ACTIVE")},
        known={"1001": {"official_sku": "1001", "canonical_id": "ACT0001001"}},
        transition={"known": {"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"}}, "offline": []},
        today_set={"1001"}, observation_complete=True, price_events=[], event_events=[], review_rows=[],
        run_record={"dry_run": False}, snapshot_path=None,
    )
    commit_daily_bundle(cfg, bundle, mode="SQLITE_PRIMARY")
    from action_tracker.exporting.service import _resolve_sqlite_current_source
    source = _resolve_sqlite_current_source(cfg, export_date="2026-08-30", requested_run_id=None)
    assert source is not None and source.kind == "SQLITE_CURRENT"
    assert source.records[0]["sku"] == "1001"
