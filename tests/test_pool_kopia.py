"""CI_SAFE: generated SQLite, owned temporary pool and fake repository only."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from action_tracker.operations.backup_pool import BackupPool
from action_tracker.operations.backup_operations import OperationPolicy
from action_tracker.operations.pool_kopia import roundtrip,own_root
from action_tracker.operations.kopia_backend import _source_inventory
from action_tracker.operations.storage_foundation import StorageError,sha256_file
from test_storage_foundation import database,projections
from test_backup_operations import Backend


class Repository(Backend):
    def __init__(self,pool):
        root=own_root(pool);repo=root/'repository';repo.mkdir()
        super().__init__(repo)
        self.config=SimpleNamespace(provider='filesystem',repository_path=str(repo),expected_repository_id='a'*64,purpose='business')
        self.workspace=root/'sessions/fake-session'
        self.restores=0;self.verifies=0;self.bad_restore=False;self.fail_restore_after_copy=False

    def create_snapshot(self,source,**kwargs):
        try:super().create_snapshot(source,**kwargs)
        finally:
            for n,snapshot in enumerate(self.snapshots):
                snapshot.update(snapshot_id=f'{n:032x}',source_path=str(source),
                                **_source_inventory(source,content_hash=True))
        return self.list_snapshots(backup_set_id=kwargs['backup_set_id'])[0]

    def verify_snapshot(self,snapshot_id):
        self.verifies+=1
        s=next(s for s in self.snapshots if s['snapshot_id']==snapshot_id)
        return {'status':'SNAPSHOT_VERIFIED','verification':'KOPIA_100_PERCENT_FILES',
                'snapshot_id':snapshot_id,'file_count':s['file_count']}

    def restore_snapshot(self,snapshot_id,target):
        self.restores+=1;result=super().restore_snapshot(snapshot_id,target)
        if self.bad_restore:next(target.rglob('*.sqlite3')).write_bytes(b'corrupted fixture')
        if self.fail_restore_after_copy:
            self.fail_restore_after_copy=False;raise StorageError('GENERATED_RESTORE_RESPONSE_LOST')
        return result


@pytest.fixture
def setup(tmp_path):
    source=database(tmp_path/'source.db');projection=projections(source,tmp_path/'projection')
    pool=BackupPool(tmp_path/'pool',initialize=True)
    base=pool.backup(source,task_id='base',code_version='generated-fixture')
    backend=Repository(pool)
    return pool,base,backend,projection


def test_disabled_does_not_touch_anything():
    assert roundtrip(None,None,backend=None)=={'status':'DISABLED','side_effects':False}


def test_roundtrip_uses_one_stage_one_restore_and_releases_them(setup):
    pool,base,backend,projection=setup
    original=sha256_file(Path(base['backup_path']))
    result=roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True),projections=projection)
    assert result['status']=='ROUNDTRIP_PASS' and backend.writes==backend.restores==1
    bundles=[a for a in pool.status()['assets'] if a['kind']=='kopia_bundle']
    assert len(bundles)==2 and all(a['state']=='DELETED' for a in bundles)
    assert all(not Path(a['path']).exists() for a in bundles)
    assert sha256_file(Path(base['backup_path']))==original
    proof=json.loads(Path(result['proof']['path']).read_text('utf-8'))
    assert all(a['status']=='PASS' for a in proof['assets'])
    assert next(a for a in proof['assets'] if a['asset_id']=='primary')['database']['export_sync']['status']=='SUCCESS'
    assert sum(a.get('actual_pool_bytes_released',0) for a in result['cleanup']['actions'])>0


def test_resume_after_exit_does_not_stage_or_restore_again(setup):
    pool,base,backend,projection=setup
    kwargs={'backend':backend,'policy':OperationPolicy(enabled=True),'projections':projection}
    first=roundtrip(pool,base['pool_asset_id'],**kwargs)
    result=roundtrip(BackupPool(pool.root),base['pool_asset_id'],**kwargs)
    assert result['reused'] and result['new_stage_copies']==result['new_restore_copies']==0
    assert result['snapshot_id']==first['snapshot_id']
    assert backend.writes==backend.restores==1


def test_unknown_snapshot_write_is_discovered_without_second_write(setup):
    pool,base,backend,_=setup;backend.fail_after_commit=True
    with pytest.raises(StorageError):roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True))
    assert backend.writes==1 and backend.restores==0
    result=roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True))
    assert result['status']=='ROUNDTRIP_PASS' and backend.writes==1


def test_failed_restore_keeps_all_copies_and_does_not_download_again(setup):
    pool,base,backend,_=setup;backend.bad_restore=True
    kwargs={'backend':backend,'policy':OperationPolicy(enabled=True)}
    with pytest.raises(StorageError):roundtrip(pool,base['pool_asset_id'],**kwargs)
    bundles=[a for a in pool.status()['assets'] if a['kind']=='kopia_bundle']
    assert len(bundles)==2 and all(Path(a['path']).exists() for a in bundles)
    assert any(a['state']=='FAILED' for a in bundles)
    with pytest.raises(StorageError):roundtrip(pool,base['pool_asset_id'],**kwargs)
    assert backend.restores==1 and Path(base['backup_path']).exists()


def test_complete_restore_with_lost_response_reuses_existing_download(setup):
    pool,base,backend,_=setup;backend.fail_restore_after_copy=True
    kwargs={'backend':backend,'policy':OperationPolicy(enabled=True)}
    with pytest.raises(StorageError):roundtrip(pool,base['pool_asset_id'],**kwargs)
    result=roundtrip(pool,base['pool_asset_id'],**kwargs)
    assert result['status']=='ROUNDTRIP_PASS' and backend.restores==1
    assert result['new_stage_copies']==result['new_restore_copies']==0


def test_missing_snapshot_after_success_is_blocked_without_new_copies(setup):
    pool,base,backend,_=setup;kwargs={'backend':backend,'policy':OperationPolicy(enabled=True)}
    roundtrip(pool,base['pool_asset_id'],**kwargs);backend.snapshots=[]
    with pytest.raises(StorageError,match='MISSING'):roundtrip(pool,base['pool_asset_id'],**kwargs)
    assert backend.writes==backend.restores==1


def test_orphan_reservation_is_adopted_without_another_stage_slot(setup):
    from action_tracker.operations.pool_kopia import PoolKopiaJob
    from action_tracker.operations import pool_bundles
    pool,base,backend,_=setup
    job=PoolKopiaJob(pool,base['pool_asset_id'],backend,OperationPolicy(enabled=True))
    state=job.load_or_create()
    orphan=pool_bundles.reserve(pool,task_id=job.id+'-stage',source_asset=base['pool_asset_id'],
                               plan=state['plan'],role='stage')
    assert job.reserve(state,'stage')['asset_id']==orphan['asset_id']
    result=roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True))
    assert result['status']=='ROUNDTRIP_PASS'
    assert len([a for a in pool.status()['assets'] if a['kind']=='kopia_bundle'])==2


def test_partial_stage_is_preserved_without_creating_another_copy(setup):
    from action_tracker.operations.pool_kopia import PoolKopiaJob
    pool,base,backend,_=setup
    job=PoolKopiaJob(pool,base['pool_asset_id'],backend,OperationPolicy(enabled=True))
    state=job.load_or_create();a=job.reserve(state,'stage')
    path=Path(a['path']);path.mkdir();(path/'partial').write_bytes(b'evidence')
    with pytest.raises(StorageError):job.run()
    assert (path/'partial').read_bytes()==b'evidence' and backend.writes==0
    assert len([a for a in pool.status()['assets'] if a['kind']=='kopia_bundle'])==1


def test_pool_capacity_blocks_before_snapshot_write(tmp_path):
    from action_tracker.operations.backup_pool import PoolPolicy,PoolCapacityError
    source=database(tmp_path/'source.db')
    pool=BackupPool(tmp_path/'pool',initialize=True,
                    policy=PoolPolicy(max_bytes=4*1024**2,target_bytes=3*1024**2))
    base=pool.backup(source,task_id='base',code_version='fixture')
    backend=Repository(pool)
    with pytest.raises(PoolCapacityError):
        roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True))
    assert backend.writes==backend.restores==0
    assert Path(base['backup_path']).exists()


def test_same_job_concurrent_writer_is_blocked(setup):
    from action_tracker.operations.pool_kopia import PoolKopiaJob
    from action_tracker.operations.pool_files import os_lock
    pool,base,backend,_=setup
    job=PoolKopiaJob(pool,base['pool_asset_id'],backend,OperationPolicy(enabled=True))
    with os_lock(job.directory/'job.lock',wait=0):
        with pytest.raises(StorageError):job.run()
    assert backend.writes==0


def test_manual_cli_is_disabled_before_opening_pool(tmp_path,capsys):
    from action_tracker.cli import main
    root=tmp_path/'does-not-exist'
    assert main(['pool-kopia','--root',str(root),'--base-asset-id','absent',
                 '--repository-id','a'*64])==0
    assert not root.exists() and json.loads(capsys.readouterr().out)['status']=='DISABLED'


def test_changed_projection_cannot_be_attached_to_old_baseline(setup):
    pool,base,backend,projection=setup
    projection['master'].write_bytes(b'newer projection')
    with pytest.raises(StorageError,match='PROJECTION_COMMIT_MISMATCH'):
        roundtrip(pool,base['pool_asset_id'],backend=backend,policy=OperationPolicy(enabled=True),projections=projection)
    assert backend.writes==backend.restores==0


def test_cleanup_error_is_not_reported_as_success_and_resume_only_cleans(setup,monkeypatch):
    from action_tracker.operations import pool_files
    pool,base,backend,_=setup;kwargs={'backend':backend,'policy':OperationPolicy(enabled=True)}
    actual=pool_files.delete_group
    def fail(files,**args):
        if args.get('owned_directory'):raise StorageError('GENERATED_CLEANUP_FAILURE')
        return actual(files,**args)
    monkeypatch.setattr(pool_files,'delete_group',fail)
    with pytest.raises(StorageError):roundtrip(pool,base['pool_asset_id'],**kwargs)
    assert backend.writes==backend.restores==1
    monkeypatch.setattr(pool_files,'delete_group',actual)
    assert roundtrip(pool,base['pool_asset_id'],**kwargs)['reused']
    assert backend.writes==backend.restores==1
