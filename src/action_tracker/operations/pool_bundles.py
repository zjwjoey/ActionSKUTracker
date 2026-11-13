"""Explicitly registered Kopia stage/restore trees; no recursive deletion."""
import json,os
from pathlib import Path

from .backup_sets import canonical,validate_seal,verify_restored,_validate_plan
from .cleanup_executor import safe_path
from .pool_files import file_record,os_lock
from .storage_foundation import StorageError,sha256_file
from .backup_pool import now


def reserve(pool,*,task_id,source_asset,plan,role):
    _validate_plan(plan)
    if role not in ('stage','restore'):raise StorageError('POOL_BUNDLE_ROLE_INVALID')
    pool.verify(source_asset)
    return pool._reserve([{'task_id':task_id,'source':source_asset,'source_asset':source_asset,
        'kind':'kopia_bundle','purpose':'kopia_'+role,'role':role,'plan':plan,
        'expected_bytes':plan['logical_bytes']+len(canonical(plan))+65536,'recovery_required':True}])[0]


def tree(directory):
    directory=safe_path(Path(directory));files=[];directories=[]
    for parent,dirs,names in os.walk(directory,followlinks=False):
        current=safe_path(Path(parent));s=current.stat()
        directories.append({'path':str(current),'file_id':f'{s.st_dev}:{s.st_ino}'})
        for name in dirs:safe_path(current/name)
        for name in names:files.append(file_record(safe_path(current/name)))
    return sorted(files,key=lambda f:f['path']),sorted(directories,key=lambda f:f['path'])


def complete(pool,asset_id):
    with pool._lock() as index:a=dict(index['assets'][asset_id])
    if a['kind']!='kopia_bundle' or a['state'] not in ('CREATING','FAILED'):raise StorageError('POOL_BUNDLE_NOT_CREATING')
    result=verify_restored(a['plan'],Path(a['path']))
    files,dirs=tree(a['path'])
    if set(f['path'] for f in files)!=set(a['paths']):raise StorageError('POOL_BUNDLE_UNEXPECTED_FILES')
    if sum(f['size_bytes'] for f in files)>a['reservation_bytes']:raise StorageError('POOL_BUNDLE_RESERVATION_EXCEEDED')
    expected={str(Path(a['path'])/v['snapshot_path']):v['sha256'] for v in a['plan']['assets']}
    expected[str(Path(a['path'])/'backup-set.json')]=__import__('hashlib').sha256(canonical(a['plan'])+b'\n').hexdigest()
    if any(f['sha256']!=expected[f['path']] for f in files):raise StorageError('POOL_BUNDLE_CHANGED_AFTER_VERIFY')
    with pool._lock() as index:
        target=index['assets'][asset_id]
        if target['state'] not in ('CREATING','FAILED'):raise StorageError('POOL_BUNDLE_STATE_CHANGED')
        if target['state']=='FAILED':target['recovered_by_full_validation']=True
        target.update(state='COMPLETED',files=files,directories=dirs,integrity_verified=True,
                      completed_at=now(),local_validation=result)
        pool._write('index.json',index);pool._checkpoint(index)
    return result


def check_tree(pool,asset):
    files,dirs=tree(asset['path'])
    if files!=sorted(asset['files'],key=lambda f:f['path']) or dirs!=sorted(asset['directories'],key=lambda f:f['path']):
        raise StorageError('POOL_BUNDLE_TREE_CHANGED')


def check_layout(pool,asset):
    files=[];dirs=[]
    for parent,children,names in os.walk(safe_path(Path(asset['path'])),followlinks=False):
        current=safe_path(Path(parent));s=current.stat()
        dirs.append({'path':str(current),'file_id':f'{s.st_dev}:{s.st_ino}'})
        for name in children:safe_path(current/name)
        files.extend(str(safe_path(current/name)) for name in names)
    if sorted(files)!=sorted(f['path'] for f in asset['files']) or sorted(dirs,key=lambda d:d['path'])!=sorted(asset['directories'],key=lambda d:d['path']):
        raise StorageError('POOL_BUNDLE_TREE_CHANGED')


def remove_empty_directories(pool,asset):
    # Payload files were removed by held verified handles. rmdir only succeeds
    # for an empty, same-identity directory; it never removes unknown children.
    for record in sorted(asset['directories'],key=lambda d:len(Path(d['path']).parts),reverse=True):
        path=safe_path(Path(record['path']));s=path.stat()
        if f'{s.st_dev}:{s.st_ino}'!=record['file_id'] or not path.is_relative_to(pool.root/'staging'):
            raise StorageError('POOL_BUNDLE_DIRECTORY_CHANGED')
        path.rmdir()


def valid_release_proof(pool,asset):
    try:
        path=safe_path(Path(asset['roundtrip_proof']))
        if not path.is_relative_to(pool.root/'kopia') or sha256_file(path)!=asset['roundtrip_proof_sha256']:return False
        proof=validate_seal(json.loads(path.read_text('utf-8')),'POOL_KOPIA_ROUNDTRIP_V1')
        verification=proof['snapshot_verification']
        return (proof['status']=='ROUNDTRIP_PASS' and proof['plan_sha256']==asset['plan']['document_sha256']
            and asset['asset_id'] in (proof['stage_asset_id'],proof['restore_asset_id'])
            and proof['base_asset_id']==asset['source_asset'] and verification['status']=='SNAPSHOT_VERIFIED'
            and verification['verification']=='KOPIA_100_PERCENT_FILES'
            and verification['snapshot_id']==proof['snapshot_id'])
    except (StorageError,OSError,ValueError,KeyError):return False


def authorize_release(pool,asset_id,proof_path):
    with pool._lock() as index:
        a=index['assets'][asset_id]
        if a['state']=='DELETED':return
        check_tree(pool,a)
        a.update(roundtrip_proof=str(proof_path),roundtrip_proof_sha256=sha256_file(Path(proof_path)))
        if not valid_release_proof(pool,a):raise StorageError('POOL_BUNDLE_RELEASE_PROOF_INVALID')
        a.update(restore_verified=True,recovery_required=False)
        index['tasks'][a['task_id']].update(state='COMPLETED',finished_at=now())
        pool._write('index.json',index)
