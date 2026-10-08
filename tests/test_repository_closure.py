"""CI_SAFE: publication and Operations contracts; temporary files only."""
import json
from pathlib import Path

import pytest

from action_tracker.exporting.service import export_catalog, ExportValidationError
from action_tracker.exporting.template1_service import export_template1
from action_tracker.operations import entry
from action_tracker.orchestrator import daily
from test_exporting import _cfg, _record, _write_master, _write_snapshot, _write_dictionary, _run_log


def catalog_fixture(tmp_path):
    cfg = _cfg(tmp_path)
    record = _record()
    _write_master(cfg['paths']['master'], [_run_log('r1', '2026-08-24')], [record])
    _write_snapshot(cfg['paths']['snapshots'], 'r1', '2026-08-24', [record])
    _write_dictionary(cfg['paths']['dictionary_baseline'], record)
    return cfg


@pytest.mark.parametrize('language', ['es', 'zh'])
def test_preview_preserves_formal_and_unmarked_bundles(tmp_path, language):
    cfg = catalog_fixture(tmp_path)
    from action_tracker.exporting.profiles import load_profile
    output = cfg['paths']['exports'] / load_profile(cfg, language=language, no_images=True).filename_for('20260824')
    output.parent.mkdir(parents=True)
    paths = [output, output.with_suffix('.manifest.json'), output.with_suffix('.repair-report.json')]
    for path in paths:
        path.write_bytes(b'protected publication')
    for _ in range(2):
        result = export_catalog(cfg, language=language, export_date='2026-08-24', no_images=True)
        assert Path(result['output']).parent == output.parent / 'preview'
        assert all(path.read_bytes() == b'protected publication' for path in paths)


def test_preview_refuses_unknown_existing_artifact_in_preview_directory(tmp_path):
    cfg = catalog_fixture(tmp_path)
    from action_tracker.exporting.profiles import load_profile
    output = cfg['paths']['exports'] / 'preview' / load_profile(cfg, language='es', no_images=True).filename_for('20260824')
    output.parent.mkdir(parents=True)
    output.write_bytes(b'unknown')
    with pytest.raises(ExportValidationError, match='PREVIEW_EXISTING_ARTIFACT_UNTRUSTED'):
        export_catalog(cfg, language='es', export_date='2026-08-24', no_images=True)
    assert output.read_bytes() == b'unknown'


def test_template1_research_release_requires_full_sqlite_contract(tmp_path):
    cfg = catalog_fixture(tmp_path)
    with pytest.raises(ExportValidationError, match='PRODUCTION_RELEASE_REQUIRES_SQLITE_CURRENT'):
        export_template1(cfg, export_date='2026-08-24', research_release=True)


def operations_fixture(tmp_path, monkeypatch, result):
    monkeypatch.setattr(entry, 'production_preflight', lambda *_: {})
    monkeypatch.setattr(entry, 'backup_sqlite', lambda *_, **kw: {})
    monkeypatch.setattr(entry, 'git_commit_info', lambda: 'fixture')
    calls = []
    def collect(*args, **kwargs):
        calls.append(kwargs)
        return result
    monkeypatch.setattr(daily, 'run_daily', collect)
    cfg = {'project_root': tmp_path, 'paths': {'state': tmp_path / 'state', 'backups': tmp_path / 'backups'},
           'localization': {'registry_enabled': True}}
    return cfg, calls


def test_operations_propagates_business_date_and_registry_failure(tmp_path, monkeypatch):
    result = {'run_id': 'r1', 'business_date': '2026-08-24', 'commit_id': 'c1',
              'commit_status': 'FULL_COMMIT', 'qa': {'passed': True},
              'run_report': {'sqlite': {'translation_registry': {'status': 'FAILED', 'error': 'fixture'}}}}
    cfg, calls = operations_fixture(tmp_path, monkeypatch, result)
    outcome = entry.run_production(cfg, business_date='2026-08-24')
    assert calls[0]['business_date'] == '2026-08-24'
    assert calls[0]['_skip_lock'] is True
    assert outcome['steps']['DB_COMMIT']['status'] == 'SUCCESS'
    assert outcome['steps']['REGISTRY']['status'] == 'FAILED'
    assert outcome['steps']['REGISTRY']['retryable'] is True
    assert outcome['state'] == 'DEGRADED'


