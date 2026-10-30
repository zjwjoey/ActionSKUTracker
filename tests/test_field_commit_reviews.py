"""Field reviews exercise the real isolated writer and shared read projections."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from action_tracker.data_quality.collection import evaluate_and_persist
from action_tracker.database.connection import connect
from action_tracker.database.integration import build_daily_bundle, _select_trusted_fields
from action_tracker.database.production import CommitBundle, ProductionDatabaseError, ProductionWriter
from action_tracker.database.repository import ProductionRepository
from action_tracker.extraction.service import ExtractionService
from action_tracker.localization.hashes import value_hash


def _seed(path):
    writer = ProductionWriter(path, role='SHADOW')
    writer.commit(CommitBundle(run_id='seed', observation_date='2026-10-28', qa_state='PASS',
        current_products=({'sku': '1001', 'canonical_id': 'ACT0001001', 'name_es': 'Producto',
                           'status': 'CURRENT', 'current_price': 2.5, 'original_price': 3.0,
                           'unit_price': '2,50 €/ud'},)))
    return writer


def _build(path, *, price=9999.0, reviewed=True, retained=False, run='review', date='2026-10-29'):
    baseline = {r['sku']: r for r in ProductionRepository(path).load_current_export_records()}
    row = {'sku': '1001', 'canonical_id': 'ACT0001001', 'name_es': 'Nombre nuevo',
           'status': 'CURRENT', 'current_price': price, 'original_price': 9.0,
           'unit_price': '9 €/ud', 'cat1_es': 'Hogar'}
    if retained:
        row['fact_field_provenance'] = {'current_price': {'state': 'HISTORY_RETAINED'}}
    issues = ([{'level': 'FIELD_REVIEW', 'sku': '1001', 'field_name': 'current_price',
                'reason': 'PRICE_ANOMALY', 'source_value': price, 'source_hash': value_hash(price)}]
              if reviewed else [])
    quality = evaluate_and_persist(path, run, {
        'run_date': date, 'qa_state': 'PASS', 'qa_issues': issues,
        'sitemap_unique': 1, 'listing_unique': 1, 'current_valid': 1,
        'price_coverage': 1.0, 'cat2_coverage': 1.0, 'description_coverage': 1.0,
        'detail_failure_rate': 0.0, 'category_coverage': {f'cat-{i}': True for i in range(15)}})
    status = SimpleNamespace(sku='1001', canonical_id='ACT0001001', status='ACTIVE',
        observation_valid=True, sitemap_present=True, listing_present=True)
    return build_daily_bundle(run_id=run, observation_date=date, qa_state='PASS',
        today_records={'1001': row}, baseline=baseline, statuses={'1001': status},
        known={}, transition={}, today_set={'1001'}, observation_complete=True,
        price_events=[{'sku': '1001', 'old_price': 2.5, 'new_price': price, 'change_type': 'UP'}],
        event_events=[], review_rows=[], run_record={'qa_issues': issues,
            'collection_quality_state': quality.state, 'collection_metrics_hash': quality.metrics_hash})


def test_bad_price_keeps_good_fields_and_last_valid_price_without_false_current(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path)
    assert bundle.current_products[0]['current_price'] == 2.5
    assert bundle.current_products[0]['name_es'] == 'Nombre nuevo'
    assert bundle.observations[0]['current_price'] is None
    assert not bundle.price_events
    assert len(bundle.review_rows) == 3
    commit = writer.commit(bundle)
    assert writer.commit(bundle) == commit
    record = ProductionRepository(path).load_current_export_records()[0]
    assert record['current_price'] is None
    assert record['original_price'] is None
    assert record['last_valid_current_price'] == 2.5
    assert record['price_review_pending'] is True
    assert record['unit_price_zh'] is None
    assert record['zh_unit_price_source'] == 'PRICE_REVIEW_REQUIRED'
    assert record['price_min'] == record['price_max'] == 2.5
    service = ExtractionService(path)
    assert service.execute().items[0]['current_price'] is None
    assert service.execute().items[0]['historical_low'] == record['price_min']
    assert service.execute().items[0]['historical_high'] == record['price_max']
    assert service.execute({'min_price': 1}).matched_count == 0
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM commit_batches WHERE run_id='review'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM price_history WHERE run_id='review'").fetchone()[0] == 0


def test_later_non_observation_patch_cannot_refresh_old_price(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    writer.commit(_build(path))
    writer.commit(CommitBundle(run_id='metadata-only', observation_date='2026-10-30', qa_state='PASS'))
    assert ProductionRepository(path).load_current_export_records()[0]['current_price'] is None
    writer.commit(_build(path, price=4.0, reviewed=False, run='verified', date='2026-10-30'))
    assert ProductionRepository(path).load_current_export_records()[0]['current_price'] == 4.0
    assert ExtractionService(path).execute({'min_price': 3}).matched_count == 1


def test_historical_price_provenance_never_becomes_current_observation(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path, price=2.5, reviewed=False, retained=True)
    writer.commit(bundle)
    assert bundle.observations[0]['current_price'] is None
    assert ProductionRepository(path).load_current_export_records()[0]['current_price'] is None


@pytest.mark.parametrize('tamper', ['receipt', 'observation', 'event', 'issues', 'price_marker', 'source_hash'])
def test_tampered_field_evidence_is_atomically_refused(tmp_path, tamper):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path)
    if tamper == 'receipt':
        report = {**bundle.run_record, 'field_commit_results': []}
        bundle = replace(bundle, run_record=report)
    elif tamper == 'observation':
        bundle = replace(bundle, observations=({**bundle.observations[0], 'current_price': 9999.0},))
    elif tamper == 'event':
        bundle = replace(bundle, price_events=({'sku': '1001', 'new_price': 9999.0},))
    elif tamper == 'issues':
        bundle = replace(bundle, run_record={**bundle.run_record, 'qa_issues': []})
    elif tamper == 'price_marker':
        bundle = replace(bundle, run_record={**bundle.run_record, 'price_observation_invalid': {}})
    else:
        receipts = [{**r, 'attempted_hash': value_hash('forged')} for r in bundle.run_record['field_commit_results']]
        bundle = replace(bundle, run_record={**bundle.run_record, 'field_commit_results': receipts})
    with pytest.raises(ProductionDatabaseError):
        writer.commit(bundle)
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM commit_batches WHERE run_id='review'").fetchone()[0] == 0
        assert db.execute("SELECT current_price FROM products WHERE official_sku='1001'").fetchone()[0] == 2.5


def test_missing_price_new_identity_is_preserved_without_inventing_history(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = ProductionWriter(path, role='SHADOW')
    bundle = _build(path, price=None)
    writer.commit(bundle)
    record = ProductionRepository(path).load_current_export_records()[0]
    assert record['sku'] == '1001'
    assert record['name_es'] == 'Nombre nuevo'
    assert record['current_price'] is None
    assert record['last_valid_current_price'] is None
    assert all(r['action'] == 'REVIEW_MISSING' for r in bundle.run_record['field_commit_results'])


def test_field_review_does_not_change_other_sku_or_mutate_source():
    records = {'1001': {'sku': '1001', 'cat1_es': '', 'name_es': 'Nuevo'},
               '1002': {'sku': '1002', 'cat1_es': 'Cocina', 'current_price': 4.5}}
    issue = {'level': 'FIELD_REVIEW', 'sku': '1001', 'field_name': 'cat1_es',
             'reason': 'CATEGORY_SOURCE_MISSING', 'source_hash': value_hash('')}
    selected, receipts, invalid = _select_trusted_fields(records,
        {'1001': {'cat1_es': 'Hogar'}}, [issue], run_id='test', observation_date='2026-10-29')
    assert selected['1001']['name_es'] == 'Nuevo'
    assert selected['1001']['cat1_es'] == 'Hogar'
    assert selected['1002']['current_price'] == 4.5
    assert records['1001']['cat1_es'] == ''
    assert len(receipts) == 1 and not invalid
    issue['source_hash'] = value_hash('different version')
    with pytest.raises(ProductionDatabaseError, match='FIELD_REVIEW_SOURCE_HASH_MISMATCH'):
        _select_trusted_fields(records, {}, [issue], run_id='test', observation_date='2026-10-29')


def test_deleting_both_price_receipts_and_marker_cannot_refresh_history(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path, price=2.5, reviewed=False, retained=True)
    report = {**bundle.run_record, 'field_commit_results': [], 'price_observation_invalid': {}}
    with pytest.raises(ProductionDatabaseError, match='PRICE_REVIEW_EVIDENCE_MISSING'):
        writer.commit(replace(bundle, run_record=report))


def test_forged_last_valid_spanish_field_is_not_authorized_by_self_hash(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path, price=4.0, reviewed=False)
    row = {**bundle.current_products[0], 'cat1_es': 'Inventado'}
    receipt = {'sku': '1001', 'field_name': 'cat1_es', 'action': 'RETAIN_LAST_VALID',
               'run_id': bundle.run_id, 'observation_date': bundle.observation_date,
               'committed_hash': value_hash('Inventado')}
    report = {**bundle.run_record, 'field_commit_results': [receipt]}
    with pytest.raises(ProductionDatabaseError, match='LAST_VALID_FIELD_RETENTION_MISMATCH'):
        writer.commit(replace(bundle, current_products=(row,), run_record=report))


def test_deleting_all_retention_metadata_still_cannot_refresh_history(tmp_path):
    path = tmp_path / 'isolated.db'
    writer = _seed(path)
    bundle = _build(path, price=2.5, reviewed=False, retained=True)
    report = {**bundle.run_record, 'field_commit_results': [],
              'price_observation_invalid': {}, 'fact_field_provenance': {}}
    products = tuple({k: v for k, v in row.items() if k != 'fact_field_provenance'}
                     for row in bundle.current_products)
    with pytest.raises(ProductionDatabaseError, match='PRICE_REVIEW_EVIDENCE_MISSING'):
        writer.commit(replace(bundle, current_products=products, run_record=report))
    with pytest.raises(ProductionDatabaseError, match='CURRENT_PRODUCT_OBSERVATION_MISSING'):
        writer.commit(replace(bundle, current_products=products, observations=(), run_record=report))
