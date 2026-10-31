"""CI_SAFE: synthetic registries/SQLite only; no provider or production writes."""
import copy
import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_tracker.exporting.difference_whitelist import (
    CONTRACT, DifferenceWhitelist, DifferenceWhitelistError, value_hash,
    pending_conflict_report, check_current_conflicts, conflict_tracking_from_config,
)
from action_tracker.exporting.repair_overrides import field_source_hash

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def record(sku='1'):
    return dict(sku=sku, name_es='Producto', name_zh='商品', cat1_es='Hogar',
        cat1_zh='家居', cat2_es='Decoración', cat2_zh='装饰', spec_es='10 cm', spec_zh='10cm',
        desc_es='Texto', desc_zh='描述', details_es='Tipo: Sí', details_zh='类型：是')


def entry(r=None, field='details_zh', **changes):
    r = r or record()
    return dict(id='tracked:'+r['sku']+':'+field, sku=r['sku'], field=field, action='ACK_DIFFERENCE',
        status='PENDING', classification='SOURCE_ATTRIBUTE_MEANING_UNCONFIRMED',
        source_hash=field_source_hash(r, field), current_hash=value_hash(r[field]),
        reviewed_hash=value_hash('未批准候选'), tracking_only=True, automatic_apply=False,
        does_not_waive_qa_or_approval=True, reason='类型标签对应布尔值，保留待核备注',
        evidence=['SYNTHETIC_SOURCE_EVIDENCE_NOT_PRODUCTION'], reviewed_by='SYNTHETIC_REVIEWER',
        reviewed_at=(NOW-timedelta(days=1)).isoformat(), expires_at=(NOW+timedelta(days=29)).isoformat(),
        **changes)


def registry_file(tmp_path, entries=None):
    p = tmp_path/'registry.json'
    p.write_text(json.dumps({'contract': CONTRACT, 'entries': entries or [entry()]}, ensure_ascii=False), encoding='utf8')
    return p


def test_known_conflict_remains_pending_and_does_not_grant_waiver():
    e = entry(); registry = DifferenceWhitelist([e]); original = copy.deepcopy(registry.entries)
    r = pending_conflict_report(registry, [record()], now=NOW)
    assert r['known_unchanged_count'] == 1 and r['recheck_required_count'] == 0
    assert r['blocked_fields_by_sku'] == {'1': ['details_zh']}
    assert r['entries'][0]['reason'] == e['reason']
    assert r['entries'][0]['status'] == 'PENDING' and not r['entries'][0]['automatic_apply']
    assert registry.match(record(), 'details_zh', '类型：是', '未批准候选') is None
    assert registry.entries == original


@pytest.mark.parametrize('values,changes', [
    ({'details_es': 'Tipo: No'}, ['SOURCE_HASH_CHANGED']),
    ({'details_zh': '类型：否'}, ['CURRENT_ZH_HASH_CHANGED']),
    ({'details_es': 'Otro tipo: No', 'details_zh': '另一类型'}, ['SOURCE_HASH_CHANGED', 'CURRENT_ZH_HASH_CHANGED']),
    ({'details_es': None}, ['SOURCE_VALUE_UNAVAILABLE']),
    ({'details_es': ''}, ['SOURCE_HASH_CHANGED']),
])
def test_exact_field_changes_reopen_without_mutating_or_resolving(values, changes):
    registry = DifferenceWhitelist([entry()]); r = {**record(), **values}
    out = pending_conflict_report(registry, [r], now=NOW)['entries'][0]
    assert out['check_state'] == 'RECHECK_REQUIRED' and out['changes'] == changes
    assert out['reason'] == entry()['reason'] and not out['automatic_apply']
    assert out['expected_source_hash'] == entry()['source_hash']