def test_operations_rejects_delegated_date_mismatch(tmp_path, monkeypatch):
    cfg, calls = operations_fixture(tmp_path, monkeypatch, {
        'run_id': 'r1', 'business_date': '2026-08-25', 'qa': {'passed': True}, 'commit_status': 'FULL_COMMIT'})
    outcome = entry.run_production(cfg, business_date='2026-08-24')
    assert outcome['state'] == 'BLOCKED'
    assert outcome['steps']['COLLECTION']['error_code'] == 'COLLECTION_BUSINESS_DATE_MISMATCH'
    assert 'DB_COMMIT' not in outcome['steps']


def committed_fixture(tmp_path):
    from test_database_integration import _cfg as db_cfg, _bundle
    from action_tracker.database.integration import commit_daily_bundle
    cfg = db_cfg(tmp_path, mode='SQLITE_PRIMARY')
    commit = commit_daily_bundle(cfg, _bundle(cfg, run_id='daily-r1'))
    return cfg, commit


def business_tables(path):
    import sqlite3
    with sqlite3.connect(path) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
                for table in ('products', 'product_localizations', 'lifecycle_state', 'observations', 'price_history', 'event_history', 'commit_batches')}


def test_registry_recovery_is_scoped_idempotent_and_preserves_facts(tmp_path):
    from action_tracker.operations.registry import record_registry_status, retry_registry
    from action_tracker.database.connection import connect
    cfg, commit = committed_fixture(tmp_path)
    path = cfg['storage']['db_path']
    before = business_tables(path)
    record_registry_status(cfg, source_run_id='daily-r1', fact_commit_id=commit,
                           business_date='2026-08-30', result={'status': 'FAILED', 'error': 'injected'})
    args = dict(source_run_id='daily-r1', fact_commit_id=commit, business_date='2026-08-30')
    first = retry_registry(cfg, **args)
    assert first['status'] == 'SUCCESS'
    assert first['retry_attempts'] == 1
    with connect(path) as db:
        counts = tuple(db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('translation_source_versions', 'translation_units', 'translation_queue'))
    second = retry_registry(cfg, **args)
    assert second['recovery_result'] == 'ALREADY_READY'
    assert second['retry_attempts'] == 1
    with connect(path) as db:
        assert tuple(db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('translation_source_versions', 'translation_units', 'translation_queue')) == counts
    assert business_tables(path) == before
    with pytest.raises(ValueError, match='IDENTITY_MISMATCH'):
        retry_registry(cfg, **{**args, 'fact_commit_id': 'other'})


def test_registry_recovery_rejects_stale_source(tmp_path):
    from action_tracker.operations.registry import record_registry_status, retry_registry
    from action_tracker.database.connection import connect
    cfg, commit = committed_fixture(tmp_path)
    record_registry_status(cfg, source_run_id='daily-r1', fact_commit_id=commit,
                           business_date='2026-08-30', result={'status': 'FAILED'})
    with connect(cfg['storage']['db_path']) as db:
        db.execute("UPDATE product_localizations SET name='Changed' WHERE language='es'")
    result = retry_registry(cfg, source_run_id='daily-r1', fact_commit_id=commit, business_date='2026-08-30')
    assert result['status'] == 'FAILED'
    assert 'SOURCE_CHANGED' in result['error']


