"""CI_SAFE: return-history contracts; SQLite and browser doubles in tmp_path."""
from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.production import CommitBundle, ProductionWriter, ProductionDatabaseError
from action_tracker.database.repository import ProductionRepository
from action_tracker.database.integration import build_daily_bundle, _localization
from action_tracker.monitor.sku_monitor import run_sku_monitor
from action_tracker.orchestrator.daily import _build_current_records, _build_lifecycle_events
from action_tracker.products.updater import (retain_historical_facts, plan_updates,
    fetch_and_merge, verified_detail_url, sitemap_detail_urls, _get_detail)
from action_tracker.services.hashing import localization_source_hash, localization_field_source_hash
from action_tracker.services.change import compute_changes
from action_tracker.state import apply_state_transition


def fact(sku="3207872"):
    return dict(sku=sku, canonical_id=f"ACT{sku}", name_es="Molde", name_zh="模具",
        cat1_es="Cocina", cat2_es="Moldes", spec_es="31 cm", desc_es="Silicona",
        details_es="Material: Silicona", current_price=2.99, original_price=None,
        unit_price="2,99 €/ud.", product_url=f"https://www.action.com/es-es/p/{sku}/molde/",
        image_url="https://asset.action.com/image.jpg", first_seen="2026-10-01",
        last_seen="2026-10-01", status="CURRENT")