def test_unrelated_field_changes_do_not_reopen_same_sku_conflict():
    out = pending_conflict_report(DifferenceWhitelist([entry()]),
        [{**record(), 'name_es': 'Otro producto', 'spec_zh': '新的正常规格'}], now=NOW)
    assert out['known_unchanged_count'] == 1
    assert out['blocked_fields_by_sku'] == {'1': ['details_zh']}


@pytest.mark.parametrize('missing,reason', [('details_es', 'SOURCE_VALUE_UNAVAILABLE'), ('details_zh', 'CURRENT_ZH_UNAVAILABLE')])
def test_omitted_values_are_not_misreported_as_official_empty(missing, reason):
    r = record(); del r[missing]
    out = pending_conflict_report(DifferenceWhitelist([entry()]), [r], now=NOW)
    assert out['entries'][0]['changes'] == [reason]
    assert 'official_source_empty' not in out['entries'][0]


def test_missing_sku_is_check_scope_issue_not_presence_decision():
    out = pending_conflict_report(DifferenceWhitelist([entry()]), [], now=NOW)
    assert out['entries'][0]['changes'] == ['NOT_IN_CHECK_SCOPE']
    assert 'presence' not in out['entries'][0]


@pytest.mark.parametrize('updates,reason', [
    ({'expires_at': NOW.isoformat()}, 'REGISTRATION_EXPIRED'),
    ({'reviewed_at': (NOW+timedelta(days=1)).isoformat()}, 'REGISTRATION_METADATA_INVALID'),
    ({'expires_at': (NOW+timedelta(days=100)).isoformat()}, 'REGISTRATION_METADATA_INVALID'),
    ({'expires_at': 'bad'}, 'REGISTRATION_METADATA_INVALID'),
    ({'reason': ''}, 'REGISTRATION_METADATA_INVALID'),
    ({'evidence': []}, 'REGISTRATION_METADATA_INVALID'),
])
def test_expiry_or_bad_metadata_keeps_field_held_for_recheck(updates, reason):
    e = {**entry(), **updates}; out = pending_conflict_report(DifferenceWhitelist([e]), [record()], now=NOW)
    assert reason in out['entries'][0]['changes']
    assert out['blocked_fields_by_sku'] == {'1': ['details_zh']}


@pytest.mark.parametrize('updates', [{'tracking_only': 'true'}, {'automatic_apply': True},
    {'automatic_apply': 'false'}, {'does_not_waive_qa_or_approval': False}, {'status': 'CONFIRMED'}])
def test_tracking_contract_cannot_be_promoted_to_auto_apply(updates):
    with pytest.raises(DifferenceWhitelistError):
        pending_conflict_report(DifferenceWhitelist([{**entry(), **updates}]), [record()], now=NOW)


def test_legacy_entries_do_not_become_source_conflict_holds():
    e = entry(); e.pop('tracking_only')
    r = pending_conflict_report(DifferenceWhitelist([e]), [record()], now=NOW)
    assert r['tracked_field_count'] == 0


def test_duplicate_current_identity_is_rejected():
    with pytest.raises(DifferenceWhitelistError, match='IDENTITY'):
        pending_conflict_report(DifferenceWhitelist([entry()]), [record(), record()], now=NOW)


@pytest.mark.parametrize('options', [None, [], '', {'enabled': 'false'}, {'enabled': 1}, {'enabled': True}])
def test_config_fails_closed(options):
    with pytest.raises(DifferenceWhitelistError):
        conflict_tracking_from_config({'localization': {'source_conflict_tracking': options}})


