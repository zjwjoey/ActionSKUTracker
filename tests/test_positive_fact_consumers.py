"""Isolated committed-scope tests for Registry, Operations and formal readers."""
import json

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.integration import latest_commit_id
from action_tracker.database.production import CommitBundle, ProductionWriter
from action_tracker.database.repository import ProductionRepository
from action_tracker.data_quality.collection import evaluate_and_persist
from action_tracker.exporting.service import _resolve_sqlite_current_source, ExportValidationError
from action_tracker.operations.registry import committed_registry_records
from action_tracker.orchestrator.daily import _commit_phase
from action_tracker.qa.validator import QAReport, trusted_field_scope
from action_tracker.services.hashing import localization_source_hash
from action_tracker.workflow_v2.fact_adapter import load_committed_daily_source


def _partial(tmp_path, monkeypatch, *, run_id='partial'):
    db = tmp_path / 'isolated.db'
    state = tmp_path / 'state'; state.mkdir(exist_ok=True)
    cfg = {'project_root': tmp_path, 'storage': {'mode': 'SQLITE_PRIMARY', 'db_path': db},
           'paths': {'state': state, 'master': tmp_path / 'Master.xlsx', 'backups': tmp_path / 'backups'},
           'localization': {'registry_enabled': True}, 'run': {}}
    old = {'sku': '1001', 'canonical_id': 'ACT0001001', 'status': 'CURRENT',
           'name_es': 'Producto', 'cat1_es': 'Hogar', 'spec_es': '1 pieza',
           'product_url': 'https://www.action.com/es-es/p/1001/producto/', 'current_price': 2.5}
    writer = ProductionWriter(db, role='PRIMARY')
    head = writer.commit(CommitBundle(run_id='full', observation_date='2026-10-29', qa_state='PASS',
        current_products=(old,), localization_updates=(
            {'sku': '1001', 'language': 'es', 'name': 'Producto', 'cat1': 'Hogar', 'spec': '1 pieza'},
            {'sku': '1001', 'language': 'zh', 'name': '商品', 'spec': '1件', 'source_hash': localization_source_hash(old),
             'review_status': 'APPROVED', 'resolution_status': 'APPLIED', 'approved_by': 'human:fixture'}),
        observations=({'sku': '1001', 'run_id': 'full', 'observation_date': '2026-10-29',
                       'presence_state': 'PRESENT', 'observation_complete': True,
                       'absence_capable': True, 'current_price': 2.5},)))
    baseline = ProductionRepository(db).load_product_baseline()
    row = {**old, 'spec_es': '2 piezas', 'presence_source': 'LISTING_ONLY',
           'fact_field_provenance': {'spec_es': {'state': 'CURRENT_VERIFIED', 'source': 'DETAIL_CURRENT_RUN'}}}
    qa = QAReport(False, 'FAIL', issues=[{'level': 'HARD_BLOCK', 'sku': '',
                                        'field_name': 'presence', 'reason': 'OBSERVATION_INVALID'}])
    scope = trusted_field_scope([row], qa, run_id=run_id, business_date='2026-10-30')
    quality = evaluate_and_persist(db, run_id, {'run_date': '2026-10-30', 'qa_state': 'FAIL',
        'qa_issues': qa.issues, 'trusted_field_scope': scope, 'listing_unique': 1,
        'current_valid': 1, 'category_coverage': {'Hogar': False}})
    report = {'qa_issues': qa.issues, 'commit_scope': 'POSITIVE_FACTS_ONLY',
              'parent_workflow_run_id': run_id,
              'trusted_field_scope': scope, 'collection_quality_state': quality.state,
              'collection_metrics_hash': quality.metrics_hash}
    # This group tests commit and consumers. Full workbook projection has its
    # own acceptance group; no mock result is counted as real Master validation.
    monkeypatch.setattr('action_tracker.database.integration.regenerate_compatibility_exports',
                        lambda *_: {'status': 'SUCCESS'})
    diagnostics = {}
    kwargs = dict(statuses={}, known={}, run_date='2026-10-30', run_id=run_id, offline_runs=3,
        today_records={'1001': row}, price_events=[], event_events=[], run_log_row={'QA状态': 'FAIL'},
        review_rows=[], baseline=baseline, today_set={'1001'}, observation_complete=False,
        run_report=report, sqlite_diagnostics=diagnostics, expected_base_commit_id=head, enforce_expected_base=True)
    assert _commit_phase(cfg, **kwargs) == 'FIELDS_COMMITTED', diagnostics
    return cfg, qa, diagnostics, kwargs, head


