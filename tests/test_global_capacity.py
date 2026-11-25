"""CI_SAFE: generated files only; volume probes are explicitly simulated.

No real quota/VHD, production inventory, network, image or historical cleanup.
"""
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest

from action_tracker.operations import global_capacity as gc
from action_tracker.operations.storage_foundation import StorageError


def gate(tmp_path, monkeypatch, *, limit=4_000_000, discovery=()):
    monkeypatch.delenv('ACTION_TRACKER_TASK_ROOT', raising=False)
    monkeypatch.setattr(gc, 'volume_info', lambda p: {'id': 'generated-test-volume', 'total_bytes': limit})
    return gc.GlobalCapacity(tmp_path / 'control', [tmp_path], write_roots=[tmp_path],
                             discovery=discovery, policy=gc.CapacityPolicy(limit))


@pytest.mark.parametrize('value', [True, 0, -1, 15_000_000_001, '15000000000'])
def test_hard_limit_cannot_be_weakened(value):
    with pytest.raises(StorageError, match='POLICY_INVALID'):
        gc.CapacityPolicy(value)
    assert gc.CapacityPolicy().max_bytes == 15_000_000_000


def test_all_roots_and_unknown_payloads_block_before_any_mutation(tmp_path, monkeypatch):
    old = tmp_path / 'historic'
    old.mkdir()
    payload = old / 'only-recovery.sqlite3'
    payload.write_bytes(b'x' * 800_000)
    g = gate(tmp_path, monkeypatch, limit=1_000_000)
    before = payload.read_bytes()
    assert g.inspect()['reason'] == 'TOTAL_OR_PEAK_EXCEEDS_LIMIT'
    with pytest.raises(gc.CapacityBlocked):
        with g.lease('repair', 1):
            pytest.fail('overfull entry must never execute')
    assert payload.read_bytes() == before
    assert not g.root.exists()


def test_limit_is_shared_across_separate_folders_not_per_folder(tmp_path, monkeypatch):
    first, second = tmp_path / 'business', tmp_path / 'old-repairs'
    first.mkdir()
    second.mkdir()
    (first / 'primary.db').write_bytes(b'a' * 400_000)
    (second / 'recovery.db').write_bytes(b'b' * 400_000)
    monkeypatch.setattr(gc, 'volume_info', lambda p: {'id': 'fixture', 'total_bytes': 1_000_000})
    g = gc.GlobalCapacity(first / 'control', [first, second], write_roots=[first],
                          policy=gc.CapacityPolicy(1_000_000))
    assert g.inspect()['reason'] == 'TOTAL_OR_PEAK_EXCEEDS_LIMIT'
    assert not g.root.exists()


def test_small_usage_on_large_volume_is_not_a_hard_quota(tmp_path):
    g = gc.GlobalCapacity(tmp_path / 'control', [tmp_path], write_roots=[tmp_path],
                          policy=gc.CapacityPolicy(1_000_000))
    assert g.inspect()['reason'] == 'BOUNDED_VOLUME_REQUIRED'
    with pytest.raises(gc.CapacityBlocked, match='BOUNDED_VOLUME'):
        with g.lease('no-vhd', 10):
            pass
    assert not g.root.exists()


def test_actual_bounded_volume_free_space_also_limits_peak(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, 'volume_info', lambda p: {
        'id': 'small-bounded', 'total_bytes': 1_000_000, 'used_bytes': 900_000})
    g = gc.GlobalCapacity(tmp_path / 'control', [tmp_path], write_roots=[tmp_path],
                          policy=gc.CapacityPolicy(4_000_000))
    with pytest.raises(gc.CapacityBlocked, match='VOLUME_PEAK_SPACE_INSUFFICIENT'):
        with g.lease('full-volume', 1000):
            pass
    assert not g.root.exists()