def test_v2_reuses_daily_facts_without_partial_production_commit(tmp_path):
    from action_tracker.workflow_v2.runner import WorkflowV2Runner
    from action_tracker.workflow_v2.context import new_context
    from action_tracker.workflow_v2.fact_adapter import load_committed_daily_source
    cfg, commit = committed_fixture(tmp_path)
    source = load_committed_daily_source(cfg, business_date='2026-08-30')
    root = tmp_path / 'workflow'
    runner = WorkflowV2Runner(root=root, context=new_context(root, business_date='2026-08-30'),
                              records=source['records'], cfg=cfg, temp_db=cfg['storage']['db_path'], production_mode=True)
    runner.context.source_ready = True
    runner._expected_primary_head = commit
    before = business_tables(cfg['storage']['db_path'])
    assert runner._fact_commit().details['state'] == 'VERIFIED_DAILY_FACT_COMMIT_REUSED'
    assert runner._fact_commit().details['fact_write_attempts'] == 0
    assert business_tables(cfg['storage']['db_path']) == before
    runner.records[0]['current_price'] = 99
    assert runner._fact_commit().status == 'BLOCKED'
    assert business_tables(cfg['storage']['db_path']) == before


def test_v2_and_operations_share_lock(tmp_path):
    from action_tracker.services.runtime import RunLock
    from action_tracker.workflow_v2.runner import WorkflowV2Runner
    from action_tracker.workflow_v2.context import new_context
    cfg, commit = committed_fixture(tmp_path)
    lock = RunLock(cfg['paths']['state'])
    lock.acquire('operations')
    try:
        root = tmp_path / 'workflow'
        runner = WorkflowV2Runner(root=root, context=new_context(root, business_date='2026-08-30'),
                                  cfg=cfg, temp_db=cfg['storage']['db_path'], production_mode=True)
        with pytest.raises(RuntimeError, match='RUN_ALREADY_ACTIVE'):
            runner.run()
    finally:
        lock.release()


def test_daily_frozen_empty_head_cannot_rebind_to_new_head(tmp_path):
    from dataclasses import replace
    from test_database_integration import _bundle
    from action_tracker.database.integration import commit_daily_bundle
    from action_tracker.database.production import ProductionDatabaseError
    cfg, commit = committed_fixture(tmp_path)
    pending = replace(_bundle(cfg, run_id='daily-r2'), base_commit_id='')
    with pytest.raises(ProductionDatabaseError, match='BASELINE_CHANGED'):
        commit_daily_bundle(cfg, pending)


def test_template1_preview_isolated(tmp_path):
    cfg = catalog_fixture(tmp_path)
    from test_template1 import _write_seed
    seed = tmp_path / 'seed.xlsx'
    _write_seed(seed)
    cfg['history_sources_path'] = tmp_path / 'history.yaml'
    cfg['history_sources_path'].write_text(f"seed:\n  path: '{seed.as_posix()}'\n  sheet: ACTION商品上下架明细\n  sku_header: 编号\n  presence_capability: true\n  absence_capability: true\n  observation_complete: true\n  evidence_level: A\nsources: []\n", encoding='utf-8')
    result = export_template1(cfg, export_date='2026-08-24')
    assert Path(result['output']).parent == cfg['paths']['exports'] / 'preview'
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    assert manifest['release_mode'] == 'preview'
    assert manifest['export_repair']['audited_zh_rows_hash'] == manifest['export_repair']['published_zh_rows_hash']


