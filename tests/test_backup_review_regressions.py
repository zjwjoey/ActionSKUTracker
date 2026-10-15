"""CI_SAFE: reviewer regressions with temporary files and an in-process backend."""
import copy
from dataclasses import replace
from pathlib import Path

import pytest

from action_tracker.operations import backup_capture as capture
from action_tracker.operations import backup_cli as cli
from action_tracker.operations import backup_operations as op
from action_tracker.operations import backup_sets as bs
from action_tracker.operations.storage_foundation import StorageError, sha256_file
from test_backup_operations import Backend, setup
from test_storage_foundation import database, projections


def reseal(plan, mutation):
    value=copy.deepcopy(plan)
    value.pop('document_sha256')
    mutation(value)
    return bs.seal(value)


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(profile='CORE_BACKUP'),
    lambda p:p['assets'][0].update(source_version=None),
    lambda p:p['assets'][0].update(kind='verified_database'),
    lambda p:p.update(profile='HISTORICAL_ARCHIVE_BACKUP'),
])
def test_resealed_plan_cannot_bypass_business_or_database_contract(setup,mutation):
    _,roots,backend,_,plan,kwargs=setup
    forged=reseal(plan,mutation)
    with pytest.raises(StorageError):
        op.run_backup(forged,roots,**kwargs)
    assert backend.writes==0


def test_execution_rejects_sqlite_content_mislabeled_as_manifest(setup):
    source,roots,backend,_,plan,kwargs=setup
    db=database(source/'mislabel.sqlite3')
    def mutation(p):
        a=p['assets'][0]
        a.update(relative_path=db.name,snapshot_path='assets/current/'+db.name,
                 kind='run_manifest',sha256=sha256_file(db),source_identity=bs.file_identity(db),
                 size_bytes=db.stat().st_size)
        p['logical_bytes']=a['size_bytes']
    forged=reseal(plan,mutation)
    with pytest.raises(StorageError):
        op.run_backup(forged,roots,**kwargs)
    assert backend.writes==0


@pytest.mark.parametrize('run_id',['../escape','../../outside','bad/run','bad\\run'])
def test_cli_capture_rejects_traversal_before_capture_io(tmp_path,monkeypatch,run_id):
    raw={'operations_root':str(tmp_path/'operations'),'writer_lock_directory':str(tmp_path/'state'),
         'policy':{'enabled':True},'capture':{'source':str(tmp_path/'source.db'),
         'projections':{},'code_version':'fixture','recovery_owner':'owner',
         'retention':{'policy_id':'keep'}}}
    def must_not_capture(**kwargs):
        pytest.fail('Unsafe run ID reached capture filesystem boundary')
    monkeypatch.setattr(capture,'capture_core',must_not_capture)
    with pytest.raises(StorageError):
        cli._capture(raw,Backend(tmp_path),run_id)
    assert not (tmp_path/'operations').exists()


def test_failed_backend_reverification_preserves_owned_stage(setup,tmp_path,monkeypatch):
    _,roots,backend,_,plan,kwargs=setup
    receipt=op.run_backup(plan,roots,**kwargs)
    report=op.restore_backup(receipt,backend=backend,target=tmp_path/'restored',
        policy=kwargs['policy'],protected_roots=[Path(roots['current'])],
        report_path=tmp_path/'restore.json')
    job=kwargs['operations_root']/'jobs'/plan['backup_set_id']
    stage=job/'stage'
    before={p.relative_to(stage).as_posix():sha256_file(p) for p in stage.rglob('*') if p.is_file()}
    monkeypatch.setattr(backend,'verify_snapshot',lambda *_:{'status':'FAIL'})
    with pytest.raises(StorageError):
        op.release_owned_stage(job,backend=backend,restore_report=report)
    assert before=={p.relative_to(stage).as_posix():sha256_file(p) for p in stage.rglob('*') if p.is_file()}