def test_live_bounded_volume_checkpoint_avoids_per_field_recursive_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, 'volume_info', lambda p: {
        'id': 'bounded', 'total_bytes': 4_000_000, 'used_bytes': 100_000})
    g = gc.GlobalCapacity(tmp_path / 'control', [tmp_path], write_roots=[tmp_path],
                          policy=gc.CapacityPolicy(4_000_000))
    with g.lease('fast', 100_000) as lease:
        with monkeypatch.context() as local:
            local.setattr(gc, 'measure', lambda p: pytest.fail('must use OS volume accounting'))
            assert lease.checkpoint()['measurement'] == 'bounded_volume_used'


def test_preexisting_pool_outside_reservation_cannot_write_even_control_records(tmp_path, monkeypatch):
    from action_tracker.operations.backup_pool import BackupPool
    outside = BackupPool(tmp_path / 'outside', initialize=True)
    before = (outside.root / 'index.json').read_bytes()
    managed = tmp_path / 'managed'
    managed.mkdir()
    g = gate(managed, monkeypatch)
    with g.lease('outside-pool', 100_000):
        with pytest.raises(StorageError, match='OUTSIDE_RESERVATION'):
            outside.backup(tmp_path / 'unused.db', task_id='new-task')
    assert (outside.root / 'index.json').read_bytes() == before
    assert not list((outside.root / 'leases').glob('capture-*'))


def test_nested_roots_and_hardlinks_are_counted_once(tmp_path):
    root = tmp_path / 'images'
    root.mkdir()
    original = root / 'original.png'
    original.write_bytes(b'x' * 9000)
    try:
        os.link(original, root / 'same.png')
    except OSError:
        pytest.skip('host cannot create test hardlink')
    result = gc.measure([tmp_path, root])
    assert result['files'] == 1
    assert result['logical_bytes'] == 9000
    assert result['charged_bytes'] >= 9000


def test_wal_and_arbitrary_extensions_are_not_excluded(tmp_path):
    for name in ['primary.db', 'primary.db-wal', 'primary.db-shm', 'untracked.tmp']:
        (tmp_path / name).write_bytes(b'x' * 5000)
    assert gc.measure([tmp_path])['logical_bytes'] == 20_000


def test_discovery_charges_new_old_style_task_folders(tmp_path, monkeypatch):
    anchor = tmp_path / 'old-root'
    anchor.mkdir()
    g = gate(tmp_path / 'managed', monkeypatch, limit=1_000_000,
             discovery=[{'path': str(anchor), 'prefixes': ['Action']}])
    before = g.inspect()['charged_bytes']
    directory = anchor / 'ActionUnregisteredRepair'
    directory.mkdir()
    (directory / 'database.bin').write_bytes(b'x' * 800_000)
    assert g.inspect()['charged_bytes'] > before
    assert g.inspect()['status'] == 'BLOCKED'
    assert not g.root.exists()


def test_inaccessible_inventory_fails_closed_without_cleanup(tmp_path, monkeypatch):
    (tmp_path / 'only-backup').write_bytes(b'keep')
    g = gate(tmp_path, monkeypatch)
    monkeypatch.setattr(gc, 'allocation', lambda p: (_ for _ in ()).throw(PermissionError('locked')))
    with pytest.raises(PermissionError):
        with g.lease('repair', 100):
            pass
    assert (tmp_path / 'only-backup').read_bytes() == b'keep'
    assert not g.root.exists()


