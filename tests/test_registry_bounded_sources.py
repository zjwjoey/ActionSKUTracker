"""CI_SAFE: scoped Registry reads on generated databases; no data/price scans."""
import json
import sqlite3
from contextlib import contextmanager

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.repository import ProductionRepository, ProductionRepositoryError
from action_tracker.operations.registry import committed_registry_records
from test_historical_fact_import import _fixture, _build
from test_positive_fact_consumers import _partial

FIELDS = ('name_es', 'cat1_es', 'cat2_es', 'spec_es', 'desc_es', 'details_es')


@contextmanager
def trace_reads(monkeypatch):
    statements = []
    original = sqlite3.connect
    def traced(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, 'connect', traced)
        yield statements


def assert_source_only(statements, sku):
    sql = '\n'.join(statements).lower()
    assert not any(name in sql for name in ('price_history', 'observations',
        'localization_fields', 'localization_field_provenance', 'price_review', 'presence_state'))
    assert "language='zh'" not in sql and 'language = \'zh\'' not in sql
    product_reads = [s for s in statements if 'from products p' in s.lower()]
    assert len(product_reads) == 1
    assert 'json_each' in product_reads[0] and sku in product_reads[0]


def history(tmp_path):
    fixture = _fixture(tmp_path)
    bundle, _ = _build(fixture, fixture[4][1:2])
    head = fixture[1].commit(bundle)
    return fixture, head


def test_narrow_source_reader_preserves_catalog_field_authority_and_name_fallbacks(tmp_path):
    path, _, _, _, _, _ = _fixture(tmp_path)
    with connect(path) as db:
        for sku, name in [('100', None), ('101', ''), ('102', '   '), ('103', '0'), ('104', 'Fuente')]:
            db.execute("UPDATE product_localizations SET name=?,cat1='',cat2=NULL,spec='2 piezas' WHERE official_sku=? AND language='es'", (name, sku))
        db.execute("DELETE FROM product_localizations WHERE official_sku='105' AND language='es'")
    repo = ProductionRepository(path)
    full = {r['sku']: r for r in repo.load_current_export_records(include_non_current=True)}
    selected = repo.load_localization_source_records(['105', '103', '100', '104', '101', '102', '103'], include_non_current=True)
    assert [r['sku'] for r in selected] == ['100', '101', '102', '103', '104', '105']
    for row in selected:
        assert {k: row[k] for k in (*FIELDS, 'sku', 'canonical_id', 'product_url', 'image_url', 'status')} == {
            k: full[row['sku']][k] for k in (*FIELDS, 'sku', 'canonical_id', 'product_url', 'image_url', 'status')}
    assert selected[0]['name_es'] == selected[1]['name_es'] == selected[5]['name_es'] == 'Original'
    assert selected[2]['name_es'] == '   ' and selected[3]['name_es'] == '0'
    assert [r['sku'] for r in repo.load_localization_source_records(['100', '101'])] == ['100']
    assert not any(k in selected[0] for k in ('current_price', 'name_zh', 'zh_field_provenance'))


