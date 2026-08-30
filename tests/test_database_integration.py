import json
from pathlib import Path
from types import SimpleNamespace

from action_tracker.database.integration import (
    acknowledge_compatibility_exports,
    build_daily_bundle,
    commit_daily_bundle,
    storage_mode,
)
from action_tracker.database.production import database_status, repair_primary_localization_regression
from action_tracker.database.connection import connect
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


def test_cutover_preflight_is_read_only_and_requires_shadow(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_SHADOW")
    cfg["paths"]["master"].write_bytes(b"master")
    (cfg["paths"]["state"] / "known_skus.csv").write_bytes(b"known")
    (cfg["paths"]["state"] / "offline_skus.csv").write_bytes(b"offline")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_SHADOW")
    from action_tracker.database.production import ProductionDatabaseError, cutover_preflight
    # This synthetic fixture has an unacknowledged export, so the preflight is
    # expected to fail before parity; importantly it must not promote or mutate
    # the role.
    try:
        cutover_preflight(cfg["storage"]["db_path"], master=cfg["paths"]["master"],
                          known=cfg["paths"]["state"] / "known_skus.csv",
                          offline=cfg["paths"]["state"] / "offline_skus.csv")
    except ProductionDatabaseError as exc:
        assert "CUTOVER_EXPORT_SYNC_PENDING" in str(exc)
    else:
        raise AssertionError("invalid cutover fixture unexpectedly passed")
    assert database_status(cfg["storage"]["db_path"])["metadata"]["database_role"] == "SHADOW"


def test_primary_repository_matches_compatibility_shapes(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    with connect(cfg["storage"]["db_path"]) as db:
        db.execute(
            "UPDATE product_localizations SET cat1=?,cat2=?,spec=?,description=?,details=? "
            "WHERE official_sku=? AND language='es'",
            ("Hogar", "Limpieza", "1 unidad", "Descripción", "Detalles", "1001"),
        )
    repo = ProductionRepository(cfg["storage"]["db_path"])
    current = repo.load_current_products()
    known = repo.load_known_skus()
    assert set(current) == {"1001"}
    assert current["1001"]["cat2_es"] == "Limpieza"
    assert current["1001"]["desc_es"] == "Descripción"
    assert set(known) == {"1001", "1002"}
    assert known["1002"]["last_status"] == "MISSING"


def test_primary_repository_projection_is_read_only_and_current_only(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    repo = ProductionRepository(cfg["storage"]["db_path"])
    rows = repo.load_current_export_records()
    assert [row["sku"] for row in rows] == ["1001"]
    assert rows[0]["name_es"] == "Producto"


def test_primary_localization_recovery_restores_only_empty_fields_and_rebuilds_content_events(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    run_id = "2026-08-30_010000"
    commit_daily_bundle(cfg, _bundle(cfg, run_id=run_id), mode="SQLITE_PRIMARY")
    snapshot = tmp_path / "snapshots" / "2026-08-29" / "2026-08-29_010000" / "products_normalized.csv"
    snapshot.parent.mkdir(parents=True)
    snapshot.parent.joinpath("run_report.json").write_text(json.dumps({
        "run_id": "2026-08-29_010000", "run_date": "2026-08-29",
        "snapshot": str(snapshot.parent), "commit_status": "FULL_COMMIT", "dry_run": False,
    }), encoding="utf-8")
    snapshot.parent.joinpath("qa_report.json").write_text(json.dumps({
        "passed": True, "state": "PASS",
    }), encoding="utf-8")
    snapshot.write_text(
        "sku,name_es,cat1_es,cat2_es,spec_es,desc_es,details_es,cat1_zh,cat2_zh,spec_zh,desc_zh,details_zh,product_url,image_url\n"
        "1001,Producto,Hogar,Limpieza,1 unidad,Descripción,Detalles,家居,清洁,1件,中文描述,中文详情,,\n",
        encoding="utf-8",
    )
    with connect(cfg["storage"]["db_path"]) as db:
        db.execute(
            "UPDATE product_localizations SET details=? WHERE official_sku=? AND language='es'",
            ("Detalle actual", "1001"),
        )
        db.execute(
            "INSERT INTO event_history(canonical_id,official_sku,occurred_at,event_type,run_id,evidence,event_key) VALUES(?,?,?,?,?,?,?)",
            ("ACT0001001", "1001", "2026-08-30", "CONTENT_CHANGE", run_id, run_id, "bad-event"),
        )
    result = repair_primary_localization_regression(
        cfg["storage"]["db_path"], trusted_snapshot=snapshot, run_id=run_id,
    )
    assert result["status"] == "REPAIRED"
    assert result["restored"]["es"] == 4
    assert result["restored"]["zh"] == 5
    assert result["commit_event_count_before"] == 0
    assert result["commit_event_count_after"] == 1
    current = ProductionRepository(cfg["storage"]["db_path"]).load_current_products()["1001"]
    assert current["cat2_es"] == "Limpieza"
    assert current["details_es"] == "Detalle actual"
    with connect(cfg["storage"]["db_path"]) as db:
        assert db.execute("SELECT COUNT(*) FROM event_history WHERE run_id=? AND event_type='CONTENT_CHANGE'", (run_id,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM migration_source_issues WHERE issue_type='LOCALIZATION_REGRESSION_REPAIRED'").fetchone()[0] == 1


def test_primary_localization_recovery_rejects_unproven_snapshot(tmp_path: Path):
    import pytest

    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    snapshot = tmp_path / "products_normalized.csv"
    snapshot.write_text("sku,name_es\n1001,Producto\n", encoding="utf-8")
    with pytest.raises(Exception, match="TRUSTED_SNAPSHOT_EVIDENCE_MISSING"):
        repair_primary_localization_regression(cfg["storage"]["db_path"], trusted_snapshot=snapshot, run_id="r1")


def test_primary_localization_recovery_rejects_non_v2_primary(tmp_path: Path):
    import pytest

    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    commit_daily_bundle(cfg, _bundle(cfg), mode="SQLITE_PRIMARY")
    snapshot = tmp_path / "snapshots" / "2026-08-29" / "2026-08-29_010000" / "products_normalized.csv"
    snapshot.parent.mkdir(parents=True)
    snapshot.parent.joinpath("run_report.json").write_text(json.dumps({
        "run_id": "2026-08-29_010000", "run_date": "2026-08-29",
        "snapshot": str(snapshot.parent), "commit_status": "FULL_COMMIT", "dry_run": False,
    }), encoding="utf-8")
    snapshot.parent.joinpath("qa_report.json").write_text(json.dumps({"passed": True, "state": "PASS"}), encoding="utf-8")
    snapshot.write_text("sku,name_es\n1001,Producto\n", encoding="utf-8")
    with connect(cfg["storage"]["db_path"]) as db:
        db.execute("UPDATE schema_metadata SET value='3.0.0' WHERE key='schema_version'")
    with pytest.raises(Exception, match="PRIMARY_V2_DATABASE_REQUIRED"):
        repair_primary_localization_regression(cfg["storage"]["db_path"], trusted_snapshot=snapshot, run_id="r1")


def test_primary_writer_blocks_catastrophic_localization_coverage_drop(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_PRIMARY")
    first = _bundle(cfg, run_id="2026-08-29_010000")
    # Seed a complete Spanish detail projection.
    seed = dict(next(iter(first.current_products)))
    seed.update({"cat1_es": "Hogar", "cat2_es": "Limpieza", "spec_es": "1 unidad",
                 "desc_es": "Descripción", "details_es": "Detalles"})
    first = build_daily_bundle(
        run_id=first.run_id, observation_date=first.observation_date, qa_state=first.qa_state,
        today_records={"1001": seed}, baseline={"1001": seed},
        statuses={"1001": _status("1001", "ACTIVE")},
        known={"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"}},
        transition={"known": {"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"}}, "offline": []},
        today_set={"1001"}, observation_complete=True, price_events=[], event_events=[], review_rows=[], run_record={"dry_run": False}, snapshot_path=None,
    )
    commit_daily_bundle(cfg, first, mode="SQLITE_PRIMARY")
    broken = dict(seed)
    for field in ("cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
        broken[field] = ""
    second = build_daily_bundle(
        run_id="2026-08-30_010000", observation_date="2026-08-30", qa_state="PASS",
        today_records={"1001": broken}, baseline={"1001": seed},
        statuses={"1001": _status("1001", "ACTIVE")},
        known={"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"}},
        transition={"known": {"1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE"}}, "offline": []},
        today_set={"1001"}, observation_complete=True, price_events=[], event_events=[], review_rows=[], run_record={"dry_run": False}, snapshot_path=None,
    )
    import pytest
    with pytest.raises(Exception, match="DB_LOCALIZATION_COVERAGE_REGRESSION"):
        commit_daily_bundle(cfg, second, mode="SQLITE_PRIMARY")


def test_bundle_preserves_last_run_id_for_untouched_historical_rows(tmp_path: Path):
    cfg = _cfg(tmp_path, mode="SQLITE_SHADOW")
    statuses = {"1001": _status("1001", "ACTIVE")}
    known = {
        "1001": {"official_sku": "1001", "canonical_id": "ACT0001001", "last_status": "ACTIVE", "last_run_id": "new-run"},
        "1002": {"official_sku": "1002", "canonical_id": "ACT0001002", "last_status": "OFFLINE", "last_run_id": "historical-run"},
    }
    bundle = build_daily_bundle(
        run_id="new-run", observation_date="2026-08-30", qa_state="PASS",
        today_records={"1001": {"sku": "1001", "canonical_id": "ACT0001001", "name_es": "Producto", "current_price": 2.5, "status": "CURRENT"}},
        baseline={}, statuses=statuses, known=known,
        transition={"known": known, "offline": []}, today_set={"1001"}, observation_complete=True,
        price_events=[], event_events=[], review_rows=[], run_record={"dry_run": False}, snapshot_path=None,
    )
    lifecycle = {str(row["sku"]): row for row in bundle.lifecycle_updates}
    assert lifecycle["1001"]["last_run_id"] == "new-run"
    assert lifecycle["1002"]["last_run_id"] == "historical-run"


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
        assert db.execute("SELECT status FROM products WHERE official_sku='1001'").fetchone()[0] == "MISSING"


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
