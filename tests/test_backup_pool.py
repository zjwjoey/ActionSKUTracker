"""CI_SAFE: generated SQLite and owned temp pools only, no production/network."""
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from action_tracker.operations.backup_pool import BackupPool, PoolPolicy, PoolCapacityError
from action_tracker.operations.storage_foundation import StorageError, sha256_file
from test_storage_foundation import database, projections

MIB=1024**2


@pytest.fixture
def pool(tmp_path):
    return BackupPool(tmp_path/'runtime/backup_pool',policy=PoolPolicy(8*MIB,4*MIB),initialize=True)


def temporary(pool,task,size=128*1024,*,discardable=True):
    with pool.temporary(task_id=task,expected_bytes=size,source='generated:test-only',
                        purpose='test scratch',discardable=discardable) as asset:
        with Path(asset['path']).open('xb') as stream:stream.write(os.urandom(size))
    return asset


def test_all_new_database_and_restore_copies_are_in_pool_and_source_unchanged(pool,tmp_path):
    src=database(tmp_path/'source.db');projections(src,tmp_path/'projections')
    before=sha256_file(src)
    result=pool.backup(src,task_id='daily1',code_version='fixture')
    assert Path(result['backup_path']).is_relative_to(pool.root)
    assert result['restore_rehearsed'] and result['permanent_restore_copy_created'] is False
    rows=pool.status()['assets'];assert len(rows)==2
    assert [a['state'] for a in rows if a['kind']=='restore_test']==['DELETED']
    assert sha256_file(src)==before
    assert not list((pool.root/'staging').glob('*.sqlite3'))
    assert pool.verify(result['pool_asset_id'])['status']=='PASS'


def test_completed_explicitly_discardable_temporary_is_released(pool):
    a=temporary(pool,'scratch1')
    report=pool.finish_task('scratch1')
    assert not Path(a['path']).exists()
    deletion=next(r for r in report['actions'] if r['status']=='DELETED')
    assert deletion['actual_pool_bytes_released']>0
    assert deletion['allocated_file_bytes_deleted']>=128*1024


def test_unreleased_temporary_and_failed_task_are_preserved(pool):
    a=temporary(pool,'protected',discardable=False)
    pool.finish_task('protected');assert Path(a['path']).exists()
    b=temporary(pool,'failed');pool.finish_task('failed',state='FAILED')
    pool.checkpoint(force_temporaries=True);assert Path(b['path']).exists()


def test_latest_and_only_recovery_point_cannot_be_deleted(pool,tmp_path):
    src=database(tmp_path/'source.db');a=pool.backup(src,task_id='one')
    pool.finish_task('one')
    with pytest.raises(StorageError):pool.release_recovery(a['pool_asset_id'],replacement_id=a['pool_asset_id'])
    with pytest.raises(PoolCapacityError) as error:pool.checkpoint(8*MIB)
    assert Path(a['backup_path']).exists()
    assert any(x['reason'] in ('RECOVERY_RESPONSIBILITY','LATEST_VALID_RECOVERY') for x in error.value.report['actions'])


def test_quota_cleans_registered_old_copy_to_target_before_new_reservation(pool):
    a=temporary(pool,'old',3*MIB)
    pool.reference(a['asset_id'],'consumer');pool.finish_task('old')
    pool.reference(a['asset_id'],'consumer',release=True)
    report=pool.checkpoint(5*MIB)
    assert report['triggered'] and not report['blocked']
    assert report['usage_after']['allocated_bytes']<=pool.policy.target_bytes
    assert not Path(a['path']).exists()
    assert sum(x.get('actual_pool_bytes_released',0) for x in report['actions'])>0


def test_predicted_over_limit_blocks_before_new_file(pool):
    with pytest.raises(PoolCapacityError):temporary(pool,'oversized',9*MIB)
    assert not list((pool.root/'staging').glob('*.bin'))


