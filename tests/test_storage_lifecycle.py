"""CI_SAFE: isolated roots and generated files/SQLite only, no live data."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from action_tracker.operations.storage_foundation import StorageError, resolve_asset, sha256_file, verified_backup
from action_tracker.operations.storage_lifecycle import (ScratchTask, cleanup_preview, create_candidate,
    materialize_asset, materialize_plan, materialize_zip_member, relative_target, write_new,
    classify_legacy_database, resolve_legacy_binding)
from action_tracker.database.schema import migrate_v2


def roots(tmp_path):
    result = {}
    for name in ('data', 'archive', 'scratch'):
        path = tmp_path / name
        create_candidate(path, protected_roots=[tmp_path / 'production'])
        result[name] = str(path)
    return result


def item(source, relative='objects/source.json', root_id='archive', **binding):
    return {'original_path': str(source.resolve()), 'relative_path': relative, 'root_id': root_id,
            'sha256': sha256_file(source), 'size_bytes': source.stat().st_size, **binding}


def test_copy_relocation_resume_and_multiple_business_bindings(tmp_path):
    rs = roots(tmp_path)
    source = tmp_path / 'Spanish.json'
    source.write_bytes(b'{"name_es":"vaso"}')
    a = materialize_asset(item(source, sku='1001', source_version='v1'), rs)
    b = materialize_asset(item(source, sku='2002', source_version='v2'), rs)
    assert a['asset_id'] == b['asset_id'] and a['binding_id'] != b['binding_id']
    assert not a['reused'] and b['reused']
    assert resolve_asset(b, rs)['hash_verified']
    assert resolve_asset(b, {'data': rs['data']})['status'] == 'ASSET_UNAVAILABLE'
    assert source.read_bytes() == b'{"name_es":"vaso"}'


@pytest.mark.parametrize('value', ['../escape', 'C:escape', 'C:/escape', 'a:ads', '/escape'])
def test_p2_path_escape(tmp_path, value):
    with pytest.raises(StorageError, match='ASSET_PATH_ESCAPE'):
        relative_target(tmp_path, value)


def test_candidate_refuses_production_and_unknown_existing_root(tmp_path):
    with pytest.raises(StorageError, match='CANDIDATE_PRODUCTION_OVERLAP'):
        create_candidate(tmp_path / 'production/child', protected_roots=[tmp_path / 'production'])
    (tmp_path / 'unknown').mkdir()
    with pytest.raises(FileExistsError): create_candidate(tmp_path / 'unknown', protected_roots=[])


def test_plan_budget_fails_before_copy_and_unknown_target_is_preserved(tmp_path):
    rs = roots(tmp_path)
    source = tmp_path / 'source'; source.write_bytes(b'fact')
    i = item(source)
    with pytest.raises(StorageError, match='PLAN_COPY_BUDGET_EXCEEDED'):
        materialize_plan([i], rs, max_bytes=1)
    assert not (Path(rs['archive']) / i['relative_path']).exists()
    target = Path(rs['archive']) / i['relative_path']; target.parent.mkdir(); target.write_bytes(b'unknown')
    with pytest.raises(StorageError, match='CANDIDATE_TARGET_UNKNOWN'): materialize_asset(i, rs)
    assert target.read_bytes() == b'unknown'


def test_live_database_cannot_be_copied_as_a_regular_asset(tmp_path):
    rs = roots(tmp_path)
    source = tmp_path / 'source.db'; migrate_v2(source, role='PRIMARY')
    with pytest.raises(StorageError, match='DATABASE_VERIFIED_BACKUP_REQUIRED'):
        materialize_asset(item(source, relative='runtime/db/action_tracker.db', root_id='data'), rs)
    backup = tmp_path / 'closed.sqlite3'
    m = verified_backup(source, backup, run_id='fixture')
    copied = materialize_asset(item(backup, relative='runtime/db/action_tracker.db', root_id='data', verified_database=m), rs)
    assert copied['status'] == 'COPIED_VERIFIED'


def completed_disposable(tmp_path):
    rs = roots(tmp_path)
    task = ScratchTask(Path(rs['scratch']), owner_task='fixture', run_id='r1', budget_bytes=1024)
    task.register('copy.txt', source='reconstructable-fixture', expected_bytes=4, recovery_required=False)
    (task.directory / 'copy.txt').write_bytes(b'fact')
    alternative = tmp_path / 'retained.txt'; alternative.write_bytes(b'fact')
    proof = tmp_path / 'recovery.json'
    write_new(proof, {'status':'PASS', 'backup_sha256':sha256_file(alternative)})
    record = task.complete('copy.txt', reference_count=0, references_verified=True, unique_business_data=False,
        retain_until=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
        alternative_recovery={'status':'PASS','sha256':sha256_file(alternative),'path':str(alternative),'evidence_path':str(proof)})
    return task, record, alternative


def test_only_new_owned_scratch_can_be_disposed_with_full_evidence(tmp_path):
    task, record, alternative = completed_disposable(tmp_path)
    preview = cleanup_preview([record])
    assert preview['whitelist'] and not preview['assets'][0]['delete_allowed']
    assert preview['released_bytes'] == 0
    assert task.discard_owned('copy.txt')['task_status'] == 'DISPOSED'
    assert alternative.read_bytes() == b'fact'
    with pytest.raises(StorageError, match='SCRATCH_OWNERSHIP_INVALID'):
        task.discard_owned('unregistered.txt')


@pytest.mark.parametrize('key,value', [('reference_count', 1), ('references_verified', False),
    ('task_status','RUNNING'), ('recovery_required',True), ('unique_business_data',None)])
def test_cleanup_blocks_missing_responsibility_evidence(tmp_path, key, value):
    task, record, _ = completed_disposable(tmp_path)
    record[key] = value
    assert not cleanup_preview([record])['whitelist']


def test_cleanup_rehashes_recovery_alternative_and_owned_file(tmp_path):
    task, record, alternative = completed_disposable(tmp_path)
    alternative.write_bytes(b'changed')
    assert not cleanup_preview([record])['whitelist']
    with pytest.raises(StorageError, match='SCRATCH_CLEANUP_BLOCKED'): task.discard_owned('copy.txt')
    alternative.write_bytes(b'fact')
    (task.directory/'copy.txt').write_bytes(b'tamper')
    with pytest.raises(StorageError, match='SCRATCH_IDENTITY_CHANGED'): task.discard_owned('copy.txt')


def test_scratch_backup_uses_p1_and_does_not_leave_restore_database(tmp_path):
    rs = roots(tmp_path)
    source = tmp_path / 'source.db'; migrate_v2(source, role='PRIMARY')
    task = ScratchTask(Path(rs['scratch']), owner_task='backup-fixture', run_id='r2', budget_bytes=8*1024**2)
    first = task.backup(source)
    again = task.backup(source)
    reopened = ScratchTask.resume(Path(rs['scratch']),task.directory,owner_task='backup-fixture',run_id='r2')
    assert reopened.backup(source)['backup']['reused']
    with pytest.raises(StorageError, match='SCRATCH_RESUMED_DELETION_DISABLED'):
        reopened.discard_owned('primary.sqlite3')
    assert again['backup']['reused'] and first['restore']['status'] == 'PASS'
    assert len(list(task.directory.glob('*.sqlite3'))) == 1
    assert not list(task.directory.glob('restore-scratch-*'))
    with pytest.raises(StorageError, match='SCRATCH_CLEANUP_BLOCKED'): task.discard_owned('primary.sqlite3')


def test_scratch_capacity_budget_and_owner_are_enforced(tmp_path):
    rs = roots(tmp_path)
    task = ScratchTask(Path(rs['scratch']), owner_task='fixture', run_id='r', budget_bytes=4)
    with pytest.raises(StorageError, match='SCRATCH_TASK_BUDGET_EXCEEDED'):
        task.register('oversize',source='fixture',expected_bytes=5)
    task, _, _ = completed_disposable(tmp_path / 'other')
    (task.directory/'owner.json').write_text('{"token":"other"}')
    with pytest.raises(StorageError, match='SCRATCH_OWNERSHIP_INVALID'): task.discard_owned('copy.txt')


def test_zip_member_exact_identity_hash_and_budget(tmp_path):
    import zipfile
    rs=roots(tmp_path)
    package=tmp_path/'images.zip'
    with zipfile.ZipFile(package,'w') as z: z.writestr('images/1001.png',b'image fixture')
    import hashlib
    i={'container_path':str(package),'member':'images/1001.png','root_id':'archive',
       'relative_path':'images/1001.png','sha256':hashlib.sha256(b'image fixture').hexdigest(),
       'size_bytes':13,'sku':'1001','source_url':'https://example.test/image'}
    with pytest.raises(StorageError,match='ZIP_MEMBER_BUDGET'):
        materialize_zip_member(i,rs,max_member_bytes=1)
    a=materialize_zip_member(i,rs)
    assert a['container_integrity']=='UNVERIFIED_WHOLE_CONTAINER'
    assert materialize_zip_member(i,rs)['reused']
    with pytest.raises(StorageError,match='ZIP_MEMBER_HASH_MISMATCH'):
        materialize_zip_member({**i,'sha256':'a'*64},rs)
    missing=materialize_zip_member({**i,'container_path':str(tmp_path/'offline.zip')},rs)
    assert missing['status']=='ASSET_UNAVAILABLE' and 'source_empty' not in missing


def test_exact_legacy_adapter_never_guesses_basename(tmp_path):
    rs=roots(tmp_path);source=tmp_path/'source';source.write_bytes(b'fact')
    a=materialize_asset(item(source),rs)
    assert resolve_legacy_binding(str(source),[a],rs)['status']=='AVAILABLE'
    assert resolve_legacy_binding(str(tmp_path/'other/source'),[a],rs)['status']=='ASSET_UNAVAILABLE'
    with pytest.raises(StorageError,match='LEGACY_BINDING_AMBIGUOUS'):
        resolve_legacy_binding(str(source),[a,{**a,'relative_path':'other'}],rs)


def test_legacy_role_candidate_and_duplicate_never_become_cleanup_permission():
    row={'path':'legacy.restore-check.sqlite3','purpose_class':'RESTORE_VALIDATION_COPY'}
    a=classify_legacy_database(row,evidence_files=['audit.json'],full_hash='a'*64)
    assert a['classification']=='DEV_TEST_ARTIFACT' and a['reference_count'] is None
    assert a['unique_business_data'] is None
    assert not cleanup_preview([a])['whitelist']


@pytest.mark.parametrize('alias', [False, True])
def test_recovery_alternative_cannot_be_the_candidate_or_its_hardlink(tmp_path, alias):
    task,record,_=completed_disposable(tmp_path)
    candidate=Path(record['path'])
    alternative=candidate
    if alias:
        alternative=tmp_path/'same-object.txt'
        alternative.hardlink_to(candidate)
    proof=tmp_path/'self-proof.json'
    write_new(proof,{'status':'PASS','backup_sha256':record['sha256']})
    record['alternative_recovery']={'status':'PASS','path':str(alternative),
        'sha256':record['sha256'],'evidence_path':str(proof)}
    result=cleanup_preview([record])
    assert not result['whitelist']
    assert 'ALTERNATIVE_RECOVERY_IS_CANDIDATE' in result['assets'][0]['blockers']
    assert candidate.is_file()
