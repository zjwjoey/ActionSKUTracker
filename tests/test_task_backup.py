"""CI_SAFE: generated SQLite/pool only. No production, browser or credentials."""
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest

from action_tracker.database.connection import connect
from action_tracker.operations.task_backup import (task_scope, TaskBackupManager,
    StorageError, PostCommitEvidenceError, register_policy)
from action_tracker.operations.backup_pool import BackupPool
from test_storage_foundation import database


def config(tmp_path,path):
    return {'project_root':tmp_path,'storage':{'db_path':path},
        'paths':{'backups':tmp_path/'historic','state':tmp_path/'state'},
        'backup_pool':{'enabled':True,'root':'runtime/backup_pool'},'task_backup':{'enabled':True}}


def update(path,number):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE products SET value=? WHERE sku='1001'",(f'value-{number}',))
        db.execute('INSERT INTO commit_batches VALUES(?,?,?)',(f'head{number+1}','COMMITTED',f'2026-11-13T00:00:{number:02}Z'))


def test_twenty_transactions_and_resume_share_one_baseline(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    for number in range(1,21):
        with task_scope(cfg,task_id='twenty',batch_id=f'batch{number}') as task:
            update(source,number)
    pool=BackupPool(tmp_path/'runtime/backup_pool')
    assets=pool.status()['assets']
    assert len([a for a in assets if a['kind']=='database_backup'])==1
    assert not list((tmp_path/'historic').rglob('*.sqlite3'))
    resumed=TaskBackupManager(pool,source,task_id='twenty');report=resumed.ensure()
    assert report['expected_head']=='head21'
    state=resumed.load();assert len(state['transactions'])==20
    intents=[resumed.read_event(resumed.read_event(ref)['intent']) for ref in state['transactions']]
    assert {i['batch_id'] for i in intents}=={f'batch{i}' for i in range(1,21)}
    assert all(i['changes_sha256'] and i['changes'][0]['before_sha256'] for i in intents)
    with sqlite3.connect(report['backup_path']) as base:
        assert base.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'
    assert [a['state'] for a in assets if a['kind']=='restore_test']==['DELETED']


def test_external_head_stops_reuse_without_new_copy(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='first') as task:task.ensure();update(source,1)
    with sqlite3.connect(source) as db:
        db.execute("INSERT INTO commit_batches VALUES('outside','COMMITTED','2099-01-01')")
    with task_scope(cfg,task_id='first'):
        with pytest.raises(StorageError,match='EXTERNAL_HEAD'):update(source,2)
    assert len(list((tmp_path/'runtime/backup_pool/active').glob('*.sqlite3')))==1
    with sqlite3.connect(source) as db:assert db.execute('SELECT value FROM products').fetchone()[0]=='value-1'


def test_failed_transaction_rolls_back_and_keeps_recoverable_base(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='failed') as task:
        with pytest.raises(RuntimeError,match='injected'):
            with connect(source) as db:
                db.execute("UPDATE products SET value='bad'")
                raise RuntimeError('injected')
        result=task.ensure()
    with sqlite3.connect(source) as db:assert db.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'
    assert Path(result['backup_path']).exists()


def test_crash_after_commit_reconciles_durable_intent(tmp_path,monkeypatch):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='receipt') as task:
        task.ensure()
        def fault(*a,**kw):raise OSError('receipt unavailable')
        monkeypatch.setattr(task,'receipt',fault)
        with pytest.raises(PostCommitEvidenceError) as error:update(source,1)
        assert error.value.committed is True
        assert task.load()['pending'] is not None
    resumed=TaskBackupManager(BackupPool(tmp_path/'runtime/backup_pool'),source,task_id='receipt')
    assert resumed.ensure()['expected_head']=='head2'
    assert resumed.load()['pending'] is None
    assert len(resumed.load()['transactions'])==1