def test_bound_output_stops_before_large_file_is_created(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    target = tmp_path / 'generated.bin'
    with pytest.raises(StorageError, match='RESERVATION_EXCEEDED'):
        with g.lease('small', 20_000) as lease:
            lease.write_bytes(target, b'x' * 30_000)
    assert not target.exists()
    assert g._load()['leases']['small']['state'] == 'FAILED_RETAINED'


def test_written_growth_consumes_reservation_instead_of_double_charge(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    with g.lease('growth', 80_000) as lease:
        before = g.inspect()
        lease.write_bytes(tmp_path / 'payload.bin', b'x' * 40_000)
        after = g.inspect()
        assert after['outstanding_bytes'] < before['outstanding_bytes']
        assert after['projected_bytes'] <= before['projected_bytes'] + 16384
    assert not g._load()['leases']
    assert (tmp_path / 'payload.bin').exists()


def test_parallel_admission_reserves_the_peak_before_writers_start(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch, limit=2_000_000)
    release = Event()
    started = [Event() for _ in range(4)]
    def worker(number):
        try:
            with g.lease('worker-' + str(number), 700_000, targets=[tmp_path / str(number)]):
                started[number].set()
                assert release.wait(20)
                return 'admitted'
        except gc.CapacityBlocked:
            started[number].set()
            return 'blocked'
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, n) for n in range(4)]
        try:
            assert all(event.wait(20) for event in started)
            report = g.inspect()
            assert report['outstanding_bytes'] == 1_400_000
            assert report['projected_bytes'] <= 2_000_000
        finally:
            release.set()
        results = [f.result() for f in futures]
    assert results.count('admitted') == 2
    assert results.count('blocked') == 2


