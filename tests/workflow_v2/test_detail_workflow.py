from action_tracker.workflow_v2.runner import WorkflowV2Runner
from action_tracker.workflow_v2.context import new_context
from action_tracker.database.schema import migrate_v2
from action_tracker.workflow_v2.detail_state import detail_is_stale, mark_detail_success
from datetime import datetime, timezone


def test_detail_pending_does_not_erase_fact_commit(workflow_root, source_row):
    source_row["detail_status"] = "DETAIL_PENDING"
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100"}).run()
    assert result.context.source_commit_id
    assert result.stages["FACT_COMMIT"].status == "PASS"


def test_detail_freshness_is_independent_of_presence(tmp_path):
    db = tmp_path / "detail.db"; migrate_v2(db, role="SHADOW")
    from action_tracker.database.connection import connect
    with connect(db) as handle:
        handle.execute("INSERT INTO products(canonical_id,official_sku,status,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)", ("ACT0000100", "100", "CURRENT"))
    mark_detail_success(db, "100", run_id="r1", source_hash="h", at="2026-10-01T00:00:00+00:00")
    assert detail_is_stale(db, "100", now=datetime(2026, 10, 2, tzinfo=timezone.utc), max_age_days=7) is False
    assert detail_is_stale(db, "100", now=datetime(2026, 10, 10, tzinfo=timezone.utc), max_age_days=7) is True