def test_multiday_daily_source_reuse_preserves_lifecycle_price_and_resume(tmp_path):
    from dataclasses import replace
    from action_tracker import state as st
    from action_tracker.services.lifecycle import classify
    from action_tracker.database.integration import build_daily_bundle, commit_daily_bundle, latest_commit_id
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.workflow_v2.fact_adapter import load_committed_daily_source
    from test_database_integration import _cfg as db_cfg, _quality_report
    from test_lifecycle_state import _sku
    cfg = db_cfg(tmp_path, mode='SQLITE_PRIMARY')
    known, baseline = {}, {}
    # A stable companion retains a valid current set while the subject goes offline.
    timeline = [(True, 2.5), (False, 2.5), (False, 2.5), (False, 2.5), (True, 3.5), (True, 1.5)]
    observed_states = []
    for index, (present, price) in enumerate(timeline, 1):
        day, rid = f'2026-08-{index + 10:02}', f'day-{index}'
        statuses, records = {}, {}
        for sku, seen in [('1001', present), ('2001', True)]:
            prior = known.get(sku, {})
            cls = classify(seen, prior.get('last_status', ''), sku in known, int(prior.get('missing_count') or 0), 3)
            status = _sku(sku, status=cls.status, cid=f'ACT{int(sku):07}', missing_count=cls.missing_count,
                          event=cls.event, previous_status=prior.get('last_status', ''))
            status.sitemap_present = status.listing_present = seen
            status.source_flag = 'BOTH' if seen else 'NONE'
            statuses[sku] = status
            if seen:
                records[sku] = {**_record(sku, current=price, last_seen=day), 'first_seen': '2026-08-11'}
        transition = st.apply_state_transition(known, statuses, day, rid, 3)
        quality = _quality_report(cfg, rid, day, sitemap_unique=len(records), listing_unique=len(records), current_valid=len(records))
        price_events = [] if index < 5 else [{'sku': '1001', 'canonical_id': 'ACT0001001', 'observed_at': day, 'change_type': 'PRICE_UP' if index == 5 else 'PRICE_DOWN', 'old_price': 2.5 if index == 5 else 3.5, 'new_price': price}]
        bundle = build_daily_bundle(run_id=rid, observation_date=day, qa_state='PASS', today_records=records,
            baseline=baseline, statuses=statuses, known=known, transition=transition, today_set=set(records),
            observation_complete=True, price_events=price_events, event_events=daily._build_lifecycle_events(statuses, day, rid),
            review_rows=[], run_record=quality)
        bundle = replace(bundle, base_commit_id=latest_commit_id(cfg['storage']['db_path']) or '')
        commit = commit_daily_bundle(cfg, bundle)
        before = business_tables(cfg['storage']['db_path'])
        source = load_committed_daily_source(cfg, business_date=day)
        assert source['fact_commit_id'] == commit
        assert set(source['authoritative_skus']) == set(records)
        assert commit_daily_bundle(cfg, bundle) == commit
        assert business_tables(cfg['storage']['db_path']) == before
        known = ProductionRepository(cfg['storage']['db_path']).load_known_skus()
        baseline = ProductionRepository(cfg['storage']['db_path']).load_current_products()
        observed_states.append((known['1001']['last_status'], int(known['1001']['missing_count'])))
    assert observed_states == [('ACTIVE', 0), ('MISSING', 1), ('MISSING', 2), ('OFFLINE', 3), ('ACTIVE', 0), ('ACTIVE', 0)]


@pytest.mark.parametrize('suffix', ['.xlsx', '.manifest.json', '.repair-report.json'])
def test_bundle_failure_preserves_all_three_previous_files(tmp_path, monkeypatch, suffix):
    from action_tracker.exporting.service import _publish_export_bundle
    output = tmp_path / 'formal.xlsx'
    manifest, repair = output.with_suffix('.manifest.json'), output.with_suffix('.repair-report.json')
    for path in (output, manifest, repair):
        path.write_bytes(b'old-' + path.suffix.encode())
    before = {path: path.read_bytes() for path in (output, manifest, repair)}
    temporary = tmp_path / 'staged.xlsx'
    temporary.write_bytes(b'new')
    original = Path.replace
    injected = []
    def replace(source, target):
        if Path(target).name == output.with_suffix(suffix).name and not injected:
            injected.append(True)
            raise OSError('injected replace failure')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError):
        _publish_export_bundle(temporary, output, manifest, {}, repair_report_path=repair, repair_report={})
    assert {path: path.read_bytes() for path in before} == before


