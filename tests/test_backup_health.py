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
