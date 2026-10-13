"""CI_SAFE: same-source evidence and canonical ownership in temporary fixtures."""
import csv
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.integration import _localization, build_daily_bundle, snapshot_digest
from action_tracker.database.production import CommitBundle, ProductionWriter
from action_tracker.database.repository import ProductionRepository
from action_tracker.monitor.sku_monitor import run_sku_monitor
from action_tracker.orchestrator.daily import (_merge_light, _verified_fields_after_light_merge,
                                             _build_current_records, _build_lifecycle_events)
from action_tracker.products import parser, updater
from action_tracker.services.listing_evidence import (TEXT_FIELDS, _digest, listing_evidence,
    listing_differences, committed_listing_baseline)
from action_tracker.state import apply_state_transition


def canonical(sku="1001"):
    return dict(sku=sku, canonical_id=f"ACT{sku.zfill(7)}", name_es="Canonical name",
        cat1_es="Vivienda", cat2_es="Decoración", spec_es="1.5 l", desc_es="Description",
        details_es=f"Número del artículo: {sku}; Color: Rojo", current_price=2.5,
        original_price=3.0, product_url=f"https://www.action.com/es-es/p/{sku}/fixture/",
        image_url="https://asset.action.com/detail.jpg", unit_price="2,50 €/ud.",
        status="CURRENT", first_seen="2026-01-09",
        detail_last_success_at=datetime.now(timezone.utc).isoformat())


def card(base):
    return {**{f: base[f] for f in TEXT_FIELDS}, "sku": base["sku"],
            "current_price": 2.5, "original_price": 3.0, "discount": None,
            "_price_observation_complete": True,
            "_listing_evidence_confidence": "COMMITTED_HASH_BOUND",
            "_listing_categories": [base["cat1_es"]]}


def plan(base, light, prior=None, budget=0):
    sku = base["sku"]
    return updater.plan_updates({sku: SimpleNamespace(status="ACTIVE", canonical_id=base["canonical_id"])},
        {sku: base}, {sku: light}, listing_baseline={sku: prior} if prior else {},
        unknown_detail_budget=budget)


@pytest.mark.parametrize("field,value", [("cat1_es", "Hogar"), ("name_es", "Listing name"),
    ("spec_es", "1,5 litros"), ("product_url", "https://www.action.com/es-es/p/1001/card-slug/"),
    ("image_url", "https://asset.action.com/card.jpg")])
def test_unchanged_listing_is_not_compared_to_canonical_detail(field, value):
    base = canonical(); prior = card(base); prior[field] = value
    prior["_listing_categories"] = [prior["cat1_es"]]
    assert plan(base, dict(prior), prior) == []


@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_real_same_layer_listing_change_requires_detail_confirmation(field):
    base = canonical(); prior = card(base); changed = dict(prior)
    changed[field] += "changed"
    if field == "cat1_es": changed["_listing_categories"] = [changed[field]]
    result = plan(base, changed, prior)
    assert len(result) == 1 and result[0]["need_detail"]
    assert result[0]["listing_changed_fields"] == (field,)
    assert result[0]["reason"] == ("IMAGE_CHANGE" if field == "image_url" else "CONTENT_CHANGE")


@pytest.mark.parametrize("field", ["name_es", "spec_es", "cat1_es"])
def test_explicit_empty_listing_field_requires_confirmation_without_deleting_fact(field):
    base = canonical(); prior = card(base); current = dict(prior)
    current[field] = ""
    if field == "cat1_es": current["_listing_categories"] = []
    assert field in listing_differences(current, prior)
    result = plan(base, current, prior)
    assert result[0]["need_detail"]
    merged = dict(base); _merge_light(merged, current)
    assert merged[field] == base[field]


def test_listing_membership_order_does_not_change_canonical_category():
    base = canonical(); before = card(base); current = card(base)
    before.update(cat1_es="Hogar", _listing_categories=["Hogar", "Vivienda"])
    current.update(cat1_es="Vivienda", _listing_categories=["Vivienda", "Hogar"])
    assert not listing_differences(current, before)
    assert plan(base, current, before) == []