def test_partial_registry_queues_only_allowed_field_and_resume_is_idempotent(tmp_path, monkeypatch):
    cfg, _, diagnostics, kwargs, head = _partial(tmp_path, monkeypatch)
    assert diagnostics['translation_registry']['status'] == 'SUCCESS'
    records = committed_registry_records(cfg, source_run_id='partial',
        fact_commit_id=diagnostics['commit_id'], business_date='2026-10-30')
    assert records[0]['_registry_allowed_fields'] == ['spec_es']
    assert _commit_phase(cfg, **kwargs) == 'FIELDS_COMMITTED'
    assert latest_commit_id(cfg['storage']['db_path']) == diagnostics['commit_id'] != head
    with connect(cfg['storage']['db_path']) as db:
        queued = db.execute('SELECT requested_fields FROM translation_queue').fetchall()
        assert [row[0] for row in queued] == ['spec']
        assert db.execute("SELECT COUNT(*) FROM commit_batches WHERE run_id='partial'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM lifecycle_state").fetchone()[0] == 0


def test_partial_preserves_chinese_text_and_approval_but_invalidates_freshness(tmp_path, monkeypatch):
    cfg, _, _, _, _ = _partial(tmp_path, monkeypatch)
    with connect(cfg['storage']['db_path']) as db:
        row = db.execute("SELECT spec,review_status,freshness_status,approved_by FROM product_localizations WHERE language='zh'").fetchone()
    assert row['spec'] == '1件'
    assert row['approved_by'] == 'human:fixture'
    assert row['freshness_status'] == 'STALE'


def test_partial_is_not_a_complete_daily_or_formal_export_source(tmp_path, monkeypatch):
    cfg, _, _, _, _ = _partial(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='WORKFLOW_V2_REQUIRES_COMPLETE_PRESENCE'):
        load_committed_daily_source(cfg, business_date='2026-10-30')
    with pytest.raises(ExportValidationError, match='SQLITE_POSITIVE_FACTS_ONLY_NOT_RELEASE_READY'):
        _resolve_sqlite_current_source(cfg, export_date='2026-10-30', requested_run_id=None)


def test_operations_keeps_verified_partial_degraded_and_resume_never_recollects(tmp_path, monkeypatch):
    cfg, qa, diagnostics, _, _ = _partial(tmp_path, monkeypatch)
    from action_tracker.operations import entry
    calls = []
    def collected(*_args, **_kwargs):
        calls.append('collect')
        return {'run_id': 'partial', 'business_date': '2026-10-30', 'qa': qa.to_dict(),
                'commit_status': 'FIELDS_COMMITTED', 'commit_id': diagnostics['commit_id'],
                'registry': diagnostics['translation_registry']}
    monkeypatch.setattr('action_tracker.orchestrator.daily.run_daily', collected)
    monkeypatch.setattr(entry, 'production_preflight', lambda *_: {'status': 'PASS'})
    monkeypatch.setattr(entry, 'backup_sqlite', lambda *_args, **_kwargs: {'status': 'PASS'})
    monkeypatch.setattr('action_tracker.review_queue.build_review_queue', lambda *_args, **_kwargs: {})
    first = entry.run_production(cfg, business_date='2026-10-30')
    assert first['state'] == 'DEGRADED' and first['exit_code'] == 10
    assert first['steps']['QA']['details']['qa']['passed'] is False
    assert first['steps']['DB_COMMIT']['status'] == 'SUCCESS'
    assert first['steps']['EXPORT']['details']['formal_release'] is False
    second = entry.run_production(cfg, business_date='2026-10-30', resume=True, run_id=first['run_id'])
    assert second['state'] == 'DEGRADED'
    assert calls == ['collect']


def test_post_commit_failure_recovers_exact_identity_without_recollection(tmp_path, monkeypatch):
    from action_tracker.operations import entry
    cfg = {'project_root': tmp_path,
           'storage': {'mode': 'SQLITE_PRIMARY', 'db_path': tmp_path / 'isolated.db'},
           'paths': {'state': tmp_path / 'state', 'backups': tmp_path / 'backups',
                     'master': tmp_path / 'Master.xlsx'},
           'localization': {'registry_enabled': True}, 'run': {}}
    calls = []
    def commit_then_fail(*_args, **kwargs):
        calls.append(kwargs['workflow_run_id'])
        _partial(tmp_path, monkeypatch, run_id=kwargs['workflow_run_id'])
        raise OSError('Injected failure after SQLite commit, before result delivery')
    monkeypatch.setattr('action_tracker.orchestrator.daily.run_daily', commit_then_fail)
    monkeypatch.setattr(entry, 'production_preflight', lambda *_: {'status': 'PASS'})
    monkeypatch.setattr(entry, 'backup_sqlite', lambda *_args, **_kwargs: {'status': 'PASS'})
    monkeypatch.setattr('action_tracker.review_queue.build_review_queue', lambda *_args, **_kwargs: {})
    monkeypatch.setattr('action_tracker.database.integration.regenerate_pending_exports',
                        lambda *_args, **_kwargs: [{'status': 'SUCCESS'}])
    result = entry.run_production(cfg, business_date='2026-10-30')
    assert result['state'] == 'DEGRADED'
    assert result['steps']['COLLECTION']['details']['post_commit_recovered'] is True
    assert result['steps']['DB_COMMIT']['status'] == 'SUCCESS'
    recovered = entry.run_production(cfg, business_date='2026-10-30', resume=True, run_id=result['run_id'])
    assert recovered['state'] == 'DEGRADED'
    assert calls == [result['run_id']]
    with connect(cfg['storage']['db_path']) as db:
        assert db.execute('SELECT COUNT(*) FROM commit_batches WHERE run_id=?', (result['run_id'],)).fetchone()[0] == 1


