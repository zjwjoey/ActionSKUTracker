"""Actual detail freshness, not presence timestamps; all fixtures are local."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from action_tracker.products.updater import plan_updates
from action_tracker.database.production import ProductionWriter, apply_detail_corrections, ProductionDatabaseError
from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.database.repository import ProductionRepository
from test_post_merge_production_safety import _primary_db


def plan(base, light=None):
    return plan_updates({"1001": SimpleNamespace(status="ACTIVE", canonical_id="ACT0001001")},
                        {"1001": base}, {"1001": light} if light else {}, detail_refresh_days=7)


def record(**kwargs):
    return dict(desc_es="Description", details_es="Color: Rojo", cat2_es="B", current_price=1,
                spec_es="100 ml", product_url="https://example.test/p/1001/", last_seen=datetime.now().date().isoformat(), **kwargs)


def test_recent_presence_does_not_hide_expired_detail_or_price_change():
    base = record(detail_last_success_at=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat())
    result = plan(base, {"current_price": 2})
    assert result[0]["need_detail"] and result[0]["reason"] == "DETAIL_REFRESH"


def test_sitemap_only_missing_detail_has_baseline_url():
    base = record(); base["details_es"] = "Número del artículo: 1001"
    result = plan(base)
    assert result[0]["need_detail"] and result[0]["light"]["product_url"] == base["product_url"]


def test_confirmed_official_empty_source_waits_until_refresh():
    base = record(detail_last_success_at=datetime.now(timezone.utc).isoformat(), detail_status="COMPLETE")
    base["desc_es"] = ""; base["details_es"] = "Número del artículo: 1001"
    assert not plan(base)
    base["detail_last_success_at"] = (datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    assert plan(base)[0]["need_detail"]


def test_unregistered_complete_legacy_detail_does_not_trigger_full_crawl():
    assert not plan(record())


def test_unknown_freshness_bootstrap_is_bounded_and_does_not_repeat_success():
    statuses = {str(i): SimpleNamespace(status="ACTIVE", canonical_id=str(i)) for i in range(100)}
    bases = {sku: record() for sku in statuses}
    result = plan_updates(statuses, bases, {}, unknown_detail_budget=25)
    assert len(result)==25 and all(r['reason']=='DETAIL_FRESHNESS_UNKNOWN' for r in result)
    for r in result:
        bases[r['sku']]['detail_last_success_at']=datetime.now(timezone.utc).isoformat()
    again = plan_updates(statuses,bases,{},unknown_detail_budget=25)
    assert len(again)==25 and not ({r['sku'] for r in result}&{r['sku'] for r in again})


def test_pending_work_is_separate_from_unknown_bootstrap_budget():
    statuses = {str(i): SimpleNamespace(status="ACTIVE", canonical_id=str(i)) for i in range(30)}
    bases = {sku: record() for sku in statuses};bases['0']['detail_status']='PENDING'
    result = plan_updates(statuses,bases,{},unknown_detail_budget=25)
    assert len(result)==26 and next(p for p in result if p['sku']=='0')['reason']=='DETAIL_PENDING'


def test_failed_detail_is_retried_even_when_other_fields_complete():
    assert plan(record(detail_status="ACCESS_INTERRUPTED"))[0]["need_detail"]


def test_changed_source_hash_invalidates_recent_detail():
    assert plan(record(detail_last_success_at=datetime.now(timezone.utc).isoformat(), source_hash="new", detail_source_hash="old"))[0]["need_detail"]


def test_verified_no_change_correction_persists_actual_success(tmp_path):
    path = tmp_path/"primary.db"; _primary_db(path)
    stamp = datetime.now(timezone.utc).isoformat()
    result = apply_detail_corrections(path, parent_run_id="parent", mode="APPLY",
        details_by_sku={"1001": {"desc_es": "old desc", "details_es": "old details"}}, detail_verified_at={"1001": stamp})
    assert result["applied_fields"] == 0 and result["commit_id"] != "c1"
    state = ProductionRepository(path).load_detail_states()["1001"]
    assert state["detail_last_success_at"] == stamp and state["detail_status"] == "COMPLETE"
    with connect(path) as db:
        assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
        assert db.execute("SELECT current_price FROM products").fetchone()[0] == 1.99


def test_failed_correction_does_not_mark_success(tmp_path):
    path = tmp_path/"primary.db"; _primary_db(path)
    with pytest.raises(ProductionDatabaseError):
        apply_detail_corrections(path, parent_run_id="parent", mode="APPLY", details_by_sku={"missing": {"desc_es": "d", "details_es": "v"}},
                                detail_verified_at={"missing": datetime.now(timezone.utc).isoformat()})
    assert not ProductionRepository(path).load_detail_states()


def test_carried_detail_does_not_advance_success_and_pending_preserves_it(tmp_path):
    path = tmp_path/"primary.db"; _primary_db(path)
    stamp = datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        ProductionWriter._record_detail_success(db, [record(sku="1001", detail_fields_source="DETAIL_CURRENT_RUN", detail_status="COMPLETE", detail_last_success_at=stamp)], "actual")
        ProductionWriter._record_detail_success(db, [record(sku="1001", detail_fields_source="BASELINE", detail_status="NOT_REQUIRED", detail_last_success_at="2099-01-01")], "carry")
        ProductionWriter._record_detail_success(db, [record(sku="1001", detail_status="ACCESS_INTERRUPTED")], "failed")
    state = ProductionRepository(path).load_detail_states()["1001"]
    assert state["detail_last_success_at"] == stamp and state["detail_last_success_run_id"] == "actual"
    assert state["detail_status"] == "ACCESS_INTERRUPTED"