def test_unknown_listing_comparison_is_bounded_and_distinct_from_content_change():
    bases = {str(i): canonical(str(i)) for i in range(40)}
    lights = {sku: {**card(base), "cat1_es": "Hogar"} for sku, base in bases.items()}
    statuses = {sku: SimpleNamespace(status="ACTIVE", canonical_id=base["canonical_id"])
                for sku, base in bases.items()}
    result = updater.plan_updates(statuses, bases, lights, unknown_detail_budget=7)
    assert len(result) == 7
    assert all(p["reason"] == "LISTING_BASELINE_UNKNOWN" and p["need_detail"] for p in result)


def test_partial_manual_listing_baseline_remains_bounded_unknown():
    base = canonical(); light = card(base); light["spec_es"] = "Changed"
    prior = card(base); prior.pop("spec_es")
    assert plan(base, light, prior, budget=1)[0]["reason"] == "LISTING_BASELINE_UNKNOWN"


@pytest.mark.parametrize("confidence", [None, "", "MISSPELLED", "LEGACY_MATCHED", "UNKNOWN"])
def test_only_explicit_bound_listing_confidence_is_authoritative(confidence):
    base = canonical(); light = {**card(base), "cat1_es": "Hogar"}
    prior = {**light, "_listing_evidence_confidence": confidence}
    assert plan(base, light, prior, budget=1)[0]["reason"] == "LISTING_BASELINE_UNKNOWN"


def test_price_change_does_not_hide_real_listing_text_change():
    base = canonical(); prior = card(base); light = {**prior, "current_price": 2.75, "name_es": "New name"}
    result = plan(base, light, prior)
    assert result[0]["reason"] == "CONTENT_CHANGE" and result[0]["need_detail"]


def test_price_only_change_retains_canonical_text_and_listing_price_source():
    base = canonical(); light = card(base)
    light.update(current_price=2.75, original_price=None, cat1_es="Hogar", spec_es="1,5 litros")
    prior = {**light, "current_price": 2.5, "original_price": 3.0}
    assert plan(base, light, prior)[0]["reason"] == "PRICE_CHANGE_CANDIDATE"
    result = dict(base); _merge_light(result, light)
    result = updater.retain_historical_facts(base, result, reappeared=False,
        verified_fields=_verified_fields_after_light_merge(result, light))
    assert result["current_price"] == 2.75 and result["original_price"] is None
    assert result["cat1_es"] == "Vivienda" and result["spec_es"] == "1.5 l"
    assert result["fact_field_provenance"]["cat1_es"]["state"] == "HISTORY_RETAINED"
    assert result["fact_field_provenance"]["current_price"]["source"] == "LISTING_CURRENT_RUN"


def test_fresh_detail_owns_all_nonempty_canonical_fields_after_final_listing_merge(tmp_path, monkeypatch):
    base = canonical(); light = card(base)
    light.update(name_es="Listing name", cat1_es="Hogar", spec_es="1,5 litros",
                 product_url="https://www.action.com/es-es/p/1001/card-slug/",
                 image_url="https://asset.action.com/card.jpg", unit_price="Earlier unit price")
    detail = {**base, "_price_observation_complete": True, "discount": None}
    monkeypatch.setattr(parser, "fetch_product_detail", lambda *_args, **_kwargs: dict(detail))
    item = dict(sku="1001", canonical_id=base["canonical_id"], reason="CONTENT_CHANGE",
                need_detail=True, light=light, detail_url=base["product_url"])
    _, result = updater.fetch_and_merge(SimpleNamespace(sleep=lambda: None), [item], {"1001": base}, tmp_path)
    merged = result["1001"]; _merge_light(merged, light, skip_raw_tags=True)
    merged = updater.retain_historical_facts(base, merged, reappeared=False,
        verified_fields=_verified_fields_after_light_merge(merged, light))
    for field in (*TEXT_FIELDS, "cat2_es", "desc_es", "details_es", "unit_price"):
        assert merged[field] == detail[field]
        assert merged["fact_field_provenance"][field]["source"] == "DETAIL_CURRENT_RUN"


