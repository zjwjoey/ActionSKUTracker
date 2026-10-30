"""CI_SAFE: collection identity and repair head tests, no real database."""
import json
import pytest

from action_tracker.database import repository
from action_tracker.exporting.service import _resolve_sqlite_current_source, ExportValidationError


def setup_repo(monkeypatch, *, date='2026-10-26', race=False, observed=True, scope=None):
    collection = {'commit_id': 'collection26', 'run_id': 'daily26', 'run_date': date}
    head = {'commit_id': 'repair27', 'run_id': 'repair27', 'run_date': '2026-10-27'}
    if scope is not None:
        head['commit_scope'] = scope
    class Repo:
        def __init__(self, path): self.reads = 0
        def latest_commit_info(self):
            self.reads += 1
            return {'commit_id': 'concurrent'} if race and self.reads > 1 else head
        def latest_observation_commit_info(self): return collection if observed else None
        def commit_info(self, run_id): return collection if run_id == 'daily26' else head
        def load_current_export_records(self): return [{'sku': '1001', 'name_zh': '修复中文'}]
    monkeypatch.setattr(repository, 'ProductionRepository', Repo)
    from action_tracker.database import integration
    monkeypatch.setattr(integration, 'database_path', lambda cfg: 'unused')


@pytest.mark.parametrize('requested', [None, 'daily26'])
def test_repair_head_does_not_replace_collection_date(monkeypatch, requested):
    setup_repo(monkeypatch)
    result = _resolve_sqlite_current_source({}, export_date='2026-10-26', requested_run_id=requested)
    assert result.kind == 'SQLITE_CURRENT' and result.run_id == 'daily26'
    assert result.source_commit_id == 'repair27'
    assert result.presence_source_commit_id == 'collection26'
    assert result.records[0]['name_zh'] == '修复中文'


@pytest.mark.parametrize('date,requested', [('2026-10-25', None), ('2026-10-27', None), ('2026-10-27', 'repair27')])
def test_other_dates_and_repair_run_are_not_current_collection(monkeypatch, date, requested):
    setup_repo(monkeypatch)
    assert _resolve_sqlite_current_source({}, export_date=date, requested_run_id=requested) is None


def test_concurrent_commit_fails_closed(monkeypatch):
    setup_repo(monkeypatch, race=True)
    with pytest.raises(ExportValidationError, match='HEAD_CHANGED'):
        _resolve_sqlite_current_source({}, export_date='2026-10-26', requested_run_id=None)


def test_new_collection_prevents_current_reconstruction_of_old_day(monkeypatch):
    setup_repo(monkeypatch, date='2026-10-27')
    assert _resolve_sqlite_current_source({}, export_date='2026-10-26', requested_run_id='daily26') is None


def test_repository_observation_query_excludes_repairs_dryrun_and_failed_qa(tmp_path):
    from action_tracker.database.schema import migrate_v2
    from action_tracker.database.connection import connect
    path = tmp_path / 'fixture.sqlite'
    migrate_v2(path, role='PRIMARY')
    with connect(path) as db:
        for sku in ('1000', '1001', '1002'):
            db.execute("INSERT INTO products(official_sku,canonical_id,status) VALUES(?,?,'CURRENT')", (sku, 'ACT000' + sku))
        for i, (count, dry, qa) in enumerate([(2, 0, 'PASS'), (0, 0, 'PASS'), (3, 1, 'PASS'), (3, 0, 'FAIL')]):
            run = 'r' + str(i)
            db.execute('INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES(?,?,?,?,?)',
                       (run, '2026-10-26', 'COMMITTED', qa, dry))
            db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,observation_count,status) VALUES(?,?,?,'2.0.0','now',?,?,'COMMITTED')",
                       ('c'+str(i), run, 'fixture', '2026-10-26T0'+str(i), count))
            for index in range(count):
                db.execute('INSERT INTO observations(run_id,official_sku,observation_date,presence_state,observation_complete) VALUES(?,?,?,?,1)',
                           (run, str(1000 + index), '2026-10-26', 'PRESENT'))
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    repo = repository.ProductionRepository(path)
    assert repo.latest_commit_info()['commit_id'] == 'c3'
    assert repo.latest_observation_commit_info()['commit_id'] == 'c0'


