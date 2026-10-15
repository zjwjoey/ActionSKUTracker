"""CI_SAFE: generated encrypted-looking bytes and mocked OS credential APIs."""
import json
import subprocess
import pytest

from action_tracker.operations import kopia_private_credentials as p
from action_tracker.operations.storage_foundation import StorageError


@pytest.fixture
def private(tmp_path, monkeypatch):
    path = tmp_path / 'password.dpapi'; path.write_bytes(b'generated test cipher')
    monkeypatch.setattr(p, 'windows_dpapi_available', lambda: True)
    monkeypatch.setattr(p, '_acl_observation', lambda _: {'current': 'S-1-TEST', 'owner': 'S-1-TEST', 'allowed': ['S-1-TEST', 'S-1-5-18']})
    monkeypatch.setattr(p, '_unprotect', lambda _: 'generated-test-password')
    return path


def test_current_user_private_source_is_local_only(private):
    assert p.local_password(private, provider='filesystem') == 'generated-test-password'
    with pytest.raises(StorageError, match='LOCAL_REPOSITORIES_ONLY'):
        p.local_password(private, provider='oss')


@pytest.mark.parametrize('acl', [
    {'current': 'me', 'owner': 'other', 'allowed': ['me']},
    {'current': 'me', 'owner': 'me', 'allowed': ['me', 'S-1-5-32-545']},
    {'current': 'me', 'owner': 'me', 'allowed': []}])
def test_broad_access_or_other_owner_blocks(private, monkeypatch, acl):
    monkeypatch.setattr(p, '_acl_observation', lambda _: acl)
    with pytest.raises(StorageError, match='PRIVATE_ACL_REQUIRED'):
        p.local_password(private, provider='local')


def test_unavailable_user_profile_is_sanitized(private, monkeypatch):
    def fail(_): raise OSError('must-not-log-secret-value')
    monkeypatch.setattr(p, '_unprotect', fail)
    with pytest.raises(StorageError, match='^KOPIA_DPAPI_CREDENTIAL_UNAVAILABLE$'):
        p.local_password(private, provider='filesystem')


def test_acl_probe_never_inherits_application_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'never-forward')
    monkeypatch.setenv('UNRELATED_SECRET', 'never-forward')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({'current': 'me', 'owner': 'me', 'allowed': ['me']}), '')
    monkeypatch.setattr(p.subprocess, 'run', run)
    assert p._acl_observation(tmp_path / 'password.dpapi')['owner'] == 'me'
    assert 'never-forward' not in str(calls)
    assert 'ACTION_STORAGE_DPAPI_PATH' in calls[0][1]['env']


def test_linked_secret_file_is_not_accepted(private, tmp_path):
    alias = tmp_path / 'alias.dpapi'; alias.hardlink_to(private)
    with pytest.raises(StorageError, match='HARDLINK'):
        p.local_password(alias, provider='local')
