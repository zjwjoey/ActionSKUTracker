"""CI_SAFE: generated small SQLite/projections, local fake storage only."""
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from action_tracker.operations import capture_lifecycle as c
from action_tracker.operations import backup_operations as op
from action_tracker.operations.backup_health import health
from action_tracker.operations.backup_sets import read_json, digest
from action_tracker.operations.storage_foundation import StorageError
from test_backup_operations import Backend
from test_storage_foundation import database, projections


@pytest.fixture
def environment(tmp_path):
    source = database(tmp_path / 'source' / 'primary.db')
    projection = projections(source, tmp_path / 'projections')
    backend = Backend(tmp_path)
    protected = tmp_path / 'protected'; protected.mkdir()
    raw = {'operations_root': str(tmp_path / 'operations'),
        'protected_roots': [str(protected)], 'writer_lock_directory': str(tmp_path / 'state'),
        'policy': {'enabled': True},
        'backend': {'provider': 'filesystem', 'repository_path': str(tmp_path / 'repo')},
        'capture_lifecycle': {'enabled': True, 'minimum_retention_seconds': 0,
            'quarantine_seconds': 0, 'keep_latest_capture': False, 'independent_rollback_required': False},
        'capture': {'source': str(source), 'projections': {k: str(v) for k, v in projection.items()},
            'code_version': 'test-only', 'recovery_owner': 'test-only',
            'retention': {'policy_id': 'synthetic-test-pinned-snapshots', 'recovery_required': True}}}
    return raw, backend


def approve(raw, backend, run_id, tmp_path):
    value = c.manifest(raw['operations_root'], run_id)
    domains = {}
    root = tmp_path / 'reference-domains'; root.mkdir(exist_ok=True)
    for name in c.cleanup.REQUIRED_REFERENCE_DOMAINS:
        path = root / name; path.mkdir(exist_ok=True)
        domains[name] = str(path)
    report_path = c._directory(raw['operations_root'], run_id) / 'restore.json'
    return {'contract': 'CAPTURE_DISPOSITION_POLICY_V1', 'scope': 'NEW_CAPTURE_WORKSPACES_ONLY',
        'allowed_actions': ['QUARANTINE', 'PURGE'],
        'approved_by': 'SYNTHETIC_TEST_OWNER', 'approval_id': 'TEST_ONLY_NOT_PRODUCTION',
        'instruction_reference': 'user-authorized-new-isolated-test-workspaces',
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'root_token': value['root_token'], 'operations_root': value['operations_root'],
        'source_configuration_sha256': c._scope(raw, backend),
        'policy_sha256': digest(asdict(c.CaptureDispositionPolicy(**raw['capture_lifecycle']))),
        'reference_scope_complete': True, 'unresolved_dynamic_references': 0,
        'reference_domains': domains, 'activation_restore_report_path': str(report_path),
        'activation_restore_report_sha256': read_json(report_path)['document_sha256']}


def test_three_cycles_retire_payload_and_keep_independent_snapshots(environment, tmp_path):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    for run_id in ('first', 'second', 'third'):
        result = c.cycle(raw, backend, run_id, grant=grant, retire=True)
        assert result['status'] == 'RETIRED'
        workspace = Path(c.manifest(raw['operations_root'], run_id)['workspace'])
        assert not list(workspace.rglob('*.sqlite3'))
        assert {p.name for p in workspace.rglob('*') if p.is_file()} == {'owner.json', 'lifecycle.jsonl'}
        assert c.cycle(raw, backend, run_id, grant=grant, retire=True)['reused'] is True
    assert backend.writes == 3
    assert len(backend.snapshots) == 3
    report = health(raw)
    assert report['unfinished_capture_count'] == 0
    assert report['notification_sent'] is False


def test_snapshot_only_or_no_approval_never_allows_disposition(environment):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    with pytest.raises(StorageError, match='APPROVAL_REQUIRED'):
        c.cycle(raw, backend, 'first', retire=True)
    with pytest.raises(StorageError, match='PRIOR_MANAGED'):
        c.cycle(raw, backend, 'second')
    assert Path(c.manifest(raw['operations_root'], 'first')['workspace'], 'capture/primary.sqlite3').exists()


