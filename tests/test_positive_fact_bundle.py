"""Positive fact scope stays within the existing bundle/writer and freezes Presence."""
import copy
import json
from dataclasses import replace

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.integration import build_daily_bundle
from action_tracker.database.production import CommitBundle, ProductionWriter, ProductionDatabaseError
from action_tracker.database.repository import ProductionRepository
from action_tracker.data_quality.collection import evaluate_and_persist
from action_tracker.extraction.service import ExtractionService
from action_tracker.qa.validator import QAReport, trusted_field_scope


def _current(*, price=False):
    row = {'sku': '1001', 'canonical_id': 'ACT0001001',
           'product_url': 'https://www.action.com/es-es/p/1001/producto/',
           'presence_source': 'LISTING_ONLY', 'spec_es': '2 piezas', '_raw_spec_es': '2 piezas',
           'name_es': 'Unscoped replacement', 'cat1_es': 'Unscoped category',
           'current_price': 4.0, 'original_price': 9.0, 'unit_price': '9 €/ud',
           'name_zh': '不得覆盖', 'status': 'CURRENT', 'first_seen': '2026-10-30',
           'fact_field_provenance': {'spec_es': {'state': 'CURRENT_VERIFIED', 'source': 'DETAIL_CURRENT_RUN'}}}
    if price:
        row['fact_field_provenance']['current_price'] = {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'}
    return row


def _baseline(status='OFFLINE'):
    return {'sku': '1001', 'canonical_id': 'ACT0001001', 'name_es': 'Producto',
            'cat1_es': 'Hogar', 'spec_es': '1 pieza', 'desc_es': 'Texto anterior',
            'details_es': 'Material: Plástico', 'name_zh': '原中文', 'status': status,
            'product_url': 'https://www.action.com/es-es/p/1001/producto/',
            'current_price': 2.5, 'original_price': 3.0, 'unit_price': '2,50 €/ud',
            'last_valid_current_price': 2.5, 'last_valid_original_price': 3.0,
            'last_valid_unit_price': '2,50 €/ud', 'first_seen': '2026-10-01',
            'last_seen': '2026-10-25', 'last_checked_at': '2026-10-25T12:00:00Z',
            'consecutive_missing': 3, 'baseline_head_commit_id': 'full-head'}


def _build(row, baseline, *, report=None, scope=None, reviews=None):
    qa = QAReport(False, 'FAIL', issues=[{'level': 'HARD_BLOCK', 'sku': '',
                                        'field_name': 'presence', 'reason': 'INCOMPLETE'}])
    scope = scope or trusted_field_scope([row], qa, run_id='partial', business_date='2026-10-30')
    return build_daily_bundle(run_id='partial', observation_date='2026-10-30', qa_state='FAIL',
        today_records={'1001': row}, baseline=baseline,
        statuses={'unobserved': object()}, known={'unobserved': {'sku': 'unobserved'}},
        transition={'known': {'1001': {'current_status': 'CURRENT'}}}, today_set={'1001'},
        observation_complete=False, price_events=[{'sku': '1001', 'new_price': 4.0}],
        event_events=[{'sku': '1001', 'event_type': 'REAPPEARED'}], review_rows=reviews or [],
        run_record={'qa_issues': qa.issues, 'trusted_field_scope': scope,
                    'commit_scope': 'POSITIVE_FACTS_ONLY',
                    'collection_quality_state': 'COLLECTION_BLOCKED', **(report or {})})


@pytest.mark.parametrize('status', ['CURRENT', 'MISSING', 'OFFLINE'])
def test_partial_bundle_freezes_old_presence_and_unscoped_fields(status):
    old = _baseline(status); before = copy.deepcopy(old)
    bundle = _build(_current(), {'1001': old})
    record = bundle.current_products[0]
    assert len(bundle.current_products) == 1
    for field in ('status', 'first_seen', 'last_seen', 'consecutive_missing', 'last_checked_at',
                  'name_es', 'cat1_es', 'desc_es', 'details_es', 'name_zh'):
        assert record[field] == old[field]
    assert record['spec_es'] == '2 piezas'
    assert old == before
    assert bundle.qa_state == 'FAIL'
    assert not bundle.lifecycle_updates and not bundle.event_events and not bundle.price_events
    assert [row['language'] for row in bundle.localization_updates] == ['es']
    assert bundle.source_fact_versions[0]['facts'] == {'spec': {'raw': '2 piezas', 'normalized': '2 piezas'}}
    assert bundle.run_record['commit_scope'] == 'POSITIVE_FACTS_ONLY'
    assert bundle.run_record['price_observation_invalid'] == {'1001': True}
    assert bundle.observations[0]['current_price'] is None
    assert bundle.observations[0]['presence_state'] == 'PRESENT'
    assert bundle.observations[0]['observation_complete'] is False
    assert bundle.observations[0]['absence_capable'] is False
    assert next(r for r in bundle.run_record['field_commit_results'] if r['field_name'] == 'current_price')['attempted_hash'] != next(r for r in bundle.run_record['field_commit_results'] if r['field_name'] == 'current_price')['committed_hash']


def test_new_positive_identity_stays_unknown_without_lifecycle():
    bundle = _build(_current(), {})
    row = bundle.current_products[0]
    assert row['status'] == 'UNKNOWN'
    assert row['first_seen_at'] is None and row['last_seen_at'] is None
    assert row['name_es'] is None and row['current_price'] is None
    assert row['spec_es'] == '2 piezas'
    assert not bundle.lifecycle_updates


def test_facts_only_identity_does_not_consume_first_valid_presence_event():
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    unknown = _build(_current(), {}).current_products[0]
    statuses, _ = run_sku_monitor(['1001'], {}, {}, {},
        historical_identities={'1001': unknown}, business_date='2026-10-30')
    assert statuses['1001'].status == 'NEW'
    assert statuses['1001'].event == 'FIRST_SEEN'
    assert statuses['1001'].ever_seen is False


def test_dated_unknown_historical_identity_keeps_its_presence_history():
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    historical = {'sku': '1001', 'status': 'UNKNOWN', 'first_seen': '2026-01-01'}
    statuses, _ = run_sku_monitor(['1001'], {}, {}, {},
        historical_identities={'1001': historical}, business_date='2026-10-30')
    assert statuses['1001'].status == 'ACTIVE'
    assert statuses['1001'].ever_seen is True
    assert statuses['1001'].first_seen == '2026-01-01'


def test_legacy_undated_unknown_is_not_mistaken_for_new_scope_identity():
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    statuses, _ = run_sku_monitor(['1001'], {}, {}, {},
        historical_identities={'1001': {'sku': '1001', 'status': 'UNKNOWN'}})
    assert statuses['1001'].status == 'ACTIVE'


def test_candidate_field_scope_does_not_change_full_bundle_route():
    bundle = _build(_current(), {'1001': _baseline()}, report={'commit_scope': 'FULL_OBSERVATION'})
    assert bundle.run_record['commit_scope'] == 'FULL_OBSERVATION'
    assert bundle.event_events and bundle.lifecycle_updates
    assert next(row for row in bundle.current_products if row['sku'] == '1001')['name_es'] == 'Unscoped replacement'


@pytest.mark.parametrize('tamper', ['hash', 'identity', 'source'])
def test_scope_source_mismatch_refused_before_build(tamper):
    row = _current(); qa = QAReport(False, 'FAIL')
    scope = trusted_field_scope([row], qa, run_id='partial', business_date='2026-10-30')
    if tamper == 'hash':
        row['spec_es'] = 'forged'
    elif tamper == 'identity':
        row['canonical_id'] = 'ACT0009999'
    else:
        row['fact_field_provenance']['spec_es']['source'] = 'BASELINE'
    with pytest.raises(ProductionDatabaseError):
        _build(row, {'1001': _baseline()}, scope=scope)


def _seed(path):
    writer = ProductionWriter(path, role='SHADOW')
    old = _baseline('CURRENT')
    full = writer.commit(CommitBundle(run_id='full', observation_date='2026-10-25', qa_state='PASS',
        current_products=(old,), localization_updates=({'sku': '1001', 'language': 'es',
            'name': old['name_es'], 'cat1': old['cat1_es'], 'spec': old['spec_es'],
            'description': old['desc_es'], 'details': old['details_es']},),
        observations=({'sku': '1001', 'run_id': 'full', 'observation_date': '2026-10-25',
                       'presence_state': 'PRESENT', 'observation_complete': True,
                       'absence_capable': True, 'current_price': 2.5},)))
    return writer, full


def _persist_scope(path, row):
    qa = QAReport(False, 'FAIL', issues=[{'level': 'HARD_BLOCK', 'sku': '',
                                       'field_name': 'presence', 'reason': 'INCOMPLETE'}])
    scope = trusted_field_scope([row], qa, run_id='partial', business_date='2026-10-30')
    quality = evaluate_and_persist(path, 'partial', {'run_date': '2026-10-30',
        'qa_state': 'FAIL', 'qa_issues': qa.issues, 'trusted_field_scope': scope,
        'sitemap_unique': 1, 'listing_unique': 1, 'current_valid': 1,
        'category_coverage': {'cat': False}})
    return scope, quality


def test_real_partial_write_preserves_full_head_and_masks_unobserved_price_fields(tmp_path):
    path = tmp_path / 'positive.db'; writer, full_head = _seed(path)
    old = ProductionRepository(path).load_product_baseline()
    row = _current(price=True); scope, quality = _persist_scope(path, row)
    bundle = _build(row, old, scope=scope, report={'collection_quality_state': quality.state,
        'collection_metrics_hash': quality.metrics_hash})
    assert bundle.current_products[0]['current_price'] == 4.0
    assert bundle.run_record['price_field_observation_invalid'] == {'1001': {'original_price': True, 'unit_price': True}}
    commit = writer.commit(bundle)
    assert writer.commit(bundle) == commit
    result = ProductionRepository(path).load_product_baseline()['1001']
    assert result['current_price'] == 4.0
    assert result['original_price'] is None and result['unit_price'] is None
    assert result['last_valid_original_price'] == 3.0 and result['last_valid_unit_price'] == '2,50 €/ud'
    assert result['presence_source_commit_id'] == full_head
    assert result['presence_observation_date'] == '2026-10-25'
    assert result['baseline_head_commit_id'] == commit
    assert ProductionRepository(path).latest_observation_commit_info()['commit_id'] == full_head
    assert ExtractionService(path).execute().items[0]['original_price'] is None
    with connect(path) as db:
        assert db.execute("SELECT status,first_seen_at,last_seen_at,consecutive_missing FROM products WHERE official_sku='1001'").fetchone()[:] == ('CURRENT', '2026-10-01', '2026-10-25', 3)
        assert db.execute("SELECT COUNT(*) FROM price_history WHERE run_id='partial'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM lifecycle_state").fetchone()[0] == 0
        assert db.execute("SELECT qa_state FROM runs WHERE run_id='partial'").fetchone()[0] == 'FAIL'


def test_partial_without_any_complete_presence_does_not_invent_head(tmp_path):
    path = tmp_path / 'no-full.db'; writer = ProductionWriter(path, role='SHADOW')
    row = _current(); scope, quality = _persist_scope(path, row)
    bundle = _build(row, {}, scope=scope, report={'collection_quality_state': quality.state,
                                               'collection_metrics_hash': quality.metrics_hash})
    writer.commit(bundle)
    assert ProductionRepository(path).latest_observation_commit_info() is None
    result = ProductionRepository(path).load_product_baseline()['1001']
    assert result['status'] == 'UNKNOWN' and result['presence_source'] == ''
    assert result['presence_source_commit_id'] is None
    assert result['_facts_only_without_presence'] is True
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    statuses, _ = run_sku_monitor(['1001'], {}, {}, {}, historical_identities={'1001': result})
    assert statuses['1001'].status == 'NEW' and statuses['1001'].event == 'FIRST_SEEN'


def test_scoped_original_review_notes_survive_partial_write(tmp_path):
    path = tmp_path / 'notes.db'; writer, _ = _seed(path)
    row = _current(); scope, quality = _persist_scope(path, row)
    reviews = [{'SKU': '1001', '问题类型': 'CATEGORY_CONFLICT', '证据': 'Unscoped category conflict',
                '人工备注': '来源冲突继续保留；本次只接受规格'},
               {'SKU': '2002', '问题类型': 'CATEGORY_CONFLICT', '证据': 'Outside identity scope'}]
    before = copy.deepcopy(reviews)
    bundle = _build(row, ProductionRepository(path).load_product_baseline(),
        scope=scope, reviews=reviews, report={'collection_quality_state': quality.state,
                                            'collection_metrics_hash': quality.metrics_hash})
    originals = [r for r in bundle.review_rows if r.get('问题类型') == 'CATEGORY_CONFLICT']
    assert len(originals) == 1 and originals[0]['sku'] == '1001'
    assert originals[0]['人工备注'] == reviews[0]['人工备注']
    assert reviews == before
    writer.commit(bundle)
    with connect(path) as db:
        saved = db.execute("SELECT entity_id,evidence FROM reviews WHERE issue_type='CATEGORY_CONFLICT'").fetchall()
        assert [tuple(r) for r in saved] == [('1001', 'Unscoped category conflict')]
        payloads = [json.loads(r[0]) for r in db.execute("SELECT action FROM sync_queue WHERE entity_type='review'")]
    assert any(p.get('人工备注') == reviews[0]['人工备注'] and p.get('sku') == '1001' for p in payloads)
    assert not any(p.get('SKU') == '2002' for p in payloads)


def test_first_valid_presence_records_one_event_and_resume_does_not_repeat(tmp_path):
    from action_tracker.monitor.sku_monitor import run_sku_monitor
    from action_tracker.state import apply_state_transition
    from action_tracker.services.change import compute_changes
    from action_tracker.orchestrator.daily import _build_current_records, _build_lifecycle_events

    path = tmp_path / 'first-event.db'
    writer = ProductionWriter(path, role='SHADOW')
    row = _current()
    scope, quality = _persist_scope(path, row)
    partial = _build(row, {}, scope=scope, report={
        'collection_quality_state': quality.state, 'collection_metrics_hash': quality.metrics_hash})
    writer.commit(partial)
    repo = ProductionRepository(path)
    historical = repo.load_product_baseline()
    statuses, today = run_sku_monitor(['1001'], {}, {}, {}, historical_identities=historical,
                                    business_date='2026-10-31')
    observed = {**historical['1001'], 'current_price': 3.5, 'status': 'CURRENT',
                'fact_field_provenance': {'current_price': {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'}}}
    records = _build_current_records(historical, {'1001': observed}, statuses, today, '2026-10-31')
    changes = compute_changes('1001', row['canonical_id'], historical['1001'], records['1001'],
        '2026-10-31', .01, 1000, 'first-valid', first_presence=statuses['1001'].event == 'FIRST_SEEN')
    assert [e['事件类型'] for e in changes.content_events] == ['FIRST_SEEN']
    transition = apply_state_transition({}, statuses, '2026-10-31', 'first-valid')
    bundle = build_daily_bundle(run_id='first-valid', observation_date='2026-10-31', qa_state='PASS',
        today_records=records, baseline=historical, statuses=statuses, known={}, transition=transition,
        today_set=today, observation_complete=True, price_events=changes.price_events,
        event_events=changes.content_events + _build_lifecycle_events(statuses, '2026-10-31', 'first-valid'),
        review_rows=[], run_record={})
    bundle = replace(bundle, requires_collection_integrity=False, base_commit_id=repo.current_head())
    committed = writer.commit(bundle)
    assert writer.commit(bundle) == committed
    with connect(path) as db:
        events = [r[0] for r in db.execute("SELECT event_type FROM event_history WHERE run_id='first-valid'")]
    assert events.count('FIRST_SEEN') == 1
    assert 'REAPPEARED' not in events and 'CONTENT_CHANGE' not in events
    saved = repo.load_product_baseline()['1001']
    assert saved['first_seen'] == '2026-10-31'
    assert saved['_facts_only_without_presence'] is False
    later, _ = run_sku_monitor(['1001'], {}, {'1001': saved}, repo.load_known_skus(),
        historical_identities={'1001': saved}, business_date='2026-11-01')
    assert later['1001'].status == 'ACTIVE' and later['1001'].event is None


@pytest.mark.parametrize('has_prior,claimed', [
    (False, []), (False, ['1001', '1001']), (False, [1001]),
    (True, ['1001']), (True, ['other']),
])
def test_writer_rejects_forged_new_identity_origin_without_business_write(tmp_path, has_prior, claimed):
    path = tmp_path / 'origin.db'
    writer = _seed(path)[0] if has_prior else ProductionWriter(path, role='SHADOW')
    row = _current()
    scope, quality = _persist_scope(path, row)
    baseline = ProductionRepository(path).load_product_baseline()
    bundle = _build(row, baseline, scope=scope, report={
        'collection_quality_state': quality.state,
        'collection_metrics_hash': quality.metrics_hash})
    forged = replace(bundle, run_record={**bundle.run_record, 'new_identity_skus': claimed})
    with connect(path) as db:
        before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                  for table in ('products', 'commit_batches', 'observations', 'runs')}
    with pytest.raises(ProductionDatabaseError, match='POSITIVE_FACT_IDENTITY_ORIGIN_MISMATCH'):
        writer.commit(forged)
    with connect(path) as db:
        after = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                 for table in before}
    assert after == before


def test_resume_cannot_reuse_claimed_hash_for_changed_scope_payload(tmp_path):
    path = tmp_path / 'resume-hash.db'
    writer = ProductionWriter(path, role='SHADOW')
    row = _current()
    scope, quality = _persist_scope(path, row)
    bundle = _build(row, {}, scope=scope, report={
        'collection_quality_state': quality.state, 'collection_metrics_hash': quality.metrics_hash})
    committed = writer.commit(bundle)
    assert writer.commit(replace(bundle, bundle_hash=bundle.resolved_hash())) == committed
    forged = replace(bundle, bundle_hash=bundle.resolved_hash(),
                     run_record={**bundle.run_record, 'new_identity_skus': []})
    with pytest.raises(ProductionDatabaseError, match='DB_COMMIT_BUNDLE_HASH_MISMATCH'):
        writer.commit(forged)
    assert ProductionRepository(path).current_head() == committed
    assert ProductionRepository(path).load_product_baseline()['1001']['_facts_only_without_presence'] is True