def test_capture_resumes_after_interrupted_projection_copy(tmp_path,monkeypatch):
    src=database(tmp_path/'source'/'primary.db')
    projection=projections(src,tmp_path/'projections')
    destination=tmp_path/'captured'
    kwargs=dict(source=src,projections=projection,destination=destination,
        writer_lock_directory=tmp_path/'state',run_id='core-resume',code_version='fixture-code',
        repository=op.public_backend_identity(Backend(tmp_path)),config_version='V1',
        recovery_owner='fixture-owner',retention={'policy_id':'keep-fixture'})
    original=capture.shutil.copyfileobj
    calls=[]
    def interrupted(source,target,*args,**kw):
        calls.append(True)
        target.write(source.read(1));target.flush()
        raise OSError('synthetic interrupted projection copy')
    monkeypatch.setattr(capture.shutil,'copyfileobj',interrupted)
    with pytest.raises((StorageError,OSError)):
        capture.capture_core(**kwargs)
    assert calls
    monkeypatch.setattr(capture.shutil,'copyfileobj',original)
    plan,roots=capture.capture_core(**kwargs)
    assert plan['profile']=='CORE_BACKUP'
    for asset in plan['assets']:
        if asset['kind']=='projection':
            assert sha256_file(Path(roots[asset['root_id']])/asset['relative_path'])==asset['sha256']


def test_quota_counts_capture_and_staging_together(setup):
    _,roots,backend,_,plan,kwargs=setup
    capture_dir=kwargs['operations_root']/'captures'/'previous-fixture'
    capture_dir.mkdir(parents=True)
    (capture_dir/'capture-context.json').write_text('{"contract":"CORE_CAPTURE_CONTEXT_V1","run_id":"previous-fixture"}')
    (capture_dir/'owned-fixture.bin').write_bytes(b'x'*8192)
    stage_only=plan['logical_bytes']+len(bs.canonical(plan))+1
    kwargs['policy']=replace(kwargs['policy'],max_scratch_bytes=stage_only+1024)
    with pytest.raises(StorageError,match='BUDGET|QUOTA'):
        op.run_backup(plan,roots,**kwargs)
    assert backend.writes==0


def test_usage_counts_all_capture_and_session_bytes(setup):
    _, roots, backend, _, plan, kwargs = setup
    op.run_backup(plan, roots, **kwargs)
    root = kwargs['operations_root']
    for relative, count in [('captures/old/primary.sqlite3', 31),
                            ('sessions/first/cache/object', 17),
                            ('sessions/second/cache/object', 23)]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * count)
    report = op.storage_usage_report(root, backend=backend,
        policy=replace(kwargs['policy'], max_scratch_bytes=70))
    assert report['capture_bytes']['value'] == 31
    assert report['session_bytes']['value'] == 40
    assert report['staged_bytes']['value'] > 0
    assert report['total_scratch_bytes']['value'] == report['staged_bytes']['value'] + 71
    assert report['scratch_budget_alert'] is True
    assert report['monthly_actual_transfer_bytes']['status'] == 'UNKNOWN'


def test_new_capture_requires_prior_disposition_but_same_run_resumes(tmp_path):
    src = database(tmp_path / 'source' / 'primary.db')
    projection = projections(src, tmp_path / 'projections')
    backend = Backend(tmp_path)
    raw = {'operations_root': str(tmp_path / 'operations'),
           'writer_lock_directory': str(tmp_path / 'state'), 'policy': {'enabled': True},
           'capture': {'source': str(src), 'projections': {k: str(v) for k, v in projection.items()},
                       'code_version': 'fixture', 'recovery_owner': 'fixture-owner',
                       'retention': {'policy_id': 'keep'}}}
    first, roots = cli._capture(raw, backend, 'first')
    original = sha256_file(Path(roots['core']) / 'primary.sqlite3')
    resumed, _ = cli._capture(raw, backend, 'first')
    assert resumed['database_identities'] == first['database_identities']
    with pytest.raises(StorageError, match='PRIOR_CAPTURE_REQUIRES_DISPOSITION'):
        cli._capture(raw, backend, 'next-day')
    assert not (tmp_path / 'operations/captures/next-day').exists()
    assert sha256_file(Path(roots['core']) / 'primary.sqlite3') == original


def test_unknown_capture_blocks_before_source_read(tmp_path):
    prior = tmp_path / 'operations/captures/unknown'
    prior.mkdir(parents=True)
    (prior / 'unknown.bin').write_bytes(b'preserve')
    raw = {'operations_root': str(tmp_path / 'operations'),
           'writer_lock_directory': str(tmp_path / 'state'),
           'capture': {'source': str(tmp_path / 'missing.db'), 'projections': {},
                       'code_version': 'fixture', 'recovery_owner': 'owner',
                       'retention': {'policy_id': 'keep'}}}
    with pytest.raises(StorageError, match='PRIOR_CAPTURE_REQUIRES_DISPOSITION'):
        cli._capture(raw, Backend(tmp_path), 'new-run')
    assert (prior / 'unknown.bin').read_bytes() == b'preserve'
