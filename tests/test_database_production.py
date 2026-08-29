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
