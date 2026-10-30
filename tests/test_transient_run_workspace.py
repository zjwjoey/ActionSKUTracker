"""CI_SAFE: generated files only; no collector, PRIMARY or network."""
import json
from pathlib import Path

import pytest

from action_tracker.operations.storage_foundation import StorageError
from action_tracker.operations.storage_lifecycle import prepare_run_workspace, record_run_workspace
from action_tracker.localization.service import _report_root


def config(tmp_path):
    production = tmp_path / 'production'
    return {'project_root': production, 'paths': {
        'master': production / 'master.xlsx', 'temp': production / 'runtime' / 'temp',
        'state': production / 'runtime' / 'state', 'backups': production / 'backups'},
        'transient_tasks': {'enabled': True, 'root': tmp_path / 'tasks', 'budget_mib': 1}}


def test_staging_centralized_resume_and_durable_paths_preserved(tmp_path):
    cfg = config(tmp_path)
    report = tmp_path / 'production' / 'reports' / 'r1'
    effective, task = prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    assert effective['paths']['temp'] == task.directory / 'data' / 'runtime_temp'
    assert cfg['paths']['temp'] != effective['paths']['temp']
    for key in ('master', 'state', 'backups'):
        assert cfg['paths'][key] == effective['paths'][key]
    assert _report_root(effective) == _report_root(cfg)
    assert effective['paths']['localization_learning_candidates'] == cfg['paths']['temp'].parent / 'localization' / 'learning_candidates'
    sample = effective['paths']['temp'] / 'candidate.json'
    sample.write_text('{"pending":true}', encoding='utf-8')
    first = record_run_workspace(task)
    resumed, again = prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    assert again.directory == task.directory and resumed['paths']['temp'] == effective['paths']['temp']
    assert sample.read_text(encoding='utf-8') == '{"pending":true}'
    second = record_run_workspace(again)
    assert first['inventory'] != second['inventory']
    assert first['total_bytes'] == second['total_bytes'] == sample.stat().st_size
    assert len(json.loads(Path(second['inventory']).read_text(encoding='utf-8'))['files']) == 1
    assert len(list((tmp_path / 'tasks').glob('task-*'))) == 1
    assert not first['automatic_cleanup']


@pytest.mark.parametrize('enabled', [False, None])
def test_legacy_or_disabled_does_not_create_files(tmp_path, enabled):
    cfg = config(tmp_path)
    if enabled is None:
        cfg.pop('transient_tasks')
    else:
        cfg['transient_tasks']['enabled'] = enabled
    effective, task = prepare_run_workspace(cfg, run_id='r1', report_dir=tmp_path / 'report')
    assert effective is cfg and task is None
    assert not (tmp_path / 'tasks').exists()


@pytest.mark.parametrize('mutation', ['overlap', 'string_enabled', 'budget', 'git'])
def test_invalid_roots_and_configuration_fail_closed(tmp_path, mutation):
    cfg = config(tmp_path)
    if mutation == 'overlap':
        cfg['transient_tasks']['root'] = cfg['project_root'] / 'temp'
    elif mutation == 'string_enabled':
        cfg['transient_tasks']['enabled'] = 'true'
    elif mutation == 'budget':
        cfg['transient_tasks']['budget_mib'] = True
    else:
        (tmp_path / '.git').mkdir()
    with pytest.raises(StorageError):
        prepare_run_workspace(cfg, run_id='r1', report_dir=tmp_path / 'report')


def test_tampered_resume_identity_is_rejected(tmp_path):
    cfg = config(tmp_path)
    report = tmp_path / 'report'
    _, task = prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    owner = task.directory / 'owner.json'
    value = json.loads(owner.read_text(encoding='utf-8'))
    value['run_id'] = 'someone-else'
    owner.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(StorageError, match='OWNERSHIP'):
        prepare_run_workspace(cfg, run_id='r1', report_dir=report)


def test_default_root_override_and_budget_alert_keep_original_bytes(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg['transient_tasks'].pop('root')
    monkeypatch.setenv('ACTION_TRACKER_TASK_ROOT', str(tmp_path / 'central-tasks'))
    effective, task = prepare_run_workspace(cfg, run_id='r1', report_dir=tmp_path / 'report')
    large = effective['paths']['temp'] / 'generated.bin'
    large.write_bytes(b'x' * (1024**2 + 1))
    report = record_run_workspace(task)
    assert task.root == (tmp_path / 'central-tasks').resolve()
    assert report['budget_alert'] and large.stat().st_size == 1024**2 + 1
    assert not report['automatic_cleanup']


def test_resume_rejects_changed_durable_runtime_anchor(tmp_path):
    cfg = config(tmp_path)
    report = tmp_path / 'report'
    prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    cfg['paths']['temp'] = cfg['paths']['temp'].parent / 'different-temp'
    with pytest.raises(StorageError, match='ANCHOR_CHANGED'):
        prepare_run_workspace(cfg, run_id='r1', report_dir=report)


def test_new_default_root_does_not_move_or_strand_existing_resume(tmp_path, monkeypatch):
    from action_tracker.operations import storage_lifecycle as storage
    cfg = config(tmp_path)
    cfg['transient_tasks'].pop('root')
    monkeypatch.delenv('ACTION_TRACKER_TASK_ROOT', raising=False)
    monkeypatch.setattr(storage, 'default_task_root', lambda: tmp_path / 'old-default')
    report = tmp_path / 'report'
    _, original = prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    monkeypatch.setattr(storage, 'default_task_root', lambda: tmp_path / 'new-default')
    _, resumed = prepare_run_workspace(cfg, run_id='r1', report_dir=report)
    assert resumed.directory == original.directory
    assert not (tmp_path / 'new-default').exists()


def test_runner_passes_isolated_config_to_existing_chain(tmp_path, monkeypatch):
    from action_tracker.operations import entry
    cfg = config(tmp_path)
    seen = {}
    monkeypatch.setattr(entry, 'database_path', lambda cfg: tmp_path / 'unused.db')
    def fake_run(self, **kwargs):
        seen['collection'] = self.steps['COLLECTION']()
        return {'state': 'SUCCESS'}
    monkeypatch.setattr(entry.ProductionRunner, 'run', fake_run)
    from action_tracker.orchestrator import daily
    def collect(effective, **kwargs):
        seen['paths'] = effective['paths']
        return {'qa': {'passed': True}, 'commit_status': 'FULL_COMMIT'}
    monkeypatch.setattr(daily, 'run_daily', collect)
    result = entry.run_production(cfg, business_date='2026-10-27')
    assert seen['paths']['temp'].is_relative_to(tmp_path / 'tasks')
    assert seen['paths']['master'] == cfg['paths']['master']
    assert result['transient_workspace']['automatic_cleanup'] is False