def test_disabled_config_never_opens_registry_and_file_load_binds_bytes(tmp_path):
    assert conflict_tracking_from_config({'localization': {'source_conflict_tracking': {'enabled': False, 'path': 'missing'}}}) is None
    p = registry_file(tmp_path)
    loaded = conflict_tracking_from_config({'project_root': tmp_path,
        'localization': {'source_conflict_tracking': {'enabled': True, 'path': p.name}}})
    assert loaded.registry_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def database(path, status='CURRENT'):
    db = sqlite3.connect(path)
    db.executescript('CREATE TABLE products(official_sku TEXT,status TEXT,name_es TEXT,name_zh TEXT);'
        'CREATE TABLE product_localizations(official_sku TEXT,language TEXT,name TEXT,cat1 TEXT,cat2 TEXT,spec TEXT,description TEXT,details TEXT);')
    r = record(); db.execute('INSERT INTO products VALUES(?,?,?,?)', ('1', status, r['name_es'], r['name_zh']))
    db.execute('INSERT INTO product_localizations VALUES(?,?,?,?,?,?,?,?)',
        ('1', 'es', r['name_es'], r['cat1_es'], r['cat2_es'], r['spec_es'], r['desc_es'], r['details_es']))
    db.execute('INSERT INTO product_localizations VALUES(?,?,?,?,?,?,?,?)',
        ('1', 'zh', r['name_zh'], r['cat1_zh'], r['cat2_zh'], r['spec_zh'], r['desc_zh'], r['details_zh']))
    db.commit(); db.close()


def test_live_reader_is_read_only_repeatable_and_uri_safe(tmp_path):
    p = tmp_path/'data #1.sqlite3'; database(p)
    before = p.read_bytes(); registry = DifferenceWhitelist([entry()])
    one = check_current_conflicts(registry, p, now=NOW); two = check_current_conflicts(registry, p, now=NOW)
    assert one == two and one['known_unchanged_count'] == 1 and p.read_bytes() == before
    assert one['production_apply'] is False


def test_reader_does_not_create_missing_database_and_ignores_absent_products(tmp_path):
    p = tmp_path/'missing.sqlite3'
    with pytest.raises(DifferenceWhitelistError, match='DATABASE_MISSING'):
        check_current_conflicts(DifferenceWhitelist([entry()]), p)
    assert not p.exists()
    database(p, 'ABSENT')
    assert check_current_conflicts(DifferenceWhitelist([entry()]), p, now=NOW)['entries'][0]['changes'] == ['NOT_IN_CHECK_SCOPE']