def test_task_configuration_is_opt_in_and_requires_pool(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    cfg['task_backup']['enabled']='false'
    with pytest.raises(StorageError):register_policy(cfg,source)
    cfg['task_backup']['enabled']=True;cfg['backup_pool']['enabled']=False
    with pytest.raises(StorageError):register_policy(cfg,source)
    cfg['task_backup']['enabled']=False
    with task_scope(cfg,task_id='off') as task:
        assert task is None
        update(source,1)
    assert not (tmp_path/'runtime/backup_pool').exists()


def test_twenty_real_approved_apply_batches_share_baseline(tmp_path):
    from test_localization_correction_apply import _primary,_patch
    from action_tracker.database.production import apply_approved_localization_patches
    from action_tracker.database.repository import ProductionRepository
    source=_primary(tmp_path);cfg=config(tmp_path,source);head='C1';old='旧描述'
    for number in range(20):
        target=f'审核描述{number}'
        with task_scope(cfg,task_id='cn-review',batch_id=f'apply-{number}'):
            _patch(source,f'p-{number}','description',old,target,base=head)
            result=apply_approved_localization_patches(source,patch_ids=[f'p-{number}'],
                expected_base_commit_id=head,actor='human',run_id=f'apply-{number}')
            head=result['commit_id'];old=target
    task=TaskBackupManager(BackupPool(tmp_path/'runtime/backup_pool'),source,task_id='cn-review')
    assert task.ensure()['expected_head']==head
    assert len(list((task.pool.root/'active').glob('*.sqlite3')))==1
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT description FROM product_localizations WHERE language='zh'").fetchone()[0]==old
        assert db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0]==21
    intents=[task.read_event(task.read_event(ref)['intent']) for ref in task.load()['transactions']]
    assert any(c['table']=='localization_patch_events' for i in intents for c in i['changes'])
    assert all('before_sha256' in c for i in intents for c in i['changes'])


def test_cursor_and_commented_commit_cannot_bypass_journal(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='cursor') as task:
        with connect(source) as db:
            cursor=db.cursor();cursor.execute("UPDATE products SET value='cursor'")
            assert cursor.rowcount==1
            cursor.execute('/* documented transaction */ COMMIT;')
            cursor.execute('SELECT value FROM products');assert cursor.fetchone()[0]=='cursor'
        assert len(task.load()['transactions'])==1


def test_missing_journal_blocks_resume(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='missing') as task:update(source,1)
    Path(task.load()['transactions'][0]['path']).unlink()
    with pytest.raises((StorageError,OSError)):task.ensure()
    assert Path(task.load()['backup_path']).exists()


def test_source_replacement_and_schema_change_are_not_silently_reused(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='schema') as task:
        task.ensure()
        with pytest.raises(StorageError,match='SCHEMA'):
            with connect(source) as db:db.execute('CREATE TABLE unexpected(value TEXT)')
        with sqlite3.connect(source) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='unexpected'").fetchone() is None
        db.close()
    replacement=database(tmp_path/'replacement.db')
    import gc
    gc.collect()  # Synthetic database() uses SQLite's non-closing context API.
    replacement.replace(source)
    with pytest.raises(StorageError,match='IDENTITY'):task.ensure()


def test_unscoped_thread_writes_are_blocked_when_policy_enabled(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    register_policy(cfg,source)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with pytest.raises(StorageError,match='SCOPE_REQUIRED'):workers.submit(update,source,1).result()
    assert not list((tmp_path/'runtime/backup_pool').glob('active/*.sqlite3'))


def test_completed_task_is_verifiable_but_cannot_accept_new_writes(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='finished') as task:
        update(source,1);task.finish();assert task.ensure()['expected_head']=='head2'
        with connect(source) as db:
            db.execute("CREATE TABLE IF NOT EXISTS products(sku TEXT PRIMARY KEY,value TEXT)")
            db.execute("UPDATE products SET value='value-1' WHERE sku='1001'")
        with pytest.raises(StorageError,match='COMPLETED'):update(source,2)
    with pytest.raises(StorageError,match='COMPLETED'):
        with task_scope(cfg,task_id='finished'):update(source,2)
    assert task.load()['state']=='COMPLETED'


def test_legacy_report_without_permanent_restore_is_validated(tmp_path):
    from action_tracker.localization.history_audit import verified_backup,validate_backup_report
    source=database(tmp_path/'source.db')
    report=verified_backup(source,tmp_path/'legacy.sqlite3')
    assert report['restore'] is None
    assert validate_backup_report(source,report)['reused_for_recovery']
    with pytest.raises(ValueError,match='SOURCE_ALIAS'):
        validate_backup_report(source,{**report,'backup':str(source)})
    with task_scope(config(tmp_path,source),task_id='legacy-evidence'):
        upgraded=validate_backup_report(source,report)
        assert upgraded['contract']=='TASK_BACKUP_REPORT_V1'
        update(source,1)
        assert validate_backup_report(source,upgraded)['expected_head']=='head2'
    with sqlite3.connect(source) as db:db.execute("INSERT INTO commit_batches VALUES('changed','COMMITTED','2099-01-01')")
    with pytest.raises(ValueError,match='CURRENT_HEAD'):validate_backup_report(source,report)


def test_task_report_follows_own_commit_chain_and_rejects_other_task(tmp_path):
    from action_tracker.localization.history_audit import verified_backup,validate_backup_report
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='report'):
        report=verified_backup(source,tmp_path/'must-not-create.sqlite3');update(source,1)
        assert validate_backup_report(source,report)['expected_head']=='head2'
    with task_scope(cfg,task_id='other'):
        with pytest.raises(ValueError,match='TASK_MISMATCH'):validate_backup_report(source,report)
    assert not (tmp_path/'must-not-create.sqlite3').exists()