def test_registry_resume_upgrades_state_without_recollection(tmp_path, monkeypatch):
    from action_tracker.operations.registry import record_registry_status
    from action_tracker import dictionary_enrichment, review_queue
    db_cfg, commit = committed_fixture(tmp_path)
    record_registry_status(db_cfg, source_run_id='daily-r1', fact_commit_id=commit,
        business_date='2026-08-30', result={'status': 'FAILED', 'error': 'fixture'})
    cfg, calls = operations_fixture(tmp_path, monkeypatch, {
        'run_id': 'daily-r1', 'business_date': '2026-08-30', 'qa': {'passed': True},
        'commit_status': 'FULL_COMMIT', 'commit_id': commit, 'registry': {'status': 'FAILED'}})
    cfg['storage'] = db_cfg['storage']
    monkeypatch.setattr(dictionary_enrichment, 'enrich_dictionary', lambda *_args, **_kw: {})
    monkeypatch.setattr(review_queue, 'build_review_queue', lambda *_args, **_kw: {})
    first = entry.run_production(cfg, business_date='2026-08-30')
    assert first['state'] == 'DEGRADED'
    second = entry.run_production(cfg, business_date='2026-08-30', resume=True, run_id=first['run_id'])
    assert second['state'] == 'SUCCESS'
    assert len(calls) == 1
    assert second['steps']['REGISTRY']['details']['retry_attempts'] == 1


def test_v2_refuses_missing_quality_and_historical_date(tmp_path):
    from action_tracker.workflow_v2.fact_adapter import load_committed_daily_source
    from action_tracker.database.connection import connect
    cfg, commit = committed_fixture(tmp_path)
    with pytest.raises(ValueError, match='REQUIRES_COMMITTED_DAILY_FACTS'):
        load_committed_daily_source(cfg, business_date='2020-01-01')
    with connect(cfg['storage']['db_path']) as db:
        db.execute("DELETE FROM collection_quality_metrics WHERE metric_name='__collection_state'")
    before = business_tables(cfg['storage']['db_path'])
    with pytest.raises(Exception, match='COLLECTION_QUALITY_EVIDENCE_MISSING'):
        load_committed_daily_source(cfg, business_date='2026-08-30')
    assert before == business_tables(cfg['storage']['db_path'])


def test_documented_command_arguments_parse_without_execution():
    from action_tracker.cli import build_parser
    examples = [
        ['data-update', '--date', '2026-10-08'],
        ['production-run', '--date', '2026-10-08', '--resume', '--run-id', 'r', '--from-step', 'REGISTRY'],
        ['daily-run', '--dry-run', '--fetch-details'],
        ['translation-worker', '--run-id', 'r', '--limit', '50', '--provider', '--once'],
        ['data-update-v2', '--date', '2026-10-08', '--profile', 'profile.yaml', '--production-translation', '--no-dry-run'],
        ['workflow-v2-apply-owner-review', '--review-csv', 'review.csv', '--owner-decisions-csv', 'owner.csv', '--actor', 'human:owner', '--report', 'report.json'],
        ['translation-apply', '--from-registry', '--run-id', 'r', '--base-commit-id', 'c', '--actor', 'human:owner', '--dry-run'],
        ['localization-apply', '--run-id', 'r', '--dry-run'],
        ['export', '--lang', 'zh', '--no-images', '--date', '2026-10-08', '--research-release'],
        ['export-template1', '--date', '2026-10-08', '--research-release'],
        ['registry-retry', '--run-id', 'r', '--commit-id', 'c', '--date', '2026-10-08'],
        ['sync-exports', '--commit-id', 'c'], ['db-validate-production'], ['db-status'], ['status'], ['qa', '--run-id', 'r'],
    ]
    for args in examples:
        assert build_parser().parse_args(args).command == args[0]