def test_unknown_files_count_towards_capacity_but_are_never_deleted(pool):
    path=pool.root/'unregistered.bin';path.write_bytes(os.urandom(5*MIB))
    with pytest.raises(PoolCapacityError) as error:pool.checkpoint(3*MIB)
    assert path.exists()
    assert str(path) in error.value.report['usage_after']['unknown_files']


def test_active_lease_and_reference_protect_complete_file(pool):
    from action_tracker.operations.pool_files import os_lock
    a=temporary(pool,'reader');pool.reference(a['asset_id'],'held');pool.finish_task('reader')
    pool.reference(a['asset_id'],'held',release=True)
    with os_lock(pool.root/'leases'/(a['asset_id']+'.lock')):
        report=pool.checkpoint(force_temporaries=True)
        assert Path(a['path']).exists()
        assert any(x.get('reason')=='POOL_FILE_IN_USE' for x in report['actions'])
    pool.checkpoint(force_temporaries=True);assert not Path(a['path']).exists()


def test_tampered_asset_never_deleted_or_reported_clean(pool):
    a=temporary(pool,'tampered');pool.reference(a['asset_id'],'hold');pool.finish_task('tampered')
    pool.reference(a['asset_id'],'hold',release=True)
    Path(a['path']).write_bytes(b'changed')
    with pytest.raises(StorageError,match='CLEANUP'):pool.checkpoint(force_temporaries=True)
    assert Path(a['path']).exists()


def test_restore_failure_keeps_base_and_failed_restore(pool,tmp_path,monkeypatch):
    from action_tracker.operations import backup_pool as module
    source=database(tmp_path/'source.db')
    monkeypatch.setattr(module.shutil,'copyfileobj',lambda *a: (_ for _ in ()).throw(OSError('restore failure')))
    with pytest.raises(OSError):pool.backup(source,task_id='fail_restore')
    assert list((pool.root/'active').glob('*.sqlite3'))
    assert list((pool.root/'staging').glob('*.sqlite3'))
    pool.checkpoint(force_temporaries=True)
    assert all(x['state']=='FAILED' for x in pool.status()['assets'])


def test_missing_restore_proof_is_not_an_eligible_backup(pool,tmp_path):
    src=database(tmp_path/'source.db');a=pool.backup(src,task_id='first');pool.finish_task('first')
    b=pool.backup(src,task_id='second');pool.finish_task('second')
    Path(a['restore_report']).unlink()
    with pytest.raises(StorageError):pool.release_recovery(a['pool_asset_id'],replacement_id=b['pool_asset_id'])
    assert Path(a['backup_path']).exists()


def test_cannot_adopt_historical_directory_or_place_pool_over_originals(tmp_path):
    historic=tmp_path/'backups';historic.mkdir();file=historic/'old.db';file.write_bytes(b'KEEP')
    with pytest.raises(StorageError):BackupPool(historic,initialize=True)
    with pytest.raises(StorageError):BackupPool(tmp_path,protected_paths=[file],initialize=True)
    assert file.read_bytes()==b'KEEP'


def test_hardlinked_payload_is_never_cleanup_eligible(pool,tmp_path):
    a=temporary(pool,'hardlinked');pool.reference(a['asset_id'],'hold');pool.finish_task('hardlinked')
    other=tmp_path/'outside.bin';os.link(a['path'],other)
    pool.reference(a['asset_id'],'hold',release=True)
    with pytest.raises(StorageError):pool.checkpoint(force_temporaries=True)
    assert other.exists() and Path(a['path']).exists()


def test_forged_registry_checksum_blocks_cleanup(pool):
    a=temporary(pool,'registry');path=pool.root/'index.json';raw=json.loads(path.read_text())
    raw['assets'][a['asset_id']]['state']='DELETED';path.write_text(json.dumps(raw))
    with pytest.raises(StorageError):pool.checkpoint(force_temporaries=True)
    assert Path(a['path']).exists()