def test_cross_process_resume_keeps_same_baseline(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='process') as task:update(source,1)
    result=subprocess.run([sys.executable,'-B','-c',
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from test_task_backup import config,update; from action_tracker.operations.task_backup import task_scope; "
        "p=Path(sys.argv[2]); ctx=task_scope(config(p.parent,p),task_id='process',batch_id='child'); "
        "ctx.__enter__(); update(p,2); ctx.__exit__(None,None,None)",
        str(Path(__file__).parent),str(source)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert task.ensure()['expected_head']=='head3'
    assert len(list((task.pool.root/'active').glob('*.sqlite3')))==1


def test_operations_resume_uses_task_baseline_after_own_metadata_writes(tmp_path,monkeypatch):
    from test_localization_correction_apply import _primary
    from action_tracker.operations import entry
    from action_tracker.orchestrator import daily
    from action_tracker.database import integration
    source=_primary(tmp_path);cfg=config(tmp_path,source)
    monkeypatch.setattr(entry,'database_path',lambda cfg:source)
    monkeypatch.setattr(integration,'database_path',lambda cfg:source)
    monkeypatch.setattr(entry,'production_preflight',lambda *a:{})
    monkeypatch.setattr(entry,'git_commit_info',lambda:'generated-task-test')
    monkeypatch.setattr(entry,'_recover_committed_collection',lambda *a:None)
    def collect(cfg,**kwargs):
        with connect(source) as db:
            db.execute("INSERT INTO sync_queue(entity_type,entity_id,action,status) VALUES('fixture','id','test','PENDING')")
        return {'run_id':kwargs['workflow_run_id'],'business_date':kwargs['business_date'],
                'qa':{'passed':False},'commit_status':'QA_BLOCKED'}
    monkeypatch.setattr(daily,'run_daily',collect)
    first=entry.run_production(cfg,business_date='2026-11-13')
    assert first['state']=='BLOCKED'
    assert first['steps']['BACKUP']['details']['contract']=='TASK_BACKUP_REPORT_V1'
    second=entry.run_production(cfg,business_date='2026-11-13',resume=True,run_id=first['run_id'])
    assert second['state']=='BLOCKED' and second['backup_pool']['status']=='PASS'
    assert len(list((tmp_path/'runtime/backup_pool/active').glob('*.sqlite3')))==1


def test_task_protection_does_not_authorize_unapproved_apply(tmp_path):
    from test_localization_correction_apply import _primary,_patch
    from action_tracker.database.production import apply_approved_localization_patches,ProductionDatabaseError
    source=_primary(tmp_path);_patch(source,'not-approved','name','旧名','不应写入')
    with connect(source) as db:db.execute("DELETE FROM localization_patch_events WHERE event_type='PATCH_APPROVED'")
    with task_scope(config(tmp_path,source),task_id='not-approved'):
        with pytest.raises(ProductionDatabaseError,match='APPROV'):
            apply_approved_localization_patches(source,patch_ids=['not-approved'],expected_base_commit_id='C1')
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT name FROM product_localizations WHERE language='zh'").fetchone()[0]=='旧名'
        assert db.execute('SELECT COUNT(*) FROM commit_batches').fetchone()[0]==1


def test_journal_capacity_failure_rolls_back_before_business_commit(tmp_path,monkeypatch):
    from action_tracker.operations.backup_pool import PoolCapacityError
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='capacity') as task:
        task.ensure()
        def blocked(*a,**kw):raise PoolCapacityError({'status':'BLOCKED','reason':'generated capacity failure'})
        monkeypatch.setattr(task,'event',blocked)
        with pytest.raises(PoolCapacityError):update(source,1)
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'
        assert db.execute('SELECT COUNT(*) FROM commit_batches').fetchone()[0]==1


def test_implicit_transaction_rechecks_head_after_sqlite_lock(tmp_path,monkeypatch):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='race') as task:
        task.ensure();original=task.check;called=False
        def inject(state,db):
            nonlocal called
            original(state,db)
            if not called:
                called=True
                with sqlite3.connect(source) as outside:
                    outside.execute("INSERT INTO commit_batches VALUES('outside','COMMITTED','2099-01-01')")
        monkeypatch.setattr(task,'check',inject)
        with pytest.raises(StorageError,match='EXTERNAL_HEAD'):
            with connect(source) as db:db.execute("UPDATE products SET value='must not commit'")
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'


