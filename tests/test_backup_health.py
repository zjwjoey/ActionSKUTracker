"""CI_SAFE: read-only local status, no credentials or external connections."""
import pytest
from action_tracker.operations.backup_health import health, validate_policy
from action_tracker.operations.storage_foundation import StorageError
from action_tracker.operations.backup_cli import main
from test_backup_cli import configuration


def test_health_disabled_creates_no_operations_or_credentials(configuration):
    raw, _ = configuration
    report = health(raw)
    assert report['status'] == 'DISABLED'
    assert report['notification_sent'] is False
    assert report['repository_live_status'] == 'NOT_PROBED'
    from pathlib import Path
    assert not Path(raw['operations_root']).exists()


def test_missing_backup_and_expired_credential_are_machine_readable_warnings(configuration):
    raw, _ = configuration
    raw['policy'] = {'enabled': True}
    raw['backend'] = {'provider': 'oss'}
    raw['operations_policy'] = {'contract': 'BACKUP_OPERATIONS_POLICY_V1', 'approved': False,
        'credentials_expires_at': '2020-01-01T00:00:00+00:00'}
    report = health(raw)
    assert report['status'] == 'WARNING'
    assert report['suggested_exit_code'] == 1
    assert {'CREDENTIALS_EXPIRED', 'BACKUP_MISSING_OR_OVERDUE', 'RESTORE_PROOF_MISSING_OR_OVERDUE'} <= set(report['warnings'])


@pytest.mark.parametrize('field,value', [('approved', 'true'), ('core_interval_hours', 0),
    ('daily_versions', True), ('capacity_warning_percent', True), ('capacity_warning_percent', 101)])
def test_policy_is_strict_and_does_not_approve_recommendations(field, value):
    with pytest.raises(StorageError):
        validate_policy({'contract': 'BACKUP_OPERATIONS_POLICY_V1', field: value}, {})


def test_disabled_capture_cli_never_loads_credentials(configuration, monkeypatch):
    raw, path = configuration
    from action_tracker.operations import kopia_backend
    def forbidden(*args, **kwargs): pytest.fail('disabled command accessed credentials')
    monkeypatch.setattr(kopia_backend.BackendCredentials, 'from_environment', forbidden)
    assert main(['--config', str(path), 'capture-cycle', '--run-id', 'first']) == 0


def test_pre_job_credentials_failure_is_persisted_for_health(configuration, tmp_path, monkeypatch, capsys):
    import json
    from action_tracker.operations import backup_cli as cli
    raw, path = configuration
    raw.update(policy={'enabled': True}, backend={'enabled': True, 'provider': 'filesystem',
        'repository_path': str(tmp_path / 'repo'), 'expected_repository_id': 'a'*64})
    path.write_text(json.dumps(raw), encoding='utf-8')
    def fail(_): raise StorageError('KOPIA_DPAPI_CURRENT_USER_UNAVAILABLE')
    monkeypatch.setattr(cli, 'execute', fail)
    assert cli.main(['--config', str(path), 'capture-cycle', '--run-id', 'before-job']) == 2
    report = health(raw)
    assert report['latest_failed_job']['reason'] == 'KOPIA_DPAPI_CURRENT_USER_UNAVAILABLE'
    assert report['latest_failed_job']['run_id'] == 'before-job'
    assert report['suggested_exit_code'] == 2


def test_status_write_failure_preserves_success_result_and_nonzero_exit(configuration, monkeypatch, capsys):
    import json
    from action_tracker.operations import backup_cli as cli
    _, path = configuration
    monkeypatch.setattr(cli, 'execute', lambda _: {'status': 'SNAPSHOT_VERIFIED'})
    def fail(*args): raise OSError('never-log-arbitrary-secret')
    monkeypatch.setattr(cli, '_record_invocation', fail)
    assert cli.main(['--config', str(path), 'capture-cycle', '--run-id', 'test']) == 2
    value = json.loads(capsys.readouterr().out)
    assert value['result']['status'] == 'SNAPSHOT_VERIFIED'
    assert value['reason'] == 'BACKUP_STATUS_FILE_WRITE_FAILED'
    assert 'never-log' not in json.dumps(value)


def test_warning_health_does_not_create_a_failed_invocation(configuration, tmp_path, capsys):
    import json
    raw, path = configuration
    raw.update(policy={'enabled': True}, backend={'enabled': True, 'provider': 'filesystem',
        'repository_path': str(tmp_path / 'repo'), 'expected_repository_id': 'a'*64})
    (tmp_path / 'repo').mkdir()
    path.write_text(json.dumps(raw), encoding='utf-8')
    assert main(['--config', str(path), 'health']) == 1
    from pathlib import Path
    assert not Path(raw['operations_root']).exists()


def test_mixed_root_is_rejected_before_capacity_scan(configuration, monkeypatch):
    from pathlib import Path
    from action_tracker.operations import backup_health
    raw, _ = configuration
    root = Path(raw['operations_root']); root.mkdir()
    (root / 'unowned-data').mkdir()
    def forbidden(*args): pytest.fail('mixed root was recursively scanned')
    monkeypatch.setattr(backup_health, '_usage', forbidden)
    with pytest.raises(StorageError, match='OPERATIONS_ROOT_UNOWNED_CONTENT'):
        backup_health.health(raw)


def test_filesystem_root_is_never_scanned(configuration, tmp_path, monkeypatch):
    from action_tracker.operations import backup_health
    raw, _ = configuration
    raw['operations_root'] = tmp_path.anchor
    def forbidden(*args): pytest.fail('filesystem root was recursively scanned')
    monkeypatch.setattr(backup_health, '_usage', forbidden)
    with pytest.raises(StorageError):
        backup_health.health(raw)