@pytest.mark.parametrize('field,value', [('keep_latest_capture', True), ('independent_rollback_required', True),
                                       ('minimum_retention_seconds', 86400)])
def test_legitimate_retention_blocks_next_cycle(environment, tmp_path, field, value):
    raw, backend = environment
    raw['capture_lifecycle'][field] = value
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'RETAIN_REQUIRED'
    with pytest.raises(StorageError, match='PRIOR_MANAGED'):
        c.cycle(raw, backend, 'second')


def test_uncertain_upload_resumes_without_second_snapshot(environment):
    raw, backend = environment
    backend.fail_after_commit = True
    with pytest.raises(StorageError):
        c.cycle(raw, backend, 'first')
    assert c.cycle(raw, backend, 'first')['status'] == 'DISPOSITION_PENDING'
    assert backend.writes == 1


@pytest.mark.parametrize('change', ['hash', 'manifest', 'reference', 'expiry', 'unknown-file', 'restore-proof'])
def test_changed_evidence_or_scope_never_deletes(environment, tmp_path, change):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    workspace = Path(c.manifest(raw['operations_root'], 'first')['workspace'])
    if change == 'hash': (workspace / 'capture/primary.sqlite3').write_bytes(b'changed')
    elif change == 'manifest':
        path = c._directory(raw['operations_root'], 'first') / 'manifest.json'
        doc = read_json(path); doc['owner_task'] = 'forged'
        import json
        path.write_text(json.dumps(doc), encoding='utf-8')
    elif change == 'reference': Path(grant['reference_domains']['active_tasks'], 'consumer.txt').write_text(str(workspace))
    elif change == 'expiry': grant['expires_at'] = '2020-01-01T00:00:00+00:00'
    elif change == 'unknown-file': (workspace / 'capture/unknown').write_bytes(b'unknown')
    else: (c._directory(raw['operations_root'], 'first') / 'restore.json').write_text('{}')
    with pytest.raises(StorageError):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    assert (workspace / 'capture/primary.sqlite3').exists()


def test_disabled_and_legacy_scope_never_adopt(environment):
    raw, backend = environment
    raw['capture_lifecycle']['enabled'] = False
    assert c.cycle(raw, backend, 'first')['status'] == 'DISABLED'
    assert not Path(raw['operations_root']).exists()
    raw['capture_lifecycle']['enabled'] = True
    old = Path(raw['operations_root']) / 'captures/old'; old.mkdir(parents=True)
    (old / 'primary.sqlite3').write_bytes(b'never adopt')
    with pytest.raises(StorageError, match='LEGACY_CAPTURE'):
        c.cycle(raw, backend, 'first')
    assert (old / 'primary.sqlite3').read_bytes() == b'never adopt'


@pytest.mark.parametrize('action', ['QUARANTINE', 'PURGE'])
def test_mutation_completed_but_receipt_lost_reconciles_without_redelete(environment, tmp_path, monkeypatch, action):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    original = c.cleanup._mutate
    failed = []
    def interrupted(source, target, item, **kw):
        original(source, target, item, **kw)
        if not failed and (target is None) == (action == 'PURGE'):
            failed.append(True)
            raise OSError('synthetic crash after mutation before receipt')
    monkeypatch.setattr(c.cleanup, '_mutate', interrupted)
    with pytest.raises(OSError):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    monkeypatch.setattr(c.cleanup, '_mutate', original)
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'RETIRED'
    assert backend.writes == 1


def test_snapshot_receipt_write_failure_reuses_uploaded_snapshot(environment, monkeypatch):
    raw, backend = environment
    original = op.write_immutable
    failed = []
    def interrupted(path, value):
        if path.name == 'receipt.json' and not failed:
            failed.append(True)
            raise OSError('synthetic receipt failure')
        return original(path, value)
    monkeypatch.setattr(op, 'write_immutable', interrupted)
    with pytest.raises(StorageError): c.cycle(raw, backend, 'first')
    assert c.cycle(raw, backend, 'first')['status'] == 'DISPOSITION_PENDING'
    assert backend.writes == 1