def test_overlapping_writers_cannot_borrow_each_others_reservations(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    with g.lease('first', 100_000, targets=[tmp_path / 'data']):
        with pytest.raises(StorageError, match='ALREADY_RESERVED'):
            with g.lease('other', 100_000, targets=[tmp_path / 'data' / 'child']):
                pass


def test_failed_task_keeps_payload_and_explicit_resume_reuses_lease(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError):
        with g.lease('resume', 100_000) as lease:
            lease.write_bytes(tmp_path / 'candidate.json', b'candidate')
            raise RuntimeError('injected failure')
    before = g.inspect()['charged_bytes']
    with pytest.raises(StorageError, match='RESUME_REQUIRED'):
        with g.lease('resume', 100_000):
            pass
    with g.lease('resume', 100_000, resume=True):
        assert (tmp_path / 'candidate.json').read_bytes() == b'candidate'
    assert g.inspect()['charged_bytes'] <= before + 4096
    assert not g._load()['leases']


def test_tampered_ledger_and_changed_scope_are_not_silently_reset(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    with g.lease('seed', 30_000):
        pass
    altered = gc.GlobalCapacity(g.root, [tmp_path], write_roots=[tmp_path / 'different'], policy=g.policy)
    with pytest.raises(StorageError, match='SCOPE_OR_POLICY_CHANGED'):
        altered.inspect()
    value = json.loads(g.ledger.read_text())
    value['leases']['fake'] = {}
    g.ledger.write_text(json.dumps(value))
    with pytest.raises(StorageError):
        g.inspect()


def test_unknown_interrupted_control_file_is_preserved_and_charged(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    with g.lease('seed', 30_000):
        pass
    abandoned = g.root / '.ledger-unknown.writing'
    abandoned.write_bytes(b'unknown evidence')
    with g.lease('again', 30_000):
        pass
    assert abandoned.read_bytes() == b'unknown evidence'


def test_output_outside_scope_fails_before_creating_file(tmp_path, monkeypatch):
    managed = tmp_path / 'managed'
    managed.mkdir()
    g = gate(managed, monkeypatch)
    with pytest.raises(StorageError, match='OUTSIDE_RESERVATION'):
        with g.lease('bad-path', 30_000) as lease:
            lease.write_bytes(tmp_path / 'escape.bin', b'bad')
    assert not (tmp_path / 'escape.bin').exists()


def test_symbolic_link_is_not_treated_as_empty_or_free_space(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create test symlink')
    with pytest.raises(StorageError, match='REPARSE'):
        gc.measure([tmp_path])


def global_config(tmp_path):
    return {'project_root': tmp_path, 'global_storage': {
        'enabled': True, 'root': str(tmp_path / 'control'), 'roots': [str(tmp_path)],
        'write_roots': [str(tmp_path)], 'peak_bytes': 1_000_000, 'max_bytes': 4_000_000}}


def test_nested_scopes_share_one_reservation_and_temp_environment_is_restored(tmp_path, monkeypatch):
    gate(tmp_path, monkeypatch)
    cfg = global_config(tmp_path)
    before = {k: os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR')}
    args = Namespace(command='export', date='2026-11-25')
    with gc.command_scope(cfg, args):
        with gc.operation_scope(cfg, task_id='nested') as nested:
            assert len(nested.gate._load()['leases']) == 1
            assert Path(os.environ['TEMP']).is_relative_to(tmp_path / 'control')
    assert {k: os.environ.get(k) for k in before} == before


def test_export_temp_uses_existing_task_workspace_and_nested_cli_keeps_it(tmp_path, monkeypatch):
    gate(tmp_path, monkeypatch)
    cfg = global_config(tmp_path)
    project = tmp_path / 'business'
    cfg.update(project_root=project, paths={
        'master': project / 'master.xlsx', 'temp': project / 'temp'})
    args = Namespace(command='export', date='2026-11-25')
    with gc.command_scope(cfg, args) as first:
        assert first['paths']['temp'].is_relative_to(tmp_path / 'control/tasks')
        assert first['_transient_workspace']['owner_task'] == 'cli:export'
        with gc.command_scope(first, args) as second:
            assert first['paths']['temp'] == second['paths']['temp']
        assert len(list((tmp_path / 'control/tasks').glob('task-*'))) == 1
    assert not (project / 'master.xlsx').exists()


def test_external_policy_context_still_routes_the_loaded_cli_temp(tmp_path, monkeypatch):
    gate(tmp_path, monkeypatch)
    policy = global_config(tmp_path)
    cfg = {**policy, 'project_root': tmp_path / 'business', 'paths': {
        'master': tmp_path / 'business/master.xlsx', 'temp': tmp_path / 'business/temp'}}
    args = Namespace(command='export', date='2026-11-25')
    with gc.command_scope(policy, args):
        with gc.command_scope(cfg, args) as loaded:
            assert loaded['paths']['temp'].is_relative_to(tmp_path / 'control/tasks')
            assert Path(os.environ['TEMP']) == loaded['paths']['temp']


def test_status_cli_is_read_only_even_when_legacy_files_exceed_limit(tmp_path, capsys):
    from action_tracker.cli import main
    cfg = global_config(tmp_path)
    (tmp_path / 'legacy.db').write_bytes(b'x' * 4_000_000)
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps(cfg, default=str))
    assert main(['--storage-policy', str(policy), 'storage-capacity']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['reason'] == 'TOTAL_OR_PEAK_EXCEEDS_LIMIT'
    assert not (tmp_path / 'control').exists()


def test_raw_pool_cli_cannot_bypass_global_entry_gate(tmp_path, capsys):
    from action_tracker.cli import main
    cfg = global_config(tmp_path)
    (tmp_path / 'legacy.db').write_bytes(b'x' * 4_000_000)
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps(cfg, default=str))
    assert main(['--storage-policy', str(policy), 'backup-pool',
                 '--root', str(tmp_path / 'pool'), 'init']) == 2
    assert not (tmp_path / 'pool').exists()


def test_global_policy_is_opt_in_for_unchanged_callers(tmp_path):
    assert gc.configured_capacity({'project_root': tmp_path}) is None
    with gc.operation_scope({'project_root': tmp_path}, task_id='legacy') as result:
        assert result is None


@pytest.mark.parametrize('section,key', [('browser', 'profile_dir'), ('images', 'asset_root'),
                                       ('storage', 'db_path'), ('paths', 'exports')])
def test_native_writer_config_cannot_redirect_outside_the_global_scope(tmp_path, section, key):
    managed = tmp_path / 'managed'
    cfg = global_config(managed)
    cfg[section] = {key: str(tmp_path / 'outside')}
    with pytest.raises(StorageError, match='CONFIGURED_WRITER_OUTSIDE_SCOPE'):
        gc.configured_capacity(cfg)
    assert not managed.exists()


def test_crashed_process_retains_reservation_and_resume_does_not_copy_payload(tmp_path, monkeypatch):
    g = gate(tmp_path, monkeypatch)
    program = '''
import os,sys
from pathlib import Path
from action_tracker.operations import global_capacity as g
p=Path(sys.argv[1]);g.volume_info=lambda p:{'id':'generated-test-volume','total_bytes':4000000}
gate=g.GlobalCapacity(p/'control',[p],write_roots=[p],policy=g.CapacityPolicy(4000000))
with gate.lease('crash',100000):
 (p/'only-recovery.bin').write_bytes(b'keep-original')
 os._exit(23)
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(gc.__file__).parents[2])}
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path)], env=env,
                            capture_output=True, timeout=30)
    assert result.returncode == 23, result.stderr
    assert g._load()['leases']['crash']['state'] == 'RUNNING'
    with g.lease('crash', 100_000, resume=True):
        assert (tmp_path / 'only-recovery.bin').read_bytes() == b'keep-original'
    assert not g._load()['leases']
    assert len(list(tmp_path.glob('*.bin'))) == 1


def test_daily_temp_is_enabled_by_global_policy_and_resume_reuses_binding(tmp_path, monkeypatch):
    from action_tracker.operations.storage_lifecycle import prepare_run_workspace
    gate(tmp_path, monkeypatch)
    cfg = global_config(tmp_path)
    project = tmp_path / 'business'
    cfg.update(project_root=project, paths={
        'master': project / 'master.xlsx', 'state': project / 'state', 'temp': project / 'temp'})
    with gc.operation_scope(cfg, task_id='temp'):
        first, one = prepare_run_workspace(cfg, run_id='same-run', report_dir=project / 'reports')
        second, two = prepare_run_workspace(cfg, run_id='same-run', report_dir=project / 'reports')
        assert one.directory == two.directory
        assert first['paths']['temp'] == second['paths']['temp']
        assert one.directory.is_relative_to(tmp_path / 'control' / 'tasks')
        assert len(list((tmp_path / 'control/tasks').glob('task-*'))) == 1
    assert not (project / 'master.xlsx').exists()


def test_global_daily_cannot_disable_temp_management(tmp_path, monkeypatch):
    from action_tracker.operations.storage_lifecycle import prepare_run_workspace
    gate(tmp_path, monkeypatch)
    cfg = {**global_config(tmp_path), 'transient_tasks': {'enabled': False}}
    with gc.operation_scope(cfg, task_id='disabled-temp'):
        with pytest.raises(StorageError, match='TRANSIENT_WORKSPACE_REQUIRED'):
            prepare_run_workspace(cfg, run_id='r', report_dir=tmp_path / 'report')
    assert not (tmp_path / 'report').exists()


def test_legacy_temp_environment_cannot_escape_the_global_reservation(tmp_path, monkeypatch):
    from action_tracker.operations.storage_lifecycle import prepare_run_workspace
    managed = tmp_path / 'managed'
    managed.mkdir()
    gate(managed, monkeypatch)
    cfg = global_config(managed)
    cfg.update(paths={'temp': managed / 'temp'}, project_root=managed / 'business')
    escape = tmp_path / 'old-unmanaged-tasks'
    monkeypatch.setenv('ACTION_TRACKER_TASK_ROOT', str(escape))
    with gc.operation_scope(cfg, task_id='temp-escape'):
        with pytest.raises(StorageError, match='OUTSIDE_RESERVATION'):
            prepare_run_workspace(cfg, run_id='r', report_dir=managed / 'reports')
    assert not escape.exists()


def test_command_output_cannot_escape_even_for_early_storage_tools(tmp_path, monkeypatch):
    managed = tmp_path / 'managed'
    managed.mkdir()
    gate(managed, monkeypatch)
    cfg = global_config(managed)
    args = Namespace(command='storage-foundation', output=tmp_path / 'escape')
    with pytest.raises(StorageError, match='OUTSIDE_RESERVATION'):
        with gc.command_scope(cfg, args):
            pytest.fail('outside output must not run')
    assert not (tmp_path / 'escape').exists()


def test_normal_cli_checks_capacity_before_runtime_dirs_and_logs(tmp_path, monkeypatch, capsys):
    from action_tracker import cli, config as loader
    cfg = global_config(tmp_path)
    (tmp_path / 'only-backup.db').write_bytes(b'x' * 4_000_000)
    monkeypatch.setattr(loader, 'load_settings', lambda: cfg)
    monkeypatch.setattr(loader, 'ensure_runtime_dirs', lambda cfg: pytest.fail('must block before mkdir'))
    assert cli.main(['status']) == 2
    assert 'TOTAL_OR_PEAK_EXCEEDS_LIMIT' in capsys.readouterr().err


def test_pool_cannot_set_a_subbudget_above_global_limit(tmp_path, monkeypatch):
    from action_tracker.operations.backup_pool import configured_pool
    gate(tmp_path, monkeypatch)
    cfg = global_config(tmp_path)
    cfg['backup_pool'] = {'enabled': True, 'max_bytes': 5_000_000, 'target_bytes': 3_000_000}
    with pytest.raises(StorageError, match='POOL_LIMIT_EXCEEDS_GLOBAL'):
        configured_pool(cfg)
    assert not (tmp_path / 'runtime').exists()


def test_twenty_database_batches_and_resume_share_one_global_baseline(tmp_path, monkeypatch):
    from test_storage_foundation import database
    from test_task_backup import config, update
    from action_tracker.operations.task_backup import task_scope
    from action_tracker.operations.backup_pool import configured_pool
    gate(tmp_path, monkeypatch, limit=8_000_000)
    source = database(tmp_path / 'source.db')
    cfg = config(tmp_path, source)
    cfg['global_storage'] = {**global_config(tmp_path)['global_storage'], 'max_bytes': 8_000_000,
                             'peak_bytes': 4_000_000}
    cfg['backup_pool'].update(max_bytes=6_000_000, target_bytes=4_000_000)
    for n in range(1, 21):
        with task_scope(cfg, task_id='global-twenty', batch_id=str(n)) as task:
            update(source, n)
    with task_scope(cfg, task_id='global-twenty') as resumed:
        assert resumed.ensure()['expected_head'] == 'head21'
    assets = configured_pool(cfg).status()['assets']
    assert sum(a['kind'] == 'database_backup' for a in assets) == 1
    assert not list((tmp_path / 'historic').rglob('*.sqlite3'))
    assert not gc.configured_capacity(cfg)._load()['leases']


def test_raw_capture_resume_reuses_verified_backup_and_rejects_same_head_drift(tmp_path):
    import sqlite3
    from test_storage_foundation import database
    from action_tracker.operations.backup_pool import BackupPool, PoolPolicy
    source = database(tmp_path / 'source.db')
    pool = BackupPool(tmp_path / 'pool', policy=PoolPolicy(8_000_000, 4_000_000), initialize=True)
    first = pool.backup(source, task_id='raw-capture')
    again = pool.backup(source, task_id='raw-capture')
    assert again['reused'] and again['backup_path'] == first['backup_path']
    assert sum(a['kind'] == 'database_backup' for a in pool.status()['assets']) == 1
    with sqlite3.connect(source) as db:
        db.execute('CREATE INDEX outside_index ON products(value)')
    with pytest.raises(StorageError, match='BASELINE_SCHEMA_CHANGED'):
        pool.backup(source, task_id='raw-capture')
    with sqlite3.connect(source) as db:
        db.execute('DROP INDEX outside_index')
    with sqlite3.connect(source) as db:
        db.execute("UPDATE products SET value='outside change without new head'")
    with pytest.raises(StorageError, match='BASELINE_SOURCE_CHANGED'):
        pool.backup(source, task_id='raw-capture')
    assert Path(first['backup_path']).exists()
    assert sum(a['kind'] == 'database_backup' for a in pool.status()['assets']) == 1