@pytest.mark.parametrize("field", ["name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "product_url", "image_url"])
def test_partial_return_retains_each_fact(field):
    base = fact()
    result = retain_historical_facts(base, {field: None}, reappeared=True, verified_fields={})
    assert result[field] == base[field]
    assert result['fact_field_provenance'][field]['state'] == 'HISTORY_RETAINED'
    assert result['current_price'] is None
    assert result['_historical_current_price'] == 2.99
    assert result['name_zh'] == base['name_zh']


@pytest.mark.parametrize("url", ["https://www.action.com/es-es/p/9999999/a/", "https://evil.test/es-es/p/3207872/a/", "http://www.action.com/es-es/p/3207872/a/", "https://www.action.com@evil.test/es-es/p/3207872/a/"])
def test_detail_url_refuses_other_identity_or_origin(url):
    assert not verified_detail_url('3207872', url)


def test_sitemap_url_is_navigation_evidence_not_listing_fact():
    base = fact()
    st = SimpleNamespace(status='REAPPEARED', canonical_id=base['canonical_id'])
    url = base['product_url']
    plans = plan_updates({'3207872': st}, {'3207872': base}, {}, sitemap_urls=sitemap_detail_urls([url]))
    assert plans[0]['light'] is None
    assert plans[0]['detail_url'] == url


def test_missing_url_has_explicit_evidence_without_navigation(tmp_path):
    evidence = []
    assert _get_detail(None, {}, '3207872', {}, tmp_path/'ckpt', 1, detail_evidence=evidence) is None
    assert evidence[0]['error_type'] == 'DETAIL_URL_MISSING'
    assert evidence[0]['navigation_attempted'] is False


def test_return_detail_fallback_and_resume(tmp_path, monkeypatch):
    from action_tracker.products import parser
    calls = []
    def fetch(browser, url, sku, **kw):
        calls.append((url, sku))
        return {'name_es': 'Molde actualizado', 'current_price': 3.49}
    monkeypatch.setattr(parser, 'fetch_product_detail', fetch)
    base = fact()
    plan = dict(sku='3207872', canonical_id=base['canonical_id'], reason='REAPPEARED',
                need_detail=True, light=None, detail_url=base['product_url'])
    browser = SimpleNamespace(sleep=lambda: None)
    for _ in range(2):
        _, updated = fetch_and_merge(browser, [plan], {'3207872': base}, tmp_path)
        assert updated['3207872']['current_price'] == 3.49
        assert updated['3207872']['details_es'] == base['details_es']
    assert len(calls) == 1


def test_return_access_interruption_preserves_facts(tmp_path, monkeypatch):
    from action_tracker.products import parser
    monkeypatch.setattr(parser, 'fetch_product_detail', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('restricted')))
    base = fact()
    plan = dict(sku='3207872', canonical_id=base['canonical_id'], reason='REAPPEARED',
                need_detail=True, light=None, detail_url=base['product_url'])
    evidence = []
    _, updated = fetch_and_merge(SimpleNamespace(sleep=lambda: None), [plan], {'3207872': base}, tmp_path, detail_evidence=evidence)
    assert updated['3207872']['details_es'] == base['details_es']
    assert updated['3207872']['current_price'] is None
    assert evidence


def test_six_day_return_and_real_change(tmp_path):
    path = tmp_path/'replica.db'
    writer = ProductionWriter(path, role='PRIMARY')
    repo = ProductionRepository(path)
    sku = '3207872'
    original = fact()
    days = []
    for day in range(1, 8):
        date = f'2026-10-{day:02}'
        baseline = repo.load_current_products()
        historical = repo.load_product_baseline()
        known = repo.load_known_skus()
        present = day not in (2, 3, 4)
        light = {sku: {'current_price': 3.49, 'spec_es': '32 cm'}} if day >= 6 else {}
        statuses, today = run_sku_monitor([sku] if present else [], light, baseline, known)
        updated = {}
        if present:
            observed = original if day == 1 else light.get(sku, {})
            verified = {k: 'DETAIL_CURRENT_RUN' for k, v in observed.items() if v not in (None, '')}
            updated[sku] = retain_historical_facts(historical.get(sku, {}), observed,
                reappeared=statuses[sku].status == 'REAPPEARED', verified_fields=verified)
            updated[sku].update(sku=sku, canonical_id=original['canonical_id'])
        records = _build_current_records(historical, updated, statuses, today, date)
        transition = apply_state_transition(known, statuses, date, f'day{day}')
        changes = compute_changes(sku, original['canonical_id'], historical.get(sku), records[sku], date, .01, 999) if present else None
        events = _build_lifecycle_events(statuses, date, f'day{day}')
        if changes:
            events += changes.content_events
        bundle = build_daily_bundle(run_id=f'day{day}', observation_date=date, qa_state='PASS',
            today_records=records, baseline=historical, statuses=statuses, known=known,
            transition=transition, today_set=today, observation_complete=True,
            price_events=changes.price_events if changes else [], event_events=events,
            review_rows=[], run_record={})
        bundle = replace(bundle, requires_collection_integrity=False, base_commit_id=repo.current_head() or '')
        if day == 1:
            localizations = [dict(r) for r in bundle.localization_updates]
            for r in localizations:
                if r['language'] == 'zh':
                    r.update(review_status='APPROVED', approved_by='human:fixture', approved_at=date)
            bundle = replace(bundle, localization_updates=tuple(localizations))
        commit = writer.commit(bundle)
        with connect(path) as db:
            snapshot = {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')]
                        for t in ('products','localization_fields','price_history','event_history','commit_batches')}
        assert writer.commit(bundle) == commit
        with connect(path) as db:
            assert snapshot == {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in snapshot}
        rows = repo.load_product_baseline()
        assert rows[sku]['name_zh'] == original['name_zh']
        assert rows[sku]['first_seen'] == '2026-10-01'
        assert rows[sku]['details_es'] == original['details_es']
        if day == 5:
            assert statuses[sku].status == 'REAPPEARED'
            assert rows[sku]['current_price'] is None
        if day >= 6:
            assert rows[sku]['spec_es'] == '32 cm'
            assert rows[sku]['zh_field_provenance']['name']['freshness_status'] == 'CURRENT'
            assert rows[sku]['zh_field_provenance']['spec']['freshness_status'] == 'STALE'
        days.append(statuses[sku].status)
    assert days[:6] == ['NEW','MISSING_FIRST','MISSING_CONTINUED','OFFLINE','REAPPEARED','ACTIVE']
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='FIRST_SEEN'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM event_history WHERE event_type='REAPPEARED'").fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT MIN(new_price),MAX(new_price) FROM price_history').fetchone()[:] == (2.99,3.49)


def test_concurrent_head_blocks_without_partial_write(tmp_path):
    path = tmp_path/'replica.db'
    writer = ProductionWriter(path, role='PRIMARY')
    first = CommitBundle(run_id='first', observation_date='2026-10-01', qa_state='PASS', current_products=(fact(),))
    head = writer.commit(first)
    writer.commit(replace(first, run_id='second', base_commit_id=head))
    with pytest.raises(ProductionDatabaseError, match='BASELINE_CHANGED'):
        writer.commit(replace(first, run_id='stale', base_commit_id=head))
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM runs WHERE run_id='stale'").fetchone()[0] == 0


def test_current_universe_excludes_historical_fact_baseline(tmp_path):
    path = tmp_path/'replica.db'
    writer = ProductionWriter(path, role='PRIMARY')
    current, offline = fact('2568365'), fact()
    offline['status'] = 'OFFLINE'
    writer.commit(CommitBundle(run_id='seed', observation_date='2026-10-01', qa_state='PASS', current_products=(current, offline),
                              localization_updates=(_localization(current,'es'),_localization(offline,'es'))))
    repo = ProductionRepository(path)
    assert set(repo.load_current_products()) == {'2568365'}
    assert set(repo.load_product_baseline()) == {'2568365', '3207872'}
    assert repo.load_product_baseline()['3207872']['details_es'] == offline['details_es']


def test_original_price_does_not_prove_current_selling_price():
    from action_tracker.products.parser import _normalize_detail
    result = _normalize_detail({'sku':'3207872','current_price':'','original_price':'2,99 €'},fact()['product_url'])
    assert result['current_price'] is None
    assert result['original_price'] == 2.99


def test_missing_chinese_with_confirmed_brand_reaches_audit(tmp_path):
    from test_exporting import _cfg, _record, _write_dictionary, _write_csv
    from action_tracker.dictionary import BRAND_DICTIONARY_HEADERS
    from action_tracker.exporting.dictionary_join import load_dictionary_context,build_zh_rows_from_localized_source
    from action_tracker.exporting.repair_report import ExportRepairReport
    cfg=_cfg(tmp_path); record=_record()
    record.update(name_es='BrandX Producto',name_zh=None)
    _write_dictionary(cfg['paths']['dictionary_baseline'],record)
    _write_csv(cfg['paths']['dictionary_baseline']/'brand_dictionary.csv',BRAND_DICTIONARY_HEADERS,
               [{'brand_id':'BrandX','canonical_name':'BrandX','confidence':'REFERENCE'}])
    report=ExportRepairReport(run_id='fixture',rule_version='fixture')
    rows,_=build_zh_rows_from_localized_source([record],category_context=load_dictionary_context(cfg),repair_report=report)
    assert rows[0]['编号']==record['sku']
    assert not rows[0]['标题'] or rows[0]['标题']==record['name_es']


def test_retained_detail_does_not_create_unobserved_normalization_change(tmp_path):
    from action_tracker.database.production import _official_localization_value
    from action_tracker.services.normalization import normalize_official_text
    text='Material del asa: Silicona'
    historical={'details':text,'details_source':'HISTORY_RETAINED:BASELINE'}
    assert _official_localization_value(historical,'details','details')==text
    observed={'details':text,'details_source':'CURRENT_VERIFIED:DETAIL_CURRENT_RUN'}
    assert _official_localization_value(observed,'details','details')==normalize_official_text(text,field='details')


@pytest.mark.parametrize('field',['name_es','spec_es','cat1_es','product_url'])
def test_listing_content_change_is_incremental_plan(field):
    base=fact();value=base[field]+'changed'
    st=SimpleNamespace(status='ACTIVE',canonical_id=base['canonical_id'])
    plans=plan_updates({'3207872':st},{'3207872':base},{'3207872':{field:value}})
    assert len(plans)==1
    assert plans[0]['reason']=='CONTENT_CHANGE'


def test_registry_retained_source_matches_committed_projection(tmp_path):
    from action_tracker.operations.registry import committed_registry_records
    path=tmp_path/'replica.db'
    writer=ProductionWriter(path,role='PRIMARY')
    rec=fact();rec['details_es']='Material del asa: Silicona'
    rec['fact_field_provenance']={k:{'state':'HISTORY_RETAINED','source':'BASELINE'}
                                 for k in ('spec_es','desc_es','details_es')}
    es=_localization(rec,'es')
    frozen={'sku':rec['sku'],'run_id':'fixture','facts':{
        field:{'raw':None,'normalized':rec[key]} for field,key in
        {'name':'name_es','cat1':'cat1_es','cat2':'cat2_es','spec':'spec_es','description':'desc_es','details':'details_es'}.items()}}
    bundle=CommitBundle(run_id='fixture',observation_date='2026-10-01',qa_state='PASS',
        current_products=(rec,),localization_updates=(es,),source_fact_versions=(frozen,),
        run_record={'fact_field_provenance':{rec['sku']:rec['fact_field_provenance']}})
    commit=writer.commit(bundle)
    records=committed_registry_records({'storage':{'db_path':path},'project_root':tmp_path},
        source_run_id='fixture',fact_commit_id=commit,business_date='2026-10-01')
    assert records[0]['details_es']=='Material del asa: Silicona'


def test_verified_regular_price_clears_historical_promotion():
    base=fact();base['original_price']=4.99
    result=retain_historical_facts(base,{'current_price':2.99,'original_price':None},reappeared=True,
        verified_fields={'current_price':'DETAIL_CURRENT_RUN','original_price':'DETAIL_CURRENT_RUN'})
    assert result['current_price']==2.99
    assert result['original_price'] is None
    assert result['fact_field_provenance']['original_price']['state']=='CURRENT_VERIFIED'


def test_history_retention_tag_cannot_authorize_changed_text(tmp_path):
    path=tmp_path/'replica.db';writer=ProductionWriter(path,role='PRIMARY')
    rec=fact();es=_localization(rec,'es')
    first=CommitBundle(run_id='first',observation_date='2026-10-01',qa_state='PASS',
                       current_products=(rec,),localization_updates=(es,))
    head=writer.commit(first)
    changed={**es,'details':'Different official facts','details_source':'HISTORY_RETAINED:BASELINE'}
    with pytest.raises(ProductionDatabaseError,match='DB_HISTORY_RETAINED_VALUE_CHANGED'):
        writer.commit(replace(first,run_id='bad-history',base_commit_id=head,localization_updates=(changed,)))
    assert ProductionRepository(path).current_head()==head


def test_readiness_audit_does_not_invent_missing_registry_qa(tmp_path,monkeypatch):
    from scripts import audit_post_release_integrity as audit
    from action_tracker.database.integration import _localization
    monkeypatch.setattr(audit,'REPORT',tmp_path)
    path=tmp_path/'fixture.db';writer=ProductionWriter(path,role='PRIMARY');rec=fact()
    es=_localization(rec,'es');zh=_localization(rec,'zh')
    zh.update(name='模具',cat1='厨房',cat2='烘焙模具',spec='31厘米',description='硅胶',details='材质：硅胶',
        review_status='APPROVED',approved_by='human:fixture',approved_at='2026-10-01')
    writer.commit(CommitBundle(run_id='seed',observation_date='2026-10-01',qa_state='PASS',
        current_products=(rec,),localization_updates=(es,zh)))
    before=ProductionRepository(path).current_head()
    result=audit.readiness(path)
    assert result['fields']==6
    assert result['classifications']=={'REVIEW_REQUIRED':6}
    assert result['applied_fields']==result['qwen_calls']==0
    assert ProductionRepository(path).current_head()==before


def test_same_day_absence_cannot_reach_offline_threshold_twice():
    sku='3207872';date='2026-10-03'
    known={sku:{'canonical_id':'ACT3207872','last_status':'MISSING','missing_count':'2',
                'last_state_observation_date':date,'first_seen_date':'2026-10-01'}}
    statuses,today=run_sku_monitor([],{}, {},known,business_date=date)
    assert statuses[sku].status=='ABSENT'
    assert statuses[sku].missing_count==2
    assert statuses[sku].event is None
    transition=apply_state_transition(known,statuses,date,'repeat')
    assert transition['known'][sku]['last_status']=='MISSING'
    assert transition['known'][sku]['missing_count']=='2'
    stale_status=SimpleNamespace(status='OFFLINE',canonical_id='ACT3207872',missing_count=3)
    defensive=apply_state_transition(known,{sku:stale_status},date,'repeat-stale')
    assert defensive['known'][sku]['last_status']=='MISSING'
    assert not defensive['offline']