def test_complete_download_report_interruption_resumes_without_redownload(environment, monkeypatch):
    raw, backend = environment
    original = op.write_immutable
    failed = []
    def interrupted(path, value):
        if path.name == 'restore.json' and not failed:
            failed.append(True)
            raise OSError('synthetic report failure')
        return original(path, value)
    monkeypatch.setattr(op, 'write_immutable', interrupted)
    with pytest.raises(OSError): c.cycle(raw, backend, 'first')
    assert c.cycle(raw, backend, 'first')['status'] == 'DISPOSITION_PENDING'
    assert read_json(c._directory(raw['operations_root'], 'first') / 'restore.json')['metrics']['resumed_existing_download']


def test_dead_lock_requires_exact_identity_dead_pid_and_never_writer_lock(environment, monkeypatch):
    import json,os
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    directory = c._directory(raw['operations_root'], 'first')
    lock = directory / 'daily-run.lock'
    lock.write_text(json.dumps({'pid': os.getpid(), 'run_id': 'first', 'command': 'storage-baseline', 'storage_token': 'a'*32}))
    sha = c.sha256_file(lock)
    with pytest.raises(StorageError, match='STILL_ACTIVE'):
        c.reconcile_lock(raw, 'first', kind='lifecycle', expected_lock_sha256=sha, requested_by='test')
    monkeypatch.setattr(op, '_process_alive', lambda _: False)
    with pytest.raises(StorageError, match='OWNER_MISMATCH'):
        c.reconcile_lock(raw, 'first', kind='lifecycle', expected_lock_sha256='0'*64, requested_by='test')
    assert c.reconcile_lock(raw, 'first', kind='lifecycle', expected_lock_sha256=sha, requested_by='test')['writer_lock_changed'] is False
    assert not lock.exists()


def test_restore_corruption_and_low_scratch_never_promote(environment, monkeypatch):
    raw, backend = environment
    original = backend.restore_snapshot
    def damaged(snapshot, target):
        result = original(snapshot, target)
        (target / 'assets/core/primary.sqlite3').write_bytes(b'incomplete')
        return result
    monkeypatch.setattr(backend, 'restore_snapshot', damaged)
    with pytest.raises(StorageError): c.cycle(raw, backend, 'first')
    with pytest.raises(StorageError): c.cycle(raw, backend, 'first')
    assert not (c._directory(raw['operations_root'], 'first') / 'restore.json').exists()
    assert not (c._directory(raw['operations_root'], 'first') / 'disposition-plan.json').exists()
    assert backend.writes == 1


def test_budget_failures_are_before_new_capture(environment):
    raw, backend = environment
    raw['policy']['max_scratch_bytes'] = 1024
    with pytest.raises(StorageError, match='BUDGET'):
        c.cycle(raw, backend, 'first')
    assert not (Path(raw['operations_root']) / 'capture-workspaces').exists()


def test_quarantine_can_rollback_without_overwrite_and_requires_owner_review(environment, tmp_path):
    raw, backend = environment
    raw['capture_lifecycle']['quarantine_seconds'] = 86400
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'DISPOSITION_PENDING'
    result = c.rollback(raw, 'first', requested_by='TEST_ONLY')
    assert result['status'] == 'RETAIN_REQUIRED'
    assert c.rollback(raw, 'first', requested_by='TEST_ONLY')['reused'] is True
    workspace = Path(c.manifest(raw['operations_root'], 'first')['workspace'])
    assert (workspace / 'capture/primary.sqlite3').exists()
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['reason'] == 'CAPTURE_ROLLED_BACK_OWNER_REVIEW_REQUIRED'


