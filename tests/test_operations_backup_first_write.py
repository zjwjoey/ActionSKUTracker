"""CI_SAFE: actual backup/DQ persistence in generated databases; collector stub."""
import sqlite3
from pathlib import Path

import pytest

from action_tracker.operations import entry
from action_tracker.operations.storage_foundation import rehearse_restore, sha256_file
from action_tracker.data_quality.contracts import CollectionMetric
from action_tracker.data_quality.repository import DataQualityRepository


def test_three_operations_runs_backup_before_quality_writes_and_resume_reuses_recovery_point(tmp_path, monkeypatch):
    db = tmp_path / 'generated.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE schema_metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE commit_batches(commit_id TEXT PRIMARY KEY,status TEXT,committed_at TEXT);
            INSERT INTO schema_metadata VALUES('schema_family','ACTION_SQLITE_DATA'),
                ('schema_version','2.0.0'),('database_role','PRIMARY');
        """)
    cfg = {'project_root': tmp_path, 'paths': {'state': tmp_path / 'state',
        'backups': tmp_path / 'backups'}}
    monkeypatch.setattr(entry, 'database_path', lambda _cfg: db)
    monkeypatch.setattr(entry, 'production_preflight', lambda *_a: {'storage_mode': 'SQLITE_PRIMARY'})
    monkeypatch.setattr(entry, 'git_commit_info', lambda: 'generated-test')
    # No fact commit is made in this QA-blocked fixture. Resume still exercises
    # the Operations state/backup boundary before repeating the quality phase.
    monkeypatch.setattr(entry, '_recover_committed_collection', lambda *_a: None)
    events, backups = [], {}
    actual_backup = entry.backup_sqlite

    def backup(source, destination, **kwargs):
        result = actual_backup(source, destination, **kwargs)
        events.append(('BACKUP_VERIFIED', kwargs['run_id']))
        backups[kwargs['run_id']] = result
        return result
    monkeypatch.setattr(entry, 'backup_sqlite', backup)
    from action_tracker.orchestrator import daily

    def collect(_cfg, **kwargs):
        run_id = kwargs['workflow_run_id']
        assert run_id in backups and backups[run_id]['integrity'] == 'PASS'
        assert ('BACKUP_VERIFIED', run_id) in events
        events.append(('DQ_WRITE', run_id))
        repo = DataQualityRepository(db)
        repo.save_metrics([CollectionMetric(run_id=run_id, metric_name='generated-only',
            metric_scope=None, metric_value=1, gate_status='WARN')])
        return {'run_id': run_id, 'business_date': kwargs['business_date'],
            'qa': {'passed': False, 'state': 'BLOCKED'}, 'commit_status': 'QA_BLOCKED'}
    monkeypatch.setattr(daily, 'run_daily', collect)

    for number in range(3):
        result = entry.run_production(cfg, business_date='2026-10-30')
        assert result['state'] == 'BLOCKED'
        run_id = result['run_id']; manifest = backups[run_id]
        backup_path = Path(manifest['backup_path']); original_hash = sha256_file(backup_path)
        with sqlite3.connect(backup_path) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='collection_quality_metrics'").fetchone()
            prior_metrics = conn.execute('SELECT count(*) FROM collection_quality_metrics').fetchone()[0] if exists else 0
        assert prior_metrics == number
        assert len(DataQualityRepository(db, ensure_schema=False).get_metrics()) == number + 1
        before_calls = len(backups)
        resumed = entry.run_production(cfg, business_date='2026-10-30', resume=True, run_id=run_id)
        assert resumed['state'] == 'BLOCKED' and len(backups) == before_calls
        assert sha256_file(backup_path) == original_hash
        assert len(DataQualityRepository(db, ensure_schema=False).get_metrics()) == number + 1
        restore = rehearse_restore(backup_path, manifest, tmp_path / 'restore' / run_id)
        assert restore['integrity'] == restore['foreign_keys'] == 'PASS'
    assert len(backups) == 3 and len(list((tmp_path / 'backups').rglob('*.sqlite3'))) == 3
    assert len([e for e in events if e[0] == 'BACKUP_VERIFIED']) == 3
    assert len([e for e in events if e[0] == 'DQ_WRITE']) == 6
    assert not list((tmp_path / 'restore').rglob('*.sqlite3'))


def test_backup_failure_blocks_quality_writes_before_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, 'database_path', lambda _cfg: tmp_path / 'not-opened.sqlite3')
    monkeypatch.setattr(entry, 'production_preflight', lambda *_a: {})
    monkeypatch.setattr(entry, 'git_commit_info', lambda: 'generated-test')
    def fail_backup(*_a, **_kw):
        raise OSError('injected backup failure')
    monkeypatch.setattr(entry, 'backup_sqlite', fail_backup)
    from action_tracker.orchestrator import daily
    monkeypatch.setattr(daily, 'run_daily', lambda *_a, **_kw: pytest.fail('write-capable collection reached'))
    cfg = {'project_root': tmp_path, 'paths': {'state': tmp_path / 'state', 'backups': tmp_path / 'backups'}}
    result = entry.run_production(cfg, business_date='2026-10-30')
    assert result['state'] == 'FAILED' and 'COLLECTION' not in result['steps']
    assert not list(tmp_path.rglob('*.sqlite3'))