def test_cli_uses_read_only_entry_without_loading_production_config(tmp_path, monkeypatch, capsys):
    from action_tracker import cli, config
    monkeypatch.setattr(config, 'load_settings', lambda **kw: pytest.fail('production configuration loaded'))
    monkeypatch.setattr(config, 'ensure_runtime_dirs', lambda cfg: pytest.fail('production paths initialized'))
    p = tmp_path/'primary.db'; database(p); reg = registry_file(tmp_path)
    assert cli.main(['export-difference-whitelist', '--registry', str(reg), '--check-current', str(p)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['read_only'] and result['tracked_field_count'] == 1


def service_fixture(tmp_path, monkeypatch, *, needs_ai=False):
    from action_tracker.localization import service
    from action_tracker.localization.contracts import LocalizationField, LocalizationPlan, LOCALIZATION_FIELDS
    from action_tracker.localization.knowledge import ensure_schemas
    from action_tracker.services.hashing import localization_source_hash
    directory = tmp_path/'dict'; ensure_schemas(directory)
    reg = registry_file(tmp_path)
    cfg = {'project_root': tmp_path, 'paths': {'temp': tmp_path/'temp', 'dictionary_baseline': directory},
        'localization': {'production_apply_enabled': True, 'source_conflict_tracking': {'enabled': True, 'path': str(reg)},
                         'ai': {'enabled': needs_ai, 'provider': 'fake'}},
        'knowledge': {'production_apply_enabled': True}}
    provider = SimpleNamespace(calls=0); calls = []
    class Engine:
        def __init__(self, **kwargs): pass
        def resolve(self, r, **kwargs):
            fields = {f: LocalizationField(r.get(f, ''), 'dictionary',
                'PENDING' if needs_ai and f in {'desc_zh','details_zh'} else 'READY', localization_source_hash(r)) for f in LOCALIZATION_FIELDS}
            return LocalizationPlan(r['sku'], localization_source_hash(r), fields, (), 'READY',
                ('DESCRIPTION_REVIEW','DETAIL_VALUE_REVIEW') if needs_ai else (), (), False)
        def validate(self, r, p):
            return SimpleNamespace(ok=not needs_ai, reasons=p.review_reasons,
                spanish_residue_tokens=(), numeric_mismatches=())
    monkeypatch.setattr(service, 'LocalizationEngine', Engine)
    monkeypatch.setattr(service, 'provider_from_config', lambda cfg: provider)
    def resolve(engine, r, plan, provider, requested_fields):
        provider.calls += 1; calls.append((r['sku'], requested_fields))
        return {'sku': r['sku'], 'schema_status': 'PASS', 'requested_fields': requested_fields}
    monkeypatch.setattr(service, 'resolve_unknown', resolve)
    return cfg, calls


def test_local_audit_preserves_notes_and_filters_ai_per_field(tmp_path, monkeypatch):
    from action_tracker.localization import service
    cfg, calls = service_fixture(tmp_path, monkeypatch, needs_ai=True)
    result = service.audit_current(cfg, run_id='local', records=[record(), record('2')])
    assert dict(calls)['1'] == ('description',)
    assert set(dict(calls)['2']) == {'description','details'}
    tracked = result['source_conflict_tracking']
    assert tracked['blocked_fields_by_sku'] == {'1': ['details_zh']}
    rows = list(csv.DictReader(Path(result['review_queue']).open(encoding='utf-8-sig')))
    note = next(x for x in rows if x['source'] == 'SOURCE_CONFLICT_TRACKING')
    assert entry()['reason'] in note['reason'] and note['field'] == 'details_zh' and note['suggested_value'] == ''
    assert result['ai_call_count'] == 2


def test_automatic_preparation_excludes_conflict_not_whole_sku(tmp_path, monkeypatch):
    from action_tracker.localization import service
    cfg, _ = service_fixture(tmp_path, monkeypatch)
    original = service.audit_current
    monkeypatch.setattr(service, 'audit_current', lambda cfg, run_id: original(cfg, run_id=run_id, records=[record(),record('2')]))
    captured = {}
    def prepare(db, **kw):captured.update(kw); return {'prepared_fields': 1}
    monkeypatch.setattr(service, 'apply_localization_correction', prepare)
    result = service.apply_from_audit(cfg, run_id='prepare', commit=True)
    assert not result['formal_apply']
    assert 'details' not in captured['localizations_by_sku']['1']
    assert 'description' in captured['localizations_by_sku']['1']
    assert 'details' in captured['localizations_by_sku']['2']
    rows = list(csv.DictReader(Path(result['audit']).open(encoding='utf-8-sig')))
    assert rows[0]['readiness'] == 'REVIEW_REQUIRED' and rows[0]['qa_readiness'] == 'READY'


def test_invalid_registry_stops_before_database_or_provider_access(tmp_path, monkeypatch):
    from action_tracker.localization import service
    monkeypatch.setattr(service, 'ProductionRepository', lambda *a: pytest.fail('database accessed'))
    cfg = {'project_root': tmp_path, 'localization': {'source_conflict_tracking': {'enabled': True, 'path': str(tmp_path/'missing')}}}
    with pytest.raises(FileNotFoundError):service.audit_current(cfg)


def test_default_off_keeps_ordinary_audit_and_ai_behavior(tmp_path, monkeypatch):
    from action_tracker.localization import service
    cfg, calls = service_fixture(tmp_path, monkeypatch, needs_ai=True)
    cfg['localization']['source_conflict_tracking']['enabled'] = False
    result = service.audit_current(cfg, run_id='disabled', records=[record()])
    assert set(dict(calls)['1']) == {'description','details'}
    assert 'source_conflict_tracking' not in result
    assert not (Path(result['report_dir'])/'source_conflict_tracking.json').exists()
    row = next(csv.DictReader(Path(result['audit']).open(encoding='utf-8-sig')))
    assert 'qa_readiness' not in row and 'tracked_conflict_fields' not in row


def test_localization_cli_opt_in_does_not_mutate_base_configuration(tmp_path, monkeypatch, capsys):
    from action_tracker import cli, config, log
    from action_tracker.localization import service
    base = {'paths': {'logs': tmp_path/'logs'}, 'localization': {'source_conflict_tracking': {'enabled': False, 'path': None}}}
    original = copy.deepcopy(base); captured = {}
    monkeypatch.setattr(config, 'load_settings', lambda **kw: base)
    monkeypatch.setattr(config, 'ensure_runtime_dirs', lambda cfg: None)
    monkeypatch.setattr(log, 'setup_logging', lambda path: None)
    def audit(cfg, **kwargs):captured.update(cfg); return {'tracking_checked': True}
    monkeypatch.setattr(service, 'audit_current', audit)
    p = registry_file(tmp_path)
    assert cli.main(['localization-audit', '--conflict-registry', str(p)]) == 0
    assert captured['localization']['source_conflict_tracking'] == {'enabled': True, 'path': str(p)}
    assert base == original and json.loads(capsys.readouterr().out)['tracking_checked']


def test_tracking_notes_use_existing_persistent_queue_contract(tmp_path, monkeypatch):
    from action_tracker.localization import service
    from action_tracker.review_queue import load_queue
    cfg, _ = service_fixture(tmp_path, monkeypatch)
    cfg['paths']['review_queue'] = tmp_path/'review_queue'
    service.audit_current(cfg, run_id='persist-one', records=[record()], persist_reviews=True)
    service.audit_current(cfg, run_id='persist-two', records=[record()], persist_reviews=True)
    rows = list(load_queue(cfg).values())
    assert len(rows) == 1 and rows[0]['issue_type'] == 'DATA_INCONSISTENCY'
    assert rows[0]['status'] == 'PENDING' and rows[0]['source'] == 'SOURCE_CONFLICT_TRACKING'
    assert entry()['reason'] in rows[0]['reason']


@pytest.mark.parametrize('fails', [False, True])
def test_readonly_cli_preserves_unicode_json_on_ascii_windows_stream(tmp_path, monkeypatch, fails):
    import contextlib
    import io
    from action_tracker import cli
    from action_tracker.exporting import difference_whitelist as module
    registry = registry_file(tmp_path)
    expected = {'reason': '来源Ø和中文备注', 'status': 'TRACKED_PENDING'}
    def check(*args, **kwargs):
        if fails:
            raise ValueError(expected['reason'])
        return expected
    monkeypatch.setattr(module, 'check_current_conflicts', check)
    stdout, stderr = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(stdout, encoding='ascii', write_through=True)
    err = io.TextIOWrapper(stderr, encoding='ascii', write_through=True)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(['export-difference-whitelist', '--registry', str(registry), '--check-current', 'unused.db'])
    assert code == (2 if fails else 0)
    payload = json.loads((stderr if fails else stdout).getvalue().decode('ascii'))
    assert payload['reason'] == expected['reason']
    assert payload['status'] == ('BLOCKED' if fails else 'TRACKED_PENDING')


def test_readonly_registry_view_is_also_ascii_safe(tmp_path):
    import contextlib
    import io
    from action_tracker import cli
    item = entry(); item['reason'] = '来源Ø'
    p = registry_file(tmp_path, [item])
    raw = io.BytesIO(); output = io.TextIOWrapper(raw, encoding='ascii', write_through=True)
    with contextlib.redirect_stdout(output):
        assert cli.main(['export-difference-whitelist', '--registry', str(p), '--sku', '1']) == 0
    assert json.loads(raw.getvalue().decode('ascii'))['entries'][0]['reason'] == item['reason']