def test_current_owner_can_explicitly_release_creation_policy(environment, tmp_path):
    raw, backend = environment
    raw['capture_lifecycle']['keep_latest_capture'] = True
    c.cycle(raw, backend, 'first')
    creation_policy = c.manifest(raw['operations_root'], 'first')['disposition_policy']
    raw['capture_lifecycle']['keep_latest_capture'] = False
    grant = approve(raw, backend, 'first', tmp_path)
    with pytest.raises(StorageError, match='CREATION_POLICY_CHANGED'):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    grant['release_creation_policy_sha256'] = digest(creation_policy)
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'RETIRED'


def test_original_path_reappearing_during_quarantine_prevents_purge(environment, tmp_path, monkeypatch):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    original = c._owned_mutation
    changed = []
    def inject(raw, backend, value, grant, plan, index, source, target, action):
        if action == 'PURGE' and not changed:
            changed.append(True)
            Path(plan['items'][index]['path']).write_bytes(b'new active consumer file')
        return original(raw, backend, value, grant, plan, index, source, target, action)
    monkeypatch.setattr(c, '_owned_mutation', inject)
    with pytest.raises(StorageError, match='ORIGINAL_PATH_REAPPEARED'):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)


def test_resealed_disposition_cannot_target_an_unregistered_external_file(environment, tmp_path):
    import json
    from action_tracker.operations.backup_sets import seal
    raw, backend = environment
    raw['capture_lifecycle']['quarantine_seconds'] = 86400
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    c.cycle(raw, backend, 'first', grant=grant, retire=True)
    outside = tmp_path / 'never-owned.txt'; outside.write_bytes(b'preserve')
    path = c._directory(raw['operations_root'], 'first') / 'disposition-plan.json'
    plan = read_json(path); plan.pop('document_sha256')
    plan['items'][0] = {**c.cleanup.identity(outside), 'sha256': c.sha256_file(outside)}
    path.write_text(json.dumps(seal(plan)), encoding='utf-8')
    with pytest.raises(StorageError, match='OUTSIDE_REGISTRY'):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    assert outside.read_bytes() == b'preserve'


def test_next_run_rechecks_elapsed_retention_before_admission(environment, tmp_path, monkeypatch):
    raw, backend = environment
    raw['capture_lifecycle']['minimum_retention_seconds'] = 10
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'RETAIN_REQUIRED'
    original_datetime = c.datetime
    class Later(original_datetime):
        @classmethod
        def now(cls, tz=None): return original_datetime.now(tz) + timedelta(seconds=11)
    monkeypatch.setattr(c, 'datetime', Later)
    monkeypatch.setattr(c, 'now', lambda: Later.now(timezone.utc).isoformat())
    result = c.cycle(raw, backend, 'second', grant=grant, retire=True)
    assert c._state(c._directory(raw['operations_root'], 'first')) == 'RETIRED'
    assert result['status'] == 'RETAIN_REQUIRED'  # the new capture's clock starts now
    assert backend.writes == 2


def test_cancelled_disposition_needs_explicit_resume(environment, tmp_path):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    job = Path(raw['operations_root']) / 'jobs/first'
    op.request_cancel(job, requested_by='TEST_ONLY')
    with pytest.raises(StorageError, match='BACKUP_CANCELLED'):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    assert health(raw)['next_run_blockers'][0]['reason'] == 'BACKUP_CANCELLED'
    op.resume_cancelled(job, requested_by='TEST_ONLY')
    assert c.cycle(raw, backend, 'first', grant=grant, retire=True)['status'] == 'RETIRED'


def test_retired_workspace_reappearance_is_visible_to_health_and_resume(environment, tmp_path):
    raw, backend = environment
    c.cycle(raw, backend, 'first')
    grant = approve(raw, backend, 'first', tmp_path)
    c.cycle(raw, backend, 'first', grant=grant, retire=True)
    workspace = Path(c.manifest(raw['operations_root'], 'first')['workspace'])
    (workspace / 'unregistered.sqlite3').write_bytes(b'protect unknown bytes')
    assert health(raw)['status'] == 'BLOCKED'
    with pytest.raises(StorageError, match='WORKSPACE_CHANGED'):
        c.cycle(raw, backend, 'first', grant=grant, retire=True)
    assert (workspace / 'unregistered.sqlite3').exists()