def test_full_commit_registry_uses_selected_es_with_real_compatibility_projection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from openpyxl import Workbook, load_workbook
    from action_tracker.excel.writer import ES_MAP, ZH_MAP, PRICE_HISTORY_HEADERS, EVENT_HISTORY_HEADERS, RUN_LOG_HEADERS, REVIEW_HEADERS
    from action_tracker.localization.hashes import value_hash

    cfg, _, _, _, _ = _partial(tmp_path, monkeypatch)
    cfg['paths']['temp'] = tmp_path / 'staging'
    workbook = Workbook(); workbook.remove(workbook.active)
    sheets = {'01_SKU_ZH_CURRENT': list(ZH_MAP), '02_SKU_ES_CURRENT': list(ES_MAP),
              '03_PRICE_HISTORY': PRICE_HISTORY_HEADERS, '04_EVENT_HISTORY': EVENT_HISTORY_HEADERS,
              '05_RUN_LOG': RUN_LOG_HEADERS, '06_REVIEW_QUEUE': REVIEW_HEADERS}
    for name, headers in sheets.items():
        workbook.create_sheet(name).append(headers)
    workbook.save(cfg['paths']['master']); workbook.close()
    baseline = ProductionRepository(cfg['storage']['db_path']).load_product_baseline()
    row = {**baseline['1001'], 'current_price': 2.5, 'cat1_es': '',
           'fact_field_provenance': {'current_price': {'state': 'CURRENT_VERIFIED', 'source': 'LISTING_CURRENT_RUN'}}}
    issue = {'level': 'FIELD_REVIEW', 'sku': '1001', 'field_name': 'cat1_es',
             'reason': 'CATEGORY_SOURCE_MISSING', 'source_hash': value_hash('')}
    quality = evaluate_and_persist(cfg['storage']['db_path'], 'full-selected', {
        'run_date': '2026-10-30', 'qa_state': 'PASS', 'qa_issues': [issue],
        'sitemap_unique': 1, 'listing_unique': 1, 'current_valid': 1,
        'category_coverage': {f'cat-{i}': True for i in range(15)}})
    status = SimpleNamespace(sku='1001', canonical_id='ACT0001001', status='ACTIVE',
        observation_valid=True, sitemap_present=True, listing_present=True, source_flag='BOTH',
        first_seen='2026-10-29', missing_count=0)
    diagnostics = {}
    result = _commit_phase(cfg, statuses={'1001': status}, known={}, run_date='2026-10-30',
        run_id='full-selected', offline_runs=3, today_records={'1001': row}, price_events=[],
        event_events=[], run_log_row={'QA状态': 'PASS'}, review_rows=[], baseline=baseline,
        today_set={'1001'}, sqlite_diagnostics=diagnostics,
        run_report={'qa_issues': [issue], 'collection_quality_state': quality.state,
                    'collection_metrics_hash': quality.metrics_hash, 'commit_scope': 'FULL_OBSERVATION'})
    assert result == 'FULL_COMMIT', diagnostics
    assert diagnostics['translation_registry']['status'] == 'SUCCESS'
    with connect(cfg['storage']['db_path']) as db:
        sources = [json.loads(r[0]) for r in db.execute('SELECT source_fields_json FROM translation_source_versions')]
        assert all(source['cat1_es'] == 'Hogar' for source in sources)
        assert db.execute('SELECT cat1 FROM product_localizations WHERE language=\'es\'').fetchone()[0] == 'Hogar'
    workbook = load_workbook(cfg['paths']['master'], read_only=True)
    try:
        assert workbook['02_SKU_ES_CURRENT'].max_row == 2
    finally:
        workbook.close()