def test_no_complete_observation_cannot_use_repair_head(monkeypatch):
    setup_repo(monkeypatch, observed=False)
    assert _resolve_sqlite_current_source({}, export_date='2026-10-27', requested_run_id=None) is None


def test_partial_head_is_rejected_through_repository_metadata(monkeypatch):
    setup_repo(monkeypatch, scope='POSITIVE_FACTS_ONLY')
    with pytest.raises(ExportValidationError, match='POSITIVE_FACTS_ONLY_NOT_RELEASE_READY'):
        _resolve_sqlite_current_source({}, export_date='2026-10-26', requested_run_id=None)


@pytest.mark.parametrize('payload', ['{', '', '[]', 'null', '{"commit_scope":null}',
    '{"commit_scope":false}', '{"commit_scope":""}', '{"commit_scope":"UNKNOWN"}'])
def test_invalid_scope_evidence_is_never_assumed_full(tmp_path, payload):
    path = _scope_database(tmp_path, payload)
    repo = repository.ProductionRepository(path)
    for read in (repo.latest_commit_info, lambda: repo.commit_info('scope-run'), repo.latest_observation_commit_info):
        with pytest.raises(repository.ProductionRepositoryError, match='COMMIT_SCOPE_EVIDENCE_INVALID'):
            read()


@pytest.mark.parametrize('payload,scope', [(None, 'FULL_OBSERVATION'), ('{}', 'FULL_OBSERVATION'),
    (json.dumps({'commit_scope': 'FULL_OBSERVATION'}), 'FULL_OBSERVATION'),
    (json.dumps({'commit_scope': 'POSITIVE_FACTS_ONLY'}), 'POSITIVE_FACTS_ONLY')])
def test_scope_metadata_and_presence_authority_are_separate(tmp_path, payload, scope):
    repo = repository.ProductionRepository(_scope_database(tmp_path, payload))
    assert repo.latest_commit_info()['commit_scope'] == scope
    assert repo.commit_info('scope-run')['commit_scope'] == scope
    presence = repo.latest_observation_commit_info()
    assert (presence is not None) == (scope == 'FULL_OBSERVATION')


def test_newer_full_scope_with_only_incomplete_observations_cannot_take_presence_head(tmp_path):
    from action_tracker.database.connection import connect
    path = _scope_database(tmp_path, '{}')
    with connect(path) as db:
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('incomplete','2026-10-27','COMMITTED','PASS',0)")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,observation_count,status) VALUES('newer-incomplete','incomplete','fixture','2.0.0','now','zz-newer',1,'COMMITTED')")
        db.execute("INSERT INTO run_evidence(run_id,evidence_json) VALUES('incomplete',?)", (json.dumps({'commit_scope': 'FULL_OBSERVATION'}),))
        db.execute("INSERT INTO observations(run_id,official_sku,observation_date,presence_state,observation_complete) VALUES('incomplete','1001','2026-10-27','PRESENT',0)")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    repo = repository.ProductionRepository(path)
    assert repo.latest_commit_info()['commit_id'] == 'newer-incomplete'
    assert repo.latest_observation_commit_info()['commit_id'] == 'scope-commit'


def _scope_database(tmp_path, payload):
    from action_tracker.database.schema import migrate_v2
    from action_tracker.database.connection import connect
    path = tmp_path / 'scope.sqlite'
    migrate_v2(path, role='PRIMARY')
    with connect(path) as db:
        db.execute("INSERT INTO products(official_sku,canonical_id,status) VALUES('1001','ACT0001001','CURRENT')")
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('scope-run','2026-10-26','COMMITTED','PASS',0)")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,observation_count,status) VALUES('scope-commit','scope-run','fixture','2.0.0','now','now',1,'COMMITTED')")
        db.execute("INSERT INTO observations(run_id,official_sku,observation_date,presence_state,observation_complete) VALUES('scope-run','1001','2026-10-26','PRESENT',1)")
        if payload is not None:
            db.execute('INSERT INTO run_evidence(run_id,evidence_json) VALUES(?,?)', ('scope-run', payload))
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    return path