def worker(pool, code, *args):
    return subprocess.Popen([sys.executable,'-B','-c',code,str(pool.root),*map(str,args)],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


def test_concurrent_admission_counts_other_process_reservations(pool,tmp_path):
    ready=tmp_path/'ready';release=tmp_path/'release'
    code='''
from pathlib import Path
import sys,time
from action_tracker.operations.backup_pool import BackupPool,PoolPolicy
p=BackupPool(sys.argv[1],policy=PoolPolicy(8*1024**2,4*1024**2))
with p.temporary(task_id='child',expected_bytes=5*1024**2,source='generated',purpose='concurrency') as a:
    Path(a['path']).write_bytes(b'child')
    Path(sys.argv[2]).write_text('ready')
    deadline=time.monotonic()+25
    while not Path(sys.argv[3]).exists():
        if time.monotonic()>deadline:raise RuntimeError('parent timeout')
        time.sleep(.05)
'''
    process=worker(pool,code,ready,release)
    try:
        deadline=time.monotonic()+20
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic()<deadline
            time.sleep(.05)
        with pytest.raises(PoolCapacityError):temporary(pool,'parent',3*MIB)
        assert pool.status()['outstanding_reserved_bytes']>4*MIB
    finally:
        release.write_text('release');stdout,stderr=process.communicate(timeout=30)
    assert process.returncode==0,(stdout,stderr)


def test_two_process_cleaners_delete_once_and_preserve_failed_assets(pool):
    a=temporary(pool,'completed');pool.reference(a['asset_id'],'hold');pool.finish_task('completed')
    b=temporary(pool,'failed');pool.finish_task('failed',state='FAILED')
    pool.reference(a['asset_id'],'hold',release=True)
    code='''
import sys
from action_tracker.operations.backup_pool import BackupPool,PoolPolicy
p=BackupPool(sys.argv[1],policy=PoolPolicy(8*1024**2,4*1024**2))
p.checkpoint(force_temporaries=True)
'''
    children=[worker(pool,code) for _ in range(2)]
    for process in children:
        stdout,stderr=process.communicate(timeout=30)
        assert process.returncode==0,(stdout,stderr)
    assert not Path(a['path']).exists() and Path(b['path']).exists()
    events=[json.loads(p.read_text()) for p in (pool.root/'cleanup').glob('*.json')]
    assert sum(e['event']=='ASSET_DELETED' and e['asset_id']==a['asset_id'] for e in events)==1


def test_cleanup_error_is_not_success_even_when_below_quota(pool,monkeypatch):
    from action_tracker.operations import pool_files
    a=temporary(pool,'error');pool.reference(a['asset_id'],'hold');pool.finish_task('error')
    pool.reference(a['asset_id'],'hold',release=True)
    def fail(*args,**kwargs):raise OSError('injected permission failure')
    monkeypatch.setattr(pool_files,'delete_group',fail)
    with pytest.raises(StorageError,match='CLEANUP'):pool.checkpoint(force_temporaries=True)
    assert Path(a['path']).exists()


def test_cleanup_must_reach_target(pool):
    a=temporary(pool,'protected',5*MIB,discardable=False);pool.finish_task('protected')
    with pytest.raises(PoolCapacityError):pool.checkpoint(3*MIB)
    assert Path(a['path']).exists()


def test_disabled_cleanup_preserves_files_and_blocks_new_reservation(tmp_path):
    p=BackupPool(tmp_path/'pool',policy=PoolPolicy(8*MIB,4*MIB,False),initialize=True)
    a=temporary(p,'disabled',3*MIB);p.finish_task('disabled')
    with pytest.raises(PoolCapacityError):temporary(p,'new',5*MIB)
    assert Path(a['path']).exists()


def test_only_explicit_boolean_enablement_and_fixed_production_limits(tmp_path):
    from action_tracker.operations.backup_pool import configured_pool
    cfg={'project_root':tmp_path,'backup_pool':{'enabled':'false'}}
    with pytest.raises(StorageError):configured_pool(cfg)
    cfg['backup_pool']={'enabled':False}
    assert configured_pool(cfg) is None and not list(tmp_path.iterdir())
    cfg['backup_pool']={'enabled':True,'max_bytes':8*MIB,'target_bytes':4*MIB}
    with pytest.raises(StorageError,match='5_AND_4'):configured_pool(cfg)


def test_new_reservation_automatically_reclaims_capacity(pool):
    a=temporary(pool,'old',3*MIB);pool.reference(a['asset_id'],'consumer');pool.finish_task('old')
    pool.reference(a['asset_id'],'consumer',release=True)
    b=temporary(pool,'new',5*MIB)
    assert not Path(a['path']).exists() and Path(b['path']).exists()
    audits=[json.loads(p.read_text()) for p in (pool.root/'cleanup').glob('*.json')]
    checkpoints=[e['report'] for e in audits if e['event']=='CAPACITY_CHECKPOINT' and e['report']['triggered']]
    assert checkpoints and checkpoints[0]['usage_after']['allocated_bytes']<=pool.policy.target_bytes


def test_temporary_sqlite_cannot_bypass_restore_protection(pool):
    with pytest.raises(StorageError):
        with pool.temporary(task_id='wrong',expected_bytes=MIB,source='generated',purpose='wrong kind',discardable=True) as a:
            Path(a['path']).write_bytes(b'SQLite format 3\x00'+bytes(128))
    assert Path(a['path']).exists()


def test_cli_does_not_load_production_settings_and_verifies_generated_backup(tmp_path,monkeypatch,capsys):
    from action_tracker import cli
    from action_tracker import config
    monkeypatch.setattr(config,'load_settings',lambda *a,**kw:pytest.fail('production configuration loaded'))
    root=tmp_path/'runtime/backup_pool';args=['backup-pool','--root',str(root)]
    assert cli.main([*args,'init'])==0;capsys.readouterr()
    src=database(tmp_path/'source.db')
    assert cli.main([*args,'capture','--source',str(src),'--task-id','cli','--code-version','test'])==0
    result=json.loads(capsys.readouterr().out)
    assert cli.main([*args,'verify','--asset-id',result['pool_asset_id']])==0;capsys.readouterr()
    assert cli.main([*args,'finish-task','--task-id','cli'])==0;capsys.readouterr()
    assert cli.main([*args,'cleanup-preview'])==0
    assert json.loads(capsys.readouterr().out)['status']=='PREVIEW'


def test_operations_opt_in_and_resume_reuse_pool_backup(tmp_path,monkeypatch):
    from action_tracker.operations import entry
    from action_tracker.database import integration
    from action_tracker.orchestrator import daily
    src=database(tmp_path/'source.db');before=sha256_file(src)
    cfg={'project_root':tmp_path,'paths':{'state':tmp_path/'state','backups':tmp_path/'historic'},
         'backup_pool':{'enabled':True,'root':'runtime/backup_pool'}}
    monkeypatch.setattr(entry,'database_path',lambda cfg:src)
    monkeypatch.setattr(integration,'database_path',lambda cfg:src)
    monkeypatch.setattr(entry,'production_preflight',lambda *a:{})
    monkeypatch.setattr(entry,'git_commit_info',lambda:'generated')
    monkeypatch.setattr(entry,'_recover_committed_collection',lambda *a:None)
    monkeypatch.setattr(daily,'run_daily',lambda cfg,**kw:{'run_id':kw['workflow_run_id'],
        'business_date':kw['business_date'],'qa':{'passed':False},'commit_status':'QA_BLOCKED'})
    first=entry.run_production(cfg,business_date='2026-11-13')
    assert first['state']=='BLOCKED' and first['backup_pool']['status']=='PASS'
    result=entry.run_production(cfg,business_date='2026-11-13',resume=True,run_id=first['run_id'])
    assert result['state']=='BLOCKED' and result['backup_pool']['status']=='PASS'
    assert not (tmp_path/'historic').exists()
    assert len(list((tmp_path/'runtime/backup_pool/active').glob('*.sqlite3')))==1
    assert sha256_file(src)==before