def test_failed_detail_retains_canonical_history_and_honest_origin(tmp_path, monkeypatch):
    base = canonical(); light = {**card(base), "name_es": "Unconfirmed change", "cat1_es": "Hogar"}
    monkeypatch.setattr(parser, "fetch_product_detail", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")))
    item = dict(sku="1001", canonical_id=base["canonical_id"], reason="CONTENT_CHANGE", need_detail=True,
                light=light, detail_url=base["product_url"])
    completed = []; evidence = []
    _, result = updater.fetch_and_merge(SimpleNamespace(sleep=lambda: None), [item], {"1001": base}, tmp_path,
        detail_completed_skus=completed, detail_evidence=evidence)
    for field in ("name_es", "cat1_es"):
        assert result["1001"][field] == base[field]
        assert result["1001"]["fact_field_provenance"][field]["state"] == "HISTORY_RETAINED"
    assert not completed and evidence


def _seed(path, *, run_id="seed", evidence=None, snapshot=None, qa="PASS", head=None):
    record = canonical()
    return ProductionWriter(path, role="PRIMARY").commit(CommitBundle(run_id=run_id,
        observation_date="2026-10-13", qa_state=qa, base_commit_id=head,
        current_products=(record,), localization_updates=(_localization(record, "es"),),
        observations=({"run_id": run_id, "observation_date": "2026-10-13", "official_sku": "1001", "presence_state": "PRESENT", "listing_present": 1,
                       "observation_complete": 1},),
        run_record={"listing_field_evidence": evidence} if evidence is not None else {},
        snapshot_path=str(snapshot) if snapshot else None,
        snapshot_hash=snapshot_digest(snapshot) if snapshot else None))


def test_committed_evidence_reconstructs_same_layer_listing(tmp_path):
    path = tmp_path / "primary.db"; light = card(canonical()); light["cat1_es"] = "Hogar"
    proof = listing_evidence({"1001": light}, run_id="seed", business_date="2026-10-13")
    head = _seed(path, evidence=proof)
    previous = committed_listing_baseline(path, expected_base_commit_id=head)
    assert previous["1001"]["cat1_es"] == "Hogar"
    assert previous["1001"]["_listing_evidence_confidence"] == "COMMITTED_HASH_BOUND"
    assert plan(canonical(), light, previous["1001"]) == []


@pytest.mark.parametrize("mutation", ["missing_field", "field_type", "categories_type", "duplicate", "date", "hash", "run", "identity", "omitted_sku"])
def test_invalid_newest_evidence_remains_unknown_and_never_uses_older_valid_values(tmp_path, mutation):
    path = tmp_path / "primary.db"; old = listing_evidence({"1001": card(canonical())}, run_id="old", business_date="2026-10-13")
    first = _seed(path, run_id="old", evidence=old)
    new = listing_evidence({"1001": card(canonical())}, run_id="new", business_date="2026-10-13")
    if mutation == "missing_field": new["rows"][0].pop("spec_es")
    if mutation == "field_type": new["rows"][0]["spec_es"] = 42
    if mutation == "categories_type": new["rows"][0]["_listing_categories"] = "Hogar"
    if mutation == "duplicate": new["rows"].append(dict(new["rows"][0]))
    if mutation == "date": new["business_date"] = "2026-10-12"
    if mutation == "run": new["run_id"] = "wrong"
    if mutation == "identity": new["rows"][0]["sku"] = "wrong"
    if mutation == "omitted_sku": new["rows"] = []
    new["rows_hash"] = "tampered" if mutation == "hash" else _digest(new["rows"])
    head = _seed(path, run_id="new", evidence=new, head=first)
    previous = committed_listing_baseline(path, expected_base_commit_id=head)
    assert previous["1001"]["_listing_source_run_id"] == "new"
    assert previous["1001"]["_listing_evidence_confidence"] == "UNKNOWN"
    light = {**card(canonical()), "spec_es": "Changed"}
    assert plan(canonical(), light, previous["1001"], budget=1)[0]["reason"] == "LISTING_BASELINE_UNKNOWN"


def test_prior_base_head_mismatch_is_refused(tmp_path):
    path = tmp_path / "primary.db"; head = _seed(path)
    with pytest.raises(ValueError, match="LISTING_BASELINE_HEAD_CHANGED"):
        committed_listing_baseline(path, expected_base_commit_id="different")


def _snapshot(path):
    directory = path / "snapshots" / "2026-10-13" / "seed"; directory.mkdir(parents=True)
    light = card(canonical()); light["cat1_es"] = "Hogar"
    (directory / "run_manifest.json").write_text(json.dumps({"run_id": "seed", "observation_date": "2026-10-13"}), encoding="utf-8")
    (directory / "listing_raw.json").write_text(json.dumps({"hogar": [light]}), encoding="utf-8")
    with (directory / "listing_products.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["sku", *TEXT_FIELDS]); writer.writeheader()
        writer.writerow({key: light[key] for key in ["sku", *TEXT_FIELDS]})
    return directory


@pytest.mark.parametrize("changed_after_commit", [False, True])
def test_legacy_snapshot_confidence_records_hash_verification(tmp_path, changed_after_commit):
    path = tmp_path / "primary.db"; directory = _snapshot(tmp_path)
    head = _seed(path, snapshot=directory)
    if changed_after_commit:
        (directory / "run_report.json").write_text('{"finalized":true}', encoding="utf-8")
    previous = committed_listing_baseline(path, expected_base_commit_id=head)
    item = previous["1001"]
    assert item["_listing_evidence_confidence"] == ("LEGACY_MATCHED" if changed_after_commit else "COMMITTED_HASH_BOUND")
    assert len(item["_listing_file_sha256"]["listing_products.csv"]) == 64
    if changed_after_commit:
        assert plan(canonical(), card(canonical()), item, budget=1) == []
        light = {**card(canonical()), "cat1_es": "Hogar"}
        assert plan(canonical(), light, item, budget=1)[0]["reason"] == "LISTING_BASELINE_UNKNOWN"


@pytest.mark.parametrize("known_status", [None, "MISSING", "OFFLINE"])
def test_historical_identity_without_current_baseline_never_becomes_new(tmp_path, known_status):
    path = tmp_path / "primary.db"; history = canonical(); history.update(status="HISTORICAL", current_price=None)
    writer = ProductionWriter(path, role="PRIMARY")
    head = writer.commit(CommitBundle(run_id="historical", observation_date="2026-01-09", qa_state="PASS",
        current_products=(history,), localization_updates=(_localization(history, "es"),)))
    repo = ProductionRepository(path); current = repo.load_current_products(); all_facts = repo.load_product_baseline()
    assert "1001" not in current and "1001" in all_facts
    known = {} if known_status is None else {"1001": {"last_status": known_status,
        "first_seen_date": "2026-01-09", "missing_count": "2"}}
    statuses, today = run_sku_monitor(["1001"], {}, current, known,
        business_date="2026-10-13", historical_identities=all_facts)
    st = statuses["1001"]
    assert st.status == ("ACTIVE" if known_status is None else "REAPPEARED")
    assert st.first_seen == "2026-01-09" and st.ever_seen
    assert st.event == (None if known_status is None else "REAPPEARED")
    transition = apply_state_transition(known, statuses, "2026-10-13", "observed")
    assert transition["known"]["1001"]["first_seen_date"] == "2026-01-09"
    records = _build_current_records(all_facts, {}, statuses, today, "2026-10-13")
    records["1001"]["current_price"] = 2.99
    events = _build_lifecycle_events(statuses, "2026-10-13", "observed")
    assert not any(e["事件类型"] == "FIRST_SEEN" for e in events)
    bundle = build_daily_bundle(run_id="observed", observation_date="2026-10-13", qa_state="PASS",
        today_records=records, baseline=all_facts, statuses=statuses, known=known, transition=transition,
        today_set=today, observation_complete=True, price_events=[], event_events=events, review_rows=[], run_record={})
    writer.commit(replace(bundle, base_commit_id=head, requires_collection_integrity=False))
    with connect(path) as db:
        assert db.execute("SELECT first_seen_at FROM products WHERE official_sku='1001'").fetchone()[0] == "2026-01-09"
        assert db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='FIRST_SEEN'").fetchone()[0] == 0
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_historical_identity_does_not_expand_presence_universe():
    statuses, today = run_sku_monitor([], {}, {}, {}, historical_identities={"1001": canonical()})
    assert not statuses and not today


def test_current_baseline_known_gap_preserves_first_seen():
    base = canonical()
    statuses, _ = run_sku_monitor(["1001"], {}, {"1001": base}, {})
    assert statuses["1001"].status == "ACTIVE"
    assert statuses["1001"].first_seen == "2026-01-09"
    assert statuses["1001"].event is None


def test_unconfirmed_unknown_changes_survive_next_bound_listing_baseline():
    base = canonical(); light = {**card(base), "cat1_es": "Hogar"}
    published = listing_evidence({"1001": light}, run_id="first", business_date="2026-10-13",
        canonical_records={"1001": base}, completed_detail_skus=set(), previous={})
    prior = {**published["rows"][0], "_listing_evidence_confidence": "COMMITTED_HASH_BOUND"}
    assert prior["_canonical_confirmation_pending"] == ["cat1_es"]
    result = plan(base, light, prior, budget=1)
    assert result[0]["reason"] == "LISTING_BASELINE_UNKNOWN"
    assert result[0]["need_detail"]
    confirmed = listing_evidence({"1001": light}, run_id="second", business_date="2026-10-13",
        canonical_records={"1001": base}, completed_detail_skus={"1001"}, previous={"1001": prior})
    assert not confirmed["rows"][0]["_canonical_confirmation_pending"]
    final = {**confirmed["rows"][0], "_listing_evidence_confidence": "COMMITTED_HASH_BOUND"}
    assert plan(base, light, final, budget=1) == []


@pytest.mark.parametrize("detail_complete", [False, True])
def test_unit_price_is_an_independent_own_field_detail_fact(tmp_path, monkeypatch, detail_complete):
    base = canonical(); light = card(base)
    light.update(current_price=2.75, original_price=None, unit_price="2,75 €/ud.")
    detail = {**base, "unit_price": "5,00 €/kg", "discount": None,
              "_price_observation_complete": detail_complete}
    monkeypatch.setattr(parser, "fetch_product_detail", lambda *_args, **_kwargs: dict(detail))
    item = dict(sku="1001", canonical_id=base["canonical_id"], reason="DETAIL_REFRESH", need_detail=True,
                light=light, detail_url=base["product_url"])
    _, updated = updater.fetch_and_merge(SimpleNamespace(sleep=lambda: None), [item], {"1001": base}, tmp_path)
    rec = updated["1001"]; _merge_light(rec, light, skip_raw_tags=True)
    rec = updater.retain_historical_facts(base, rec, reappeared=False,
        verified_fields=_verified_fields_after_light_merge(rec, light))
    assert rec["current_price"] == (2.5 if detail_complete else 2.75)
    assert rec["unit_price"] == "5,00 €/kg"
    assert rec["fact_field_provenance"]["unit_price"]["source"] == "DETAIL_CURRENT_RUN"


@pytest.mark.parametrize("listing_unit", ["2,75 €/ud.", ""])
def test_listing_unit_price_refresh_and_empty_history_retention_in_price_plan(tmp_path, listing_unit):
    base = canonical(); light = {**card(base), "current_price": 2.75, "unit_price": listing_unit}
    item = plan(base, light, card(base))[0]
    assert item["reason"] == "PRICE_CHANGE_CANDIDATE" and not item["need_detail"]
    _, updated = updater.fetch_and_merge(SimpleNamespace(sleep=lambda: None), [item], {"1001": base}, tmp_path)
    rec = updated["1001"]; _merge_light(rec, light, skip_raw_tags=False)
    rec = updater.retain_historical_facts(base, rec, reappeared=False,
        verified_fields=_verified_fields_after_light_merge(rec, light))
    assert rec["unit_price"] == (listing_unit or base["unit_price"])
    assert rec["fact_field_provenance"]["unit_price"] == {
        "state": "CURRENT_VERIFIED" if listing_unit else "HISTORY_RETAINED",
        "source": "LISTING_CURRENT_RUN" if listing_unit else "BASELINE"}
    assert rec["name_es"] == base["name_es"] and rec["spec_es"] == base["spec_es"]


def test_unit_price_only_change_is_a_lightweight_metadata_plan():
    base = canonical(); prior = card(base); light = {**prior, "unit_price": "5,00 €/kg"}
    item = plan(base, light, prior)[0]
    assert item["reason"] == "PRICE_METADATA_CHANGE" and not item["need_detail"]
    result = dict(base); _merge_light(result, light)
    assert result["current_price"] == base["current_price"]
    assert result["unit_price"] == "5,00 €/kg"


@pytest.mark.parametrize("observed_price,qa_state", [(2.99, "PASS"), (99999, "FAIL")])
def test_mocked_full_daily_uses_historical_identity_and_real_source_qa(tmp_path, monkeypatch, observed_price, qa_state):
    from action_tracker.orchestrator import daily
    from action_tracker.monitor.listing import LightProduct
    from action_tracker.monitor.sitemap import SitemapResult
    path = tmp_path / "primary.db"; history = canonical()
    history.update(status="HISTORICAL", current_price=None)
    head = ProductionWriter(path, role="PRIMARY").commit(CommitBundle(run_id="history",
        observation_date="2026-01-09", qa_state="PASS", current_products=(history,),
        localization_updates=(_localization(history, "es"),)))
    (tmp_path / "config").mkdir(); (tmp_path / "config" / "settings.yaml").write_text("{}", encoding="utf-8")
    cfg = {"project_root": tmp_path, "storage": {"mode": "SQLITE_PRIMARY", "db_path": path},
        "paths": {"master": tmp_path / "master.xlsx", "state": tmp_path / "state",
                  "snapshots": tmp_path / "snapshots", "staging": tmp_path / "staging"},
        "run": {"detail_refresh_days": 7, "detail_freshness_probe_limit": 25, "lock_stale_minutes": 1},
        "browser": {"cooldown_seconds": 0, "degraded_recovery_successes": 1},
        "site": {"base_url": "https://www.action.com/es-es/", "sitemap_url": "https://www.action.com/sitemap.xml",
                 "categories": {"hogar": "https://www.action.com/es-es/c/hogar/"}},
        "lifecycle": {"offline_confirmation_runs": 3, "max_detail_retries": 1},
        "qa": {"max_active_increase_percent": 100, "max_active_drop_percent": 100,
            "max_sitemap_listing_gap_percent": 100, "max_new_sku_percent": 100,
            "max_missing_percent": 100, "max_anomaly_count": 10, "price_min": 0.01, "price_max": 100}}

    class Browser:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def goto(self, *_args): return True
        def sleep(self): pass
        def close(self): pass
        def manifest(self): return {}

    light = LightProduct(sku="1001", name_es="Listing name", cat1_es="Hogar", spec_es="1,5 litros",
        product_url=history["product_url"], current_price=observed_price, original_price=None,
        unit_price="", discount=None, raw_tags="", image_url="https://asset.action.com/card.jpg",
        price_observation_complete=True)
    detail = {**canonical(), "current_price": observed_price, "original_price": None,
              "discount": None, "_price_observation_complete": True}
    monkeypatch.setattr(daily, "BrowserSession", Browser)
    monkeypatch.setattr(daily, "discover_categories", lambda _browser, cats: (cats, {"categories": []}))
    monkeypatch.setattr(daily, "fetch_sitemap", lambda *_args: SitemapResult("<urlset/>", [history["product_url"]], ["1001"], "fixture"))
    monkeypatch.setattr(daily.listing_mod, "scan_all_categories", lambda *_args, **_kwargs: ({"hogar": [light]}, {"Hogar": True}))
    monkeypatch.setattr(parser, "fetch_product_detail", lambda *_args, **_kwargs: dict(detail))
    monkeypatch.setattr(daily, "_evaluate_daily_collection_quality", lambda _cfg, _run, report, **_kwargs: report.update(collection_quality_state="PASS"))
    result = daily.run_daily(cfg, dry_run=True, fetch_details=True, business_date="2026-10-13")
    assert result["qa"]["state"] == qa_state
    assert result["run_report"]["new"] == 0 and result["run_report"]["reappeared"] == 0
    assert result["run_report"]["historical_identity_reused"] == 1
    assert result["run_report"]["detail_planned"] == result["run_report"]["detail_completed"] == 1
    assert result["run_report"]["listing_comparison"]["pending_confirmation_skus"] == 0
    with (tmp_path / "snapshots" / "2026-10-13" / result["run_id"] / "products_normalized.csv").open(encoding="utf-8-sig", newline="") as file:
        actual = next(csv.DictReader(file))
    assert actual["cat1_es"] == "Vivienda" and actual["name_es"] == "Canonical name"
    assert actual["spec_es"] == "1.5 l" and actual["first_seen"] == "2026-01-09"
    assert ProductionRepository(path).current_head() == head