def test_empty_scope_never_loads_all_products_and_invalid_identifiers_fail_closed(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    repo = ProductionRepository(fixture[0])
    monkeypatch.setattr(sqlite3, 'connect', lambda *_a, **_kw: pytest.fail('empty/invalid scope opened SQLite'))
    assert repo.load_localization_source_records([]) == []
    for invalid in (None, '100', b'100', [None], [''], [' 100'], [100]):
        with pytest.raises(ProductionRepositoryError, match='SKUS_INVALID'):
            repo.load_localization_source_records(invalid)


def test_one_historical_field_uses_bounded_source_read_and_actual_registry_resume(tmp_path, monkeypatch):
    fixture, head = history(tmp_path)
    path, writer, _, _, candidates, cfg = fixture
    with trace_reads(monkeypatch) as statements:
        rows = committed_registry_records(cfg, source_run_id='history', fact_commit_id=head, business_date='2026-10-30')
    assert_source_only(statements, '101')
    assert len(rows) == 1 and rows[0]['sku'] == '101'
    assert rows[0]['_registry_allowed_fields'] == ['spec_es'] and rows[0]['_registry_observed_at'] == 'UNKNOWN'
    from action_tracker.localization.registry.repository import LocalizationRegistry
    registry = LocalizationRegistry(path, role='PRIMARY')
    try:
        registry.ingest_records(rows, source_run_id='history', observed_at='2026-10-30')
        with connect(path) as db:
            before = [db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
                ('translation_source_versions', 'translation_units', 'translation_queue')]
            assert [r[0] for r in db.execute('SELECT requested_fields FROM translation_queue')] == ['spec']
        again = committed_registry_records(cfg, source_run_id='history', fact_commit_id=head, business_date='2026-10-30')
        assert again == rows
        registry.ingest_records(again, source_run_id='history', observed_at='2026-10-30')
        with connect(path) as db:
            assert [db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
                ('translation_source_versions', 'translation_units', 'translation_queue')] == before
    finally:
        registry.close()
    empty, report = _build(fixture, candidates[1:2], run_id='history-empty', base_commit_id=head)
    assert report['filled_fields'] == 0
    empty_head = writer.commit(empty)
    with trace_reads(monkeypatch) as statements:
        assert committed_registry_records(cfg, source_run_id='history-empty', fact_commit_id=empty_head, business_date='2026-10-30') == []
    assert not any('from products' in s.lower() for s in statements)


def test_historical_value_hash_identity_and_head_guards_are_not_bypassed(tmp_path, monkeypatch):
    fixture, head = history(tmp_path)
    path, _, _, _, _, cfg = fixture
    kwargs = dict(source_run_id='history', fact_commit_id=head, business_date='2026-10-30')
    with pytest.raises(ValueError, match='COMMIT_NOT_VERIFIED'):
        committed_registry_records(cfg, **{**kwargs, 'business_date': '2026-10-29'})
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET spec='3 piezas' WHERE official_sku='101' AND language='es'")
    with pytest.raises(ValueError, match='HISTORICAL_SOURCE_CHANGED'):
        committed_registry_records(cfg, **kwargs)
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET spec='2 unidades' WHERE official_sku='101' AND language='es'")
        original = db.execute("SELECT evidence_json FROM run_evidence WHERE run_id='history'").fetchone()[0]
        evidence = json.loads(original)
        evidence['historical_committed_fields'][0]['value_hash'] = '0'*64
        db.execute("UPDATE run_evidence SET evidence_json=? WHERE run_id='history'", (json.dumps(evidence),))
    with pytest.raises(ValueError, match='HISTORICAL_SOURCE_CHANGED'):
        committed_registry_records(cfg, **kwargs)
    with connect(path) as db:
        db.execute("UPDATE run_evidence SET evidence_json=? WHERE run_id='history'", (original,))
    from action_tracker.operations import registry as recovery
    recovery.record_registry_status(cfg, **kwargs, result={'status': 'FAILED'})
    heads = iter(('before', 'after'))
    monkeypatch.setattr(recovery, 'latest_commit_id', lambda _path: next(heads))
    result = recovery._retry_registry_unlocked(cfg, **kwargs)
    assert result['status'] == 'FAILED' and 'BASELINE_CHANGED_BEFORE_REGISTRY_RETRY' in result['error']
    with connect(path) as db:
        assert db.execute('SELECT count(*) FROM translation_source_versions').fetchone()[0] == 0


def test_partial_field_uses_bounded_reader_and_preserves_frozen_hash_checks(tmp_path, monkeypatch):
    cfg, _, diagnostics, _, _ = _partial(tmp_path, monkeypatch)
    path = cfg['storage']['db_path']
    kwargs = dict(source_run_id='partial', fact_commit_id=diagnostics['commit_id'], business_date='2026-10-30')
    with trace_reads(monkeypatch) as statements:
        rows = committed_registry_records(cfg, **kwargs)
    assert_source_only(statements, '1001')
    assert rows[0]['_registry_allowed_fields'] == ['spec_es']
    assert committed_registry_records(cfg, **kwargs) == rows
    with connect(path) as db:
        db.execute("UPDATE product_localizations SET spec='3 piezas' WHERE official_sku='1001' AND language='es'")
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        committed_registry_records(cfg, **kwargs)


def test_partial_canonical_facts_query_is_sku_bound_and_rejects_its_changed_frozen_value(tmp_path, monkeypatch):
    cfg, _, diagnostics, _, _ = _partial(tmp_path, monkeypatch)
    path = cfg['storage']['db_path']
    canonical = {'name': 'Producto', 'cat1': 'Hogar', 'spec': '2 piezas'}
    with connect(path) as db:
        db.execute('CREATE TABLE product_fact_versions(version_id TEXT PRIMARY KEY,official_sku TEXT,run_id TEXT,normalized_fact_json TEXT)')
        db.execute('CREATE INDEX generated_pfv_sku ON product_fact_versions(official_sku)')
        db.executemany('INSERT INTO product_fact_versions VALUES(?,?,?,?)', [
            ('target', '1001', 'partial', json.dumps(canonical)),
            ('unrelated', '9999', 'partial', 'invalid unrelated JSON must not be fetched')])
    kwargs = dict(source_run_id='partial', fact_commit_id=diagnostics['commit_id'], business_date='2026-10-30')
    with trace_reads(monkeypatch) as statements:
        rows = committed_registry_records(cfg, **kwargs)
    assert_source_only(statements, '1001')
    frozen_reads = [s for s in statements if 'from product_fact_versions' in s.lower()]
    assert len(frozen_reads) == 1 and 'json_each' in frozen_reads[0]
    assert rows[0]['spec_es'] == '2 piezas'
    canonical['spec'] = '3 piezas'
    with connect(path) as db:
        db.execute("UPDATE product_fact_versions SET normalized_fact_json=? WHERE official_sku='1001'", (json.dumps(canonical),))
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        committed_registry_records(cfg, **kwargs)


def test_partial_non_translation_scope_returns_no_registry_candidates(tmp_path, monkeypatch):
    from action_tracker.data_quality.collection import evaluate_and_persist
    from action_tracker.orchestrator.daily import _commit_phase
    from action_tracker.qa.validator import trusted_field_scope
    cfg, qa, prior, kwargs, _ = _partial(tmp_path, monkeypatch)
    path = cfg['storage']['db_path']
    baseline = ProductionRepository(path).load_product_baseline()
    row = {**baseline['1001'], 'image_url': 'https://www.action.com/generated-image.png',
           'presence_source': 'LISTING_ONLY',
           'fact_field_provenance': {'image_url': {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'}}}
    run_id = 'image-only'
    scope = trusted_field_scope([row], qa, run_id=run_id, business_date='2026-10-30')
    assert set(scope['rows'][0]['fields']) == {'image_url'}
    quality = evaluate_and_persist(path, run_id, {'run_date': '2026-10-30', 'qa_state': 'FAIL',
        'qa_issues': qa.issues, 'trusted_field_scope': scope, 'listing_unique': 1,
        'current_valid': 1, 'category_coverage': {'Hogar': False}})
    diagnostics = {}
    updated = {**kwargs, 'run_id': run_id, 'today_records': {'1001': row}, 'baseline': baseline,
        'expected_base_commit_id': prior['commit_id'], 'sqlite_diagnostics': diagnostics,
        'run_report': {'qa_issues': qa.issues, 'commit_scope': 'POSITIVE_FACTS_ONLY',
            'parent_workflow_run_id': run_id, 'trusted_field_scope': scope,
            'collection_quality_state': quality.state, 'collection_metrics_hash': quality.metrics_hash}}
    assert _commit_phase(cfg, **updated) == 'FIELDS_COMMITTED', diagnostics
    assert diagnostics['translation_registry']['status'] == 'SUCCESS'
    with trace_reads(monkeypatch) as statements:
        assert committed_registry_records(cfg, source_run_id=run_id,
            fact_commit_id=diagnostics['commit_id'], business_date='2026-10-30') == []
    assert_source_only(statements, '1001')
    with connect(path) as db:
        assert [r[0] for r in db.execute('SELECT requested_fields FROM translation_queue')] == ['spec']


def test_full_normal_registry_still_rejects_an_extra_current_identity(tmp_path):
    from test_repository_closure import committed_fixture
    cfg, commit = committed_fixture(tmp_path)
    kwargs = dict(source_run_id='daily-r1', fact_commit_id=commit, business_date='2026-08-30')
    before = committed_registry_records(cfg, **kwargs)
    assert before
    with connect(cfg['storage']['db_path']) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,status) VALUES('ACT9999999','9999999','Unrelated','CURRENT')")
    with pytest.raises(ValueError, match='CURRENT_SKU_SET_CHANGED'):
        committed_registry_records(cfg, **kwargs)