def approved_export_fixture(tmp_path):
    from dataclasses import replace
    from test_database_integration import _cfg as db_cfg, _bundle
    from action_tracker.database.integration import _localization, commit_daily_bundle, acknowledge_compatibility_exports
    cfg = db_cfg(tmp_path, mode='SQLITE_PRIMARY')
    cfg['project_root'] = Path(__file__).resolve().parents[1]
    cfg['paths']['exports'] = tmp_path / 'exports'
    record = _record(last_seen='2026-08-30')
    record['cat1_zh'] = '家居布置'
    record['unit_price'] = '1,25 €/ud.'
    base = _bundle(cfg, run_id='formal-source')
    es = _localization(record, 'es')
    zh = {**_localization(record, 'zh'), 'review_status': 'HUMAN_REVIEWED', 'freshness_status': 'CURRENT',
          'source': 'HUMAN_OVERRIDE', 'resolution_status': 'APPLIED', 'approved_by': 'human:fixture',
          'approved_at': '2026-08-30'}
    bundle = replace(base, current_products=({**record}, base.current_products[1]), localization_updates=(es, zh))
    commit = commit_daily_bundle(cfg, bundle)
    cfg['paths']['master'].write_bytes(b'compatibility-fixture')
    for filename in ('known_skus.csv', 'offline_skus.csv'):
        (cfg['paths']['state'] / filename).write_bytes(b'compatibility-fixture')
    acknowledge_compatibility_exports(cfg, commit)
    return cfg


def test_formal_catalog_passes_real_complete_gate_then_preview_preserves_it(tmp_path):
    cfg = approved_export_fixture(tmp_path)
    result = export_catalog(cfg, language='zh', export_date='2026-08-30', no_images=True, research_release=True)
    output = Path(result['output'])
    assert output.parent == cfg['paths']['exports']
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    assert manifest['strict_release_audit']['status'] == 'PASS'
    before = {path: path.read_bytes() for path in (output, Path(result['manifest']), Path(result['repair_report']))}
    preview = export_catalog(cfg, language='zh', export_date='2026-08-30', no_images=True)
    assert Path(preview['output']).parent == output.parent / 'preview'
    assert before == {path: path.read_bytes() for path in before}


def test_template1_formal_runs_the_same_complete_gate(tmp_path):
    from test_template1 import _write_seed
    cfg = approved_export_fixture(tmp_path)
    seed = tmp_path / 'seed.xlsx'
    _write_seed(seed)
    cfg['history_sources_path'] = tmp_path / 'history.yaml'
    cfg['history_sources_path'].write_text(f"seed:\n  path: '{seed.as_posix()}'\n  sheet: ACTION商品上下架明细\n  sku_header: 编号\n  presence_capability: true\n  absence_capability: true\n  observation_complete: true\n  evidence_level: A\nsources: []\n", encoding='utf-8')
    result = export_template1(cfg, export_date='2026-08-30', research_release=True)
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    assert manifest['release_mode'] == 'production_release'
    assert manifest['strict_release_audit']['status'] == 'PASS'


def test_template1_repair_pass_cannot_bypass_master_quality(tmp_path):
    from action_tracker.database.connection import connect
    cfg = approved_export_fixture(tmp_path)
    with connect(cfg['storage']['db_path']) as db:
        db.execute('UPDATE products SET original_price=2 WHERE official_sku=?', ('1001',))
    with pytest.raises(ExportValidationError, match='RESEARCH_RELEASE_GATE_FAILED:MASTER_QUALITY_BLOCKED'):
        export_template1(cfg, export_date='2026-08-30', research_release=True)
    assert not cfg['paths']['exports'].exists()


def test_operations_resume_rejects_wrong_persisted_identity(tmp_path):
    from action_tracker.operations.runner import ProductionRunner
    runner = ProductionRunner(root=tmp_path / 'reports', business_date='2026-10-08', run_id='r1', steps={})
    runner.run_dir.mkdir(parents=True)
    runner.state_path.write_text(json.dumps({'run_id': 'r1', 'business_date': '2026-10-09'}), encoding='utf-8')
    with pytest.raises(ValueError, match='RESUME_RUN_ID_OR_BUSINESS_DATE_MISMATCH'):
        runner.run(resume=True)
