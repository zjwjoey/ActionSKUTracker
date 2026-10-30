"""CI_SAFE: collection identity and repair head tests, no real database."""
import pytest

from action_tracker.database import repository
from action_tracker.exporting.service import _resolve_sqlite_current_source, ExportValidationError


def setup_repo(monkeypatch, *, date='2026-10-26', race=False):
    collection = {'commit_id': 'collection26', 'run_id': 'daily26', 'run_date': date}
    head = {'commit_id': 'repair27', 'run_id': 'repair27', 'run_date': '2026-10-27'}
    class Repo:
        def __init__(self, path): self.reads = 0
        def latest_commit_info(self):
            self.reads += 1
            return {'commit_id': 'concurrent'} if race and self.reads > 1 else head
        def latest_observation_commit_info(self): return collection
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
        for i, (count, dry, qa) in enumerate([(2, 0, 'PASS'), (0, 0, 'PASS'), (3, 1, 'PASS'), (3, 0, 'FAIL')]):
            run = 'r' + str(i)
            db.execute('INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES(?,?,?,?,?)',
                       (run, '2026-10-26', 'COMMITTED', qa, dry))
            db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,observation_count,status) VALUES(?,?,?,'2.0.0','now',?,?,'COMMITTED')",
                       ('c'+str(i), run, 'fixture', '2026-10-26T0'+str(i), count))
    repo = repository.ProductionRepository(path)
    assert repo.latest_commit_info()['commit_id'] == 'c3'
    assert repo.latest_observation_commit_info()['commit_id'] == 'c0'