def test_old_candidate_copy_cannot_overwrite_source_or_existing_output(tmp_path):
    from scripts.build_localization_closure_candidate import _copy_db
    source=database(tmp_path/'source.db');target=tmp_path/'existing.db';target.write_bytes(b'KEEP')
    with pytest.raises(ValueError,match='MUST_DIFFER'):_copy_db(source,source)
    with pytest.raises(ValueError,match='NOT_OVERWRITTEN'):_copy_db(source,target)
    assert target.read_bytes()==b'KEEP'
    with sqlite3.connect(source) as db:assert db.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'


def test_recovery_plan_remains_available_when_generated_source_is_corrupt(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='recovery') as task:update(source,1)
    import gc
    gc.collect()
    source.write_bytes(b'generated corruption for recovery test')
    result=task.recovery_plan()
    assert result['source_status']=='UNREADABLE'
    assert not result['automatic_restore'] and not result['production_database_modified']
    with sqlite3.connect(result['backup_path']) as db:
        assert db.execute('SELECT value FROM products').fetchone()[0]=='original Spanish fact'


def test_connection_context_commits_are_journaled_and_raw_copy_is_blocked(tmp_path):
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='nested-context') as task:
        with connect(source) as db:
            with db:db.execute("UPDATE products SET value='context commit'")
            assert len(task.load()['transactions'])==1
            other=sqlite3.connect(tmp_path/'empty-destination.db')
            try:
                with pytest.raises(StorageError,match='REQUIRES_POOL'):db.backup(other)
                assert other.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]==0
            finally:other.close()


def test_commit_error_after_durable_commit_is_never_called_rollback(tmp_path,monkeypatch):
    from action_tracker.operations.task_connection import TaskConnection
    source=database(tmp_path/'source.db');cfg=config(tmp_path,source)
    with task_scope(cfg,task_id='commit-io') as task:
        task.ensure()
        def uncertain_commit(db):
            sqlite3.Connection.commit(db)
            raise OSError('generated error after durable commit')
        monkeypatch.setattr(TaskConnection,'_commit_sqlite',uncertain_commit)
        with pytest.raises(PostCommitEvidenceError) as error:update(source,1)
        assert error.value.committed is True
        assert task.ensure()['expected_head']=='head2'
        assert task.read_event(task.load()['transactions'][-1])['event']=='COMMITTED'
    with sqlite3.connect(source) as db:assert db.execute('SELECT value FROM products').fetchone()[0]=='value-1'


def test_switching_legacy_run_to_task_mode_cannot_replace_original_baseline(tmp_path,monkeypatch):
    from test_localization_correction_apply import _primary
    from action_tracker.operations import entry
    from action_tracker.orchestrator import daily
    from action_tracker.database import integration
    source=_primary(tmp_path);cfg=config(tmp_path,source);cfg['task_backup']['enabled']=False
    monkeypatch.setattr(entry,'database_path',lambda cfg:source)
    monkeypatch.setattr(integration,'database_path',lambda cfg:source)
    monkeypatch.setattr(entry,'production_preflight',lambda *a:{})
    monkeypatch.setattr(entry,'git_commit_info',lambda:'generated')
    monkeypatch.setattr(daily,'run_daily',lambda cfg,**kw:{'run_id':kw['workflow_run_id'],
        'business_date':kw['business_date'],'qa':{'passed':False},'commit_status':'QA_BLOCKED'})
    first=entry.run_production(cfg,business_date='2026-11-13');assert first['state']=='BLOCKED'
    cfg['task_backup']['enabled']=True
    with pytest.raises(StorageError,match='LEGACY_RESUME'):
        entry.run_production(cfg,business_date='2026-11-13',resume=True,run_id=first['run_id'])
    assert len(list((tmp_path/'runtime/backup_pool/active').glob('*.sqlite3')))==1
