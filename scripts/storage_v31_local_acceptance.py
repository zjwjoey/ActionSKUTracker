"""LOCAL_ONLY: real Kopia, generated tiny SQLite, separate-process lifecycle.

Requires an exclusive new root. Never opens production data or cloud services.
The test policy/grant is explicitly synthetic-only, not production approval.
"""
import argparse
from dataclasses import asdict, replace
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
from test_capture_lifecycle import environment, approve
from action_tracker.operations import capture_lifecycle as c
from action_tracker.operations.backup_health import health
from action_tracker.operations.backup_sets import read_json, write_immutable, sha256_file
from action_tracker.operations.kopia_backend import BackendConfig, BackendCredentials, KopiaBackend


def write(path, value):
    write_immutable(path, value)


def inventory(root):
    return [{'path': p.relative_to(root).as_posix(), 'bytes': p.stat().st_size}
            for p in root.rglob('*') if p.is_file()]


def child(args):
    root = args.root
    raw = read_json(root / 'operations-config.json')
    config = BackendConfig(**raw['backend'])
    backend = KopiaBackend(config, BackendCredentials(password=os.environ['ACTION_KOPIA_LOCAL_PASSWORD']),
        root / 'sessions' / uuid.uuid4().hex)
    backend.connect()
    try:
        if args.interrupt:
            original = c._register_workspace
            def crash(value):
                original(value)
                os._exit(77)  # emulate loss of this process, not machine reboot
            c._register_workspace = crash
        grant = read_json(root / 'SYNTHETIC_ONLY_GRANT.json') if args.retire else None
        result = c.cycle(raw, backend, args.run_id, grant=grant, retire=args.retire)
        print(json.dumps(result))
    finally:
        backend.disconnect()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--kopia', type=Path)
    p.add_argument('--child', action='store_true')
    p.add_argument('--run-id')
    p.add_argument('--retire', action='store_true')
    p.add_argument('--interrupt', action='store_true')
    args = p.parse_args()
    if args.child:
        child(args)
        return
    root = args.root.absolute()
    if root.exists():
        raise RuntimeError('NEW_ISOLATED_ROOT_REQUIRED')
    root.mkdir(parents=True)
    (root / 'sessions').mkdir()
    raw, _ = environment.__wrapped__(root)
    raw['contract'] = 'BACKUP_OPERATIONS_CONFIG_V1'
    raw['schedule'] = {'enabled': False, 'trigger': 'daily', 'at_time': '03:00'}
    password = secrets.token_urlsafe(48)
    cfg = BackendConfig(enabled=True, provider='filesystem', repository_path=str(root / 'repository'), cli_path=str(args.kopia))
    backend = KopiaBackend(cfg, BackendCredentials(password=password), root / 'sessions' / 'create')
    created = backend.create_repository(creation_authorized=True)
    cfg = replace(cfg, expected_repository_id=created['repository_id'])
    backend.config = cfg
    raw['backend'] = asdict(cfg)
    raw['capture']['code_version'] = 'STORAGE_V31_SYNTHETIC_LOCAL_ACCEPTANCE'
    raw['local_password_dpapi_path'] = str(root / 'private-test-password/test-password.dpapi')
    backend.disconnect()
    write(root / 'operations-config.json', raw)
    # No real password is involved. Preserve only a DPAPI-encrypted test secret;
    # child processes receive the generated test password through private env.
    spec = importlib.util.spec_from_file_location('v3_local', REPO / 'scripts/storage_v3_local_acceptance.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    private = root / 'private-test-password'
    from action_tracker.operations.kopia_connectivity import _private_workspace
    _private_workspace(private)
    (private / 'test-password.dpapi').write_bytes(module.dpapi_protect(password.encode()))
    env = dict(os.environ, ACTION_KOPIA_LOCAL_PASSWORD=password,
               PYTHONPATH=str(REPO / 'src'), PYTHONIOENCODING='utf-8')
    results = []
    def invoke(run_id, *extra, expected=0):
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child',
            '--root', str(root), '--run-id', run_id, *extra], cwd=REPO, env=env,
            capture_output=True, encoding='utf-8', timeout=600)
        if completed.returncode != expected:
            # Tracebacks are local test diagnostics; no credentials in args or reports.
            raise RuntimeError(f'CHILD_FAILED_{run_id}_{completed.returncode}: ' + completed.stderr[-3000:])
        return json.loads(completed.stdout) if completed.stdout.strip() else {'exit_code': completed.returncode}
    invoke('cycle-1')
    grant = approve(raw, type('Identity', (), {'config': cfg})(), 'cycle-1', root)
    write(root / 'SYNTHETIC_ONLY_GRANT.json', grant)
    for index in range(1, 4):
        run_id = f'cycle-{index}'
        if index > 1:
            # Different synthetic facts, same valid export_sync relationship.
            for name, path in raw['capture']['projections'].items():
                Path(path).write_bytes(f'{name} artificial projection version {index}'.encode())
            with sqlite3.connect(raw['capture']['source']) as db:
                db.execute('UPDATE products SET value=? WHERE sku=?', (f'artificial version {index}', '1001'))
                db.execute('INSERT INTO commit_batches VALUES(?,?,?)',
                    (f'head{index}', 'COMMITTED', f'2026-10-15T00:0{index}:00Z'))
                db.execute('INSERT INTO export_sync VALUES(?,?,?,?,?,?,?,?)',
                    (f'head{index}', 'SUCCESS', 'SUCCESS', 'SUCCESS',
                     *(sha256_file(Path(raw['capture']['projections'][k])) for k in ('master', 'known', 'offline')), 'SUCCESS'))
        if index == 2:
            invoke(run_id, '--interrupt', expected=77)
            value = c.manifest(raw['operations_root'], run_id)
            for kind, directory in [('admission', Path(raw['operations_root']) / 'capture-admission'),
                                    ('lifecycle', c._directory(raw['operations_root'], run_id))]:
                lock = directory / 'daily-run.lock'
                c.reconcile_lock(raw, run_id, kind=kind, expected_lock_sha256=sha256_file(lock),
                    requested_by='AUTHORIZED_ISOLATED_TEST_OWNER')
        result = invoke(run_id, '--retire')
        repeated = invoke(run_id, '--retire')
        assert result['status'] == 'RETIRED' and repeated['reused'] is True
        rows = inventory(root)
        value = c.manifest(raw['operations_root'], run_id)
        workspace = Path(value['workspace'])
        results.append({'run_id': run_id, 'result': result, 'repeated': repeated,
            'workspace_files': inventory(workspace), 'workspace_bytes': c._usage(workspace),
            'all_files_after_round': len(rows), 'all_logical_bytes_after_round': sum(x['bytes'] for x in rows),
            'repository_bytes_after_round': c._usage(root / 'repository'),
            'operations_bytes_after_round': c._usage(Path(raw['operations_root'])),
            'workspace_database_files_remaining': len(list(workspace.rglob('*.sqlite3'))),
            'restore_report': str(c._directory(raw['operations_root'], run_id) / 'restore.json')})
    # Fresh independent process/session already restores without reading DPAPI.
    # This proves process/environment portability, not another Windows account.
    status = health(raw)
    assert status['unfinished_capture_count'] == 0
    clean_env = dict(env)
    clean_env.pop('ACTION_KOPIA_LOCAL_PASSWORD', None)
    clean_env.pop('PYTHONPATH', None)
    private_cli = subprocess.run([sys.executable, '-m', 'action_tracker.operations.backup_cli',
        '--config', str(root / 'operations-config.json'), 'capture-cycle', '--run-id', 'cycle-3',
        '--grant', str(root / 'SYNTHETIC_ONLY_GRANT.json'), '--retire'],
        cwd=REPO / 'src', env=clean_env, capture_output=True, encoding='utf-8', timeout=120)
    if private_cli.returncode or json.loads(private_cli.stdout).get('reused') is not True:
        raise RuntimeError('NONINTERACTIVE_PRIVATE_DPAPI_CLI_FAILED: ' + private_cli.stdout[-1000:])
    status = health(raw)
    reader = KopiaBackend(cfg, BackendCredentials(password=password), root / 'sessions' / 'final-readonly-check')
    try:
        reader.connect(readonly=True)
        snapshots = reader.list_snapshots()
        assert len(snapshots) == 3
        assert {s['snapshot_id'] for s in snapshots} == {r['result']['snapshot_id'] for r in results}
        final_usage = reader.storage_usage()
    finally:
        reader.disconnect()
    write(root / 'THREE_RUN_ACCEPTANCE.json', {'status': 'PASS', 'synthetic_only': True,
        'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'repository_id': cfg.expected_repository_id, 'results': results,
        'process_interrupt_and_dead_lock_reconciliation': 'PASS_CYCLE_2',
        'separate_process_password_supply_without_dpapi': 'PASS_GENERATED_TEST_PASSWORD_ONLY',
        'noninteractive_private_dpapi_cli_without_password_environment': 'PASS_GENERATED_TEST_PASSWORD_ONLY',
        'cross_account_or_cross_device': 'NOT_TESTED', 'real_repository_password_offline_custody': 'PENDING',
        'production_mutations': 0, 'historical_mutations': 0, 'cloud_calls': 0,
        'health': status, 'final_kopia_usage': final_usage,
        'snapshot_count_after_repeated_runs': len(snapshots), 'all_final_files': inventory(root)})
    print(json.dumps({'status': 'PASS', 'root': str(root), 'rounds': 3}))


if __name__ == '__main__':
    main()
