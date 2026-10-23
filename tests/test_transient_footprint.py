"""CI_SAFE: task ownership and bounded serialization on generated files only."""
import hashlib
import json
from pathlib import Path

import pytest

from action_tracker.cli import build_parser
from action_tracker.database.integration import snapshot_digest
from action_tracker.operations.storage_cli import run_storage_command
from action_tracker.operations.storage_foundation import StorageError
from action_tracker.snapshot import _write_json_atomic, write_snapshot


def command(tmp_path, *extra):
    return build_parser().parse_args([
        'storage-foundation', 'task-workspace', '--scratch-root', str(tmp_path / 'scratch'),
        '--owner-task', 'fixture', '--run-id', 'r1', *map(str, extra),
    ])


def test_task_files_share_one_owned_workspace_and_resume_does_not_grow_journal(tmp_path):
    created = run_storage_command(command(tmp_path))
    task = Path(created['task_dir'])
    assert all((task / name).is_dir() for name in ('scripts', 'data', 'debug'))
    for relative, value in [('scripts/task.py', b'# fixture'), ('data/candidates.json', b'[]')]:
        args = command(tmp_path, '--task-dir', task, '--register', relative,
                       '--source', 'generated-fixture', '--expected-bytes', len(value))
        run_storage_command(args)
        (task / relative).write_bytes(value)
        complete = command(tmp_path, '--task-dir', task, '--complete', relative)
        run_storage_command(complete)
        journal = (task / 'lifecycle.jsonl').read_bytes()
        assert run_storage_command(args)['reused']
        assert run_storage_command(complete)['reused']
        assert (task / 'lifecycle.jsonl').read_bytes() == journal
    status = run_storage_command(command(tmp_path, '--task-dir', task))
    assert status['registered_files'] == 2 and status['completed_bytes'] == 11
    assert status['automatic_cleanup'] is False
    (task / 'data/candidates.json').write_bytes(b'[1]')
    with pytest.raises(StorageError, match='SCRATCH_IDENTITY_CHANGED'):
        run_storage_command(command(tmp_path, '--task-dir', task, '--complete', 'data/candidates.json'))


def test_task_budget_ownership_and_registration_conflicts_preserve_files(tmp_path):
    created = run_storage_command(command(tmp_path, '--budget-mib', 1))
    task = Path(created['task_dir'])
    with pytest.raises(StorageError, match='BUDGET_EXCEEDED'):
        run_storage_command(command(tmp_path, '--task-dir', task, '--register', 'data/large',
                                    '--source', 'fixture', '--expected-bytes', 2 * 1024**2))
    with pytest.raises(StorageError, match='OWNERSHIP_INVALID'):
        run_storage_command(command(tmp_path, '--task-dir', task, '--owner-task', 'other'))
    args = command(tmp_path, '--task-dir', task, '--register', 'data/one',
                   '--source', 'fixture', '--expected-bytes', 10)
    run_storage_command(args)
    args.expected_bytes = 11
    with pytest.raises(StorageError, match='REGISTRATION_CHANGED'):
        run_storage_command(args)
    assert not (task / 'data/one').exists()


@pytest.mark.parametrize('relative', ['../escape', 'owner.json', 'scripts', 'data/../owner.json'])
def test_task_rejects_paths_outside_payload_namespaces(tmp_path, relative):
    created = run_storage_command(command(tmp_path))
    with pytest.raises(StorageError):
        run_storage_command(command(tmp_path, '--task-dir', created['task_dir'], '--register', relative,
                                    '--source', 'fixture', '--expected-bytes', 1))


def test_task_refuses_protected_and_unknown_existing_roots(tmp_path):
    with pytest.raises(StorageError, match='PRODUCTION_OVERLAP'):
        run_storage_command(command(tmp_path, '--protected-root', tmp_path))
    assert not (tmp_path / 'scratch').exists()
    (tmp_path / 'scratch').mkdir()
    (tmp_path / 'scratch/unknown').write_bytes(b'keep')
    with pytest.raises((StorageError, OSError)):
        run_storage_command(command(tmp_path))
    assert (tmp_path / 'scratch/unknown').read_bytes() == b'keep'


def test_snapshot_streams_json_and_preserves_roundtrip_without_dumps(tmp_path, monkeypatch):
    data = {'run_report': {'run_id': 'fixture'}, 'listing_raw': {'家居': [{'name': '商品', 'n': 1.5}]},
            'coverage': {'main': True}}
    monkeypatch.setattr(json, 'dumps', lambda *_a, **_k: pytest.fail('whole JSON allocation'))
    directory = write_snapshot({'paths': {'snapshots': tmp_path}}, '2026-10-23', data)
    assert json.loads((directory / 'listing_raw.json').read_text(encoding='utf-8')) == data['listing_raw']
    assert b'\n' not in (directory / 'listing_raw.json').read_bytes()
    assert json.loads((directory / 'coverage.json').read_text()) == data['coverage']


def test_failed_json_serialization_preserves_original(tmp_path):
    path = tmp_path / 'evidence.json'
    path.write_text('{"original":true}')
    cyclic = []; cyclic.append(cyclic)
    with pytest.raises(ValueError):
        _write_json_atomic(path, cyclic, compact=True)
    assert json.loads(path.read_text()) == {'original': True}


def test_snapshot_digest_keeps_legacy_bytes_and_uses_bounded_reads(tmp_path, monkeypatch):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/z').write_bytes(b'fact\x00' * 500_000)
    (tmp_path / 'a').write_bytes(b'other')
    expected = hashlib.sha256()
    for p in sorted(path for path in tmp_path.rglob('*') if path.is_file()):
        expected.update(p.relative_to(tmp_path).as_posix().encode()); expected.update(b'\x00')
        expected.update(p.read_bytes()); expected.update(b'\x00')
    monkeypatch.setattr(Path, 'read_bytes', lambda *_a: pytest.fail('whole-file allocation'))
    assert snapshot_digest(tmp_path) == expected.hexdigest()
