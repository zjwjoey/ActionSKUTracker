from pathlib import Path

import pytest

from action_tracker.database.production import CommitBundle, ProductionDatabaseError, ProductionWriter, import_legacy_baseline_v2
from action_tracker.database.connection import connect


def _bundle(run_id="run-1", base=None):
    return CommitBundle(
        run_id=run_id,
        observation_date="2026-08-30",
        qa_state="PASS",
        base_commit_id=base,
        current_products=({"sku": "1001", "name_es": "Producto", "current_price": 2.5},),
        localization_updates=({"sku": "1001", "language": "zh", "name": "商品"},),
        lifecycle_updates=({"sku": "1001", "current_status": "ACTIVE", "last_run_id": run_id},),
        observations=({"run_id": run_id, "sku": "1001", "observation_date": "2026-08-30", "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True},),
    )


def test_v2_commit_is_atomic_and_idempotent(tmp_path: Path):
    db = tmp_path / "action.db"
    writer = ProductionWriter(db)
    bundle = _bundle()
    commit_id = writer.commit(bundle)
    assert writer.commit(bundle) == commit_id
    with connect(db) as conn:
        assert conn.execute("select count(*) from commit_batches").fetchone()[0] == 1
        assert conn.execute("select count(*) from products").fetchone()[0] == 1
        assert conn.execute("select count(*) from observations").fetchone()[0] == 1
        assert conn.execute("select value from schema_metadata where key='schema_version'").fetchone()[0] == "2.0.0"


def test_base_commit_gate_rejects_stale_writer(tmp_path: Path):
    db = tmp_path / "action.db"
    writer = ProductionWriter(db)
    first = writer.commit(_bundle())
    with pytest.raises(ProductionDatabaseError, match="BASELINE_CHANGED"):
        writer.commit(_bundle("run-2", base="wrong"))
    with connect(db) as conn:
        assert conn.execute("select count(*) from commit_batches").fetchone()[0] == 1
        assert conn.execute("select status from export_sync where commit_id=?", (first,)).fetchone()[0] == "PENDING"


def test_non_pass_bundle_does_not_write(tmp_path: Path):
    db = tmp_path / "action.db"
    writer = ProductionWriter(db)
    bad = _bundle()
    object.__setattr__(bad, "qa_state", "FAIL")
    with pytest.raises(ProductionDatabaseError, match="QA_NOT_PASS"):
        writer.commit(bad)
    with connect(db) as conn:
        assert conn.execute("select count(*) from commit_batches").fetchone()[0] == 0


def test_validate_production_database(tmp_path: Path):
    db = tmp_path / "action.db"
    ProductionWriter(db).commit(_bundle())
    from action_tracker.database.production import validate_production_database
    result = validate_production_database(db)
    assert result["integrity"] == "PASS"
    assert result["foreign_keys"] == "PASS"


def test_v2_uses_dedicated_reviews_table_and_events_view(tmp_path: Path):
    db = tmp_path / "action.db"
    bundle = _bundle()
    object.__setattr__(bundle, "review_rows", ({"sku": "1001", "问题类型": "DATA_INCONSISTENCY", "证据": "x", "建议动作": "核对"},))
    ProductionWriter(db).commit(bundle)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_database_boolean_parser_does_not_treat_false_text_as_true():
    from action_tracker.database.production import _to_bool
    assert _to_bool("false") is False
    assert _to_bool("0") is False
    assert _to_bool("true") is True


def test_legacy_baseline_rebuilds_incompatible_v1_database_atomically(tmp_path: Path):
    db = tmp_path / "legacy.db"
    master = tmp_path / "master.xlsx"
    state = tmp_path / "state"
    state.mkdir()
    # Simulate the old mirror identity; the V2 schema cannot reuse these
    # table definitions in place.
    with connect(db) as conn:
        conn.executescript("CREATE TABLE products (sku TEXT PRIMARY KEY); CREATE TABLE runs (run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL);")
    master.write_bytes(b"not-used")
    # Use the existing reader fixtures indirectly by replacing the call with a
    # minimal valid workbook/state setup below.
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "01_SKU_ZH_CURRENT"
    ws.append(["Canonical_ID", "SKU", "中文品名", "当前售价 (€)"])
    ws.append(["ACT0001001", "1001", "商品", 2.5])
    es = wb.create_sheet("02_SKU_ES_CURRENT")
    es.append(["Canonical_ID", "SKU", "西班牙语品名", "当前售价 (€)"])
    es.append(["ACT0001001", "1001", "Producto", 2.5])
    wb.save(master)
    (state / "known_skus.csv").write_text("canonical_id,official_sku,first_seen_date,last_seen_date,last_status,missing_count,last_missing_date,offline_date,last_state_observation_date,ever_offline,last_run_id,updated_at\nACT0001001,1001,2026-08-29,2026-08-30,ACTIVE,0,,,,false,,2026-08-30\n", encoding="utf-8-sig")
    (state / "offline_skus.csv").write_text("canonical_id,official_sku,offline_date,last_seen_date,last_status\n", encoding="utf-8-sig")
    commit_id = import_legacy_baseline_v2(db, master_path=master, state_dir=state, observed_at="2026-08-30")
    assert commit_id
    from action_tracker.database.production import database_status
    status = database_status(db)
    assert status["metadata"]["schema_family"] == "ACTION_SQLITE_DATA"
    assert status["products"] == 1
