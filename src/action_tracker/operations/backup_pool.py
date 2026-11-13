"""Unified local copy pool, bounded admission and preauthorized owned cleanup.

No historic adoption, business schema migration, Kopia enablement or source writes.
Task/batch commit-chain integration is deliberately the following development phase.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .backup_sets import seal, validate_seal, write_immutable
from . import pool_files
from .storage_foundation import (StorageError, verified_backup, read_database,
                                database_identity, sha256_file)
from .cleanup_executor import safe_path

GIB = 1024**3
OVERHEAD = 64 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


class PoolCapacityError(StorageError):
    def __init__(self, report):
        self.report = report
        super().__init__('BACKUP_POOL_CAPACITY_BLOCKED')


class PoolCleanupError(StorageError):
    def __init__(self,report):
        self.report=report
        super().__init__('BACKUP_POOL_CLEANUP_INCOMPLETE')


@dataclass(frozen=True)
class PoolPolicy:
    max_bytes: int = 5 * GIB
    target_bytes: int = 4 * GIB
    automatic_cleanup: bool = True

    def __post_init__(self):
        if (type(self.max_bytes) is not int or type(self.target_bytes) is not int
                or not 0 < self.target_bytes < self.max_bytes
                or type(self.automatic_cleanup) is not bool):
            raise StorageError('POOL_POLICY_INVALID')


class BackupPool:
    def __init__(self, root, *, policy=None, protected_paths=(), initialize=False):
        self.root = safe_path(Path(root).absolute(), must_exist=False)
        self.policy = policy or PoolPolicy()
        self.protected = [safe_path(Path(p).absolute(), must_exist=False) for p in protected_paths]
        if os.name == 'nt':
            self.protected += [Path(p).resolve() for p in (
                'F:/ActionSKUTracker/runtime/db', 'F:/ActionSKUTracker/runtime/master',
                'F:/ActionSKUTracker/runtime/backups', 'F:/Action_Master', 'F:/按日期整理')]
        if self.root == Path(self.root.anchor) or any(
                self.root.is_relative_to(p) or p.is_relative_to(self.root) for p in self.protected):
            raise StorageError('POOL_PROTECTED_PATH_OVERLAP')
        marker = self.root / 'pool.json'
        if not marker.exists():
            if not initialize:raise StorageError('POOL_NOT_INITIALIZED')
            if self.root.exists() and any(self.root.iterdir()):raise StorageError('POOL_ROOT_NOT_EMPTY')
            self.root.mkdir(parents=True, exist_ok=True)
            for name in ('active', 'staging', 'cleanup', 'leases'): (self.root/name).mkdir(exist_ok=True)
            # Initializers are serialized using an OS-released lock too.
            with pool_files.os_lock(self.root/'pool.lock', wait=30):
                if not marker.exists():
                    write_immutable(marker, seal({'contract':'BACKUP_POOL_ROOT_V1', 'root':str(self.root),
                        'token':uuid.uuid4().hex, 'policy':asdict(self.policy),
                        'protected_paths':[str(p) for p in self.protected], 'created_at':now()}))
                    self._write('index.json', {'contract':'BACKUP_POOL_INDEX_V1',
                        'token':self._marker()['token'], 'assets':{}, 'tasks':{}, 'latest':{}, 'sequence':0})
        for path in self._marker().get('protected_paths',[]):
            protected=safe_path(Path(path),must_exist=False)
            if self.root.is_relative_to(protected) or protected.is_relative_to(self.root):
                raise StorageError('POOL_PROTECTED_PATH_OVERLAP')
            if protected not in self.protected:self.protected.append(protected)

    def _marker(self):
        safe_path(self.root)
        for name in ('active','staging','cleanup','leases','pool.json'):
            safe_path(self.root/name)
        result = validate_seal(json.loads((self.root/'pool.json').read_text('utf-8')), 'BACKUP_POOL_ROOT_V1')
        if result['root'] != str(self.root) or result['policy'] != asdict(self.policy):
            raise StorageError('POOL_IDENTITY_OR_POLICY_CHANGED')
        return result

    def _write(self, name, value):
        target = safe_path(self.root/name,must_exist=False)
        temporary = safe_path(self.root/'staging'/('control-'+uuid.uuid4().hex+'.tmp'),must_exist=False)
        try:
            with temporary.open('xb') as stream:
                stream.write(json.dumps(seal(value), ensure_ascii=False, sort_keys=True).encode('utf-8'))
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            # Unpublished control records contain no database payload.
            if temporary.exists():temporary.unlink()

    def _load(self):
        index = validate_seal(json.loads(safe_path(self.root/'index.json').read_text('utf-8')), 'BACKUP_POOL_INDEX_V1')
        if index['token'] != self._marker()['token']:raise StorageError('POOL_OWNER_CHANGED')
        return {k:v for k,v in index.items() if k!='document_sha256'}

    @contextmanager
    def _lock(self):
        self._marker()
        with pool_files.os_lock(self.root/'pool.lock', wait=30):
            yield self._load()

    def _audit(self, event, **facts):
        payload = seal({'contract':'BACKUP_POOL_AUDIT_V1','event':event,'time':now(),**facts})
        path=self.root/'cleanup'/(uuid.uuid4().hex+'.json');write_immutable(path,payload)
        return str(path)

    def _usage(self):
        total=logical=0;seen=set();uncertain=[];unknown=[]
        index=self._load()
        known={Path(p) for a in index['assets'].values() for p in a['paths']}
        known.update(Path(p) for p in index.get('task_manifests',{}).values())
        known.update(Path(p) for p in index.get('kopia_controls',{}).values())
        for parent, dirs, files in os.walk(self.root, followlinks=False):
            for name in list(dirs):
                p=Path(parent)/name
                try:safe_path(p)
                except StorageError:
                    dirs.remove(name);uncertain.append(str(p))
            for name in files:
                p=Path(parent)/name
                try:
                    safe_path(p);st=p.stat();key=(st.st_dev,st.st_ino)
                    logical+=st.st_size
                    if key not in seen:total+=pool_files.allocation(p);seen.add(key)
                    control=(p in {self.root/'pool.json',self.root/'index.json',self.root/'pool.lock'}
                             or (p.is_relative_to(self.root/'kopia') and index.get('kopia_owned_root')==str(self.root/'kopia'))
                             or p.parent==self.root/'cleanup'
                             or (p.parent==self.root/'leases' and (p.stem in index['assets']
                                 or p.name in index.get('source_locks',{}))))
                    if p not in known and not control:unknown.append(str(p))
                except (OSError,StorageError):uncertain.append(str(p))
        return {'allocated_bytes':total,'logical_bytes':logical,'uncertain_paths':uncertain,'unknown_files':unknown,
                'measurement':'FileStandardInfo.AllocationSize' if os.name=='nt' else 'st_blocks*512'}

    def _outstanding(self, index):
        total=0
        for a in index['assets'].values():
            if a['state']=='CREATING':
                actual=0
                for p in a['paths']:
                    if Path(p).is_file():actual+=pool_files.allocation(p)
                total+=max(0,a['reservation_bytes']-actual)
        return total

    def _valid(self, asset, *, restore=True):
        if (asset['state']!='COMPLETED' or not asset.get('integrity_verified')
                or (restore and not asset.get('restore_verified'))):return False
        try:
            if restore and asset['kind']=='database_backup':
                path=safe_path(Path(asset['restore_report']))
                if path.parent!=self.root/'cleanup' or sha256_file(path)!=asset.get('restore_report_sha256'):return False
                proof=validate_seal(json.loads(path.read_text('utf-8')),'BACKUP_POOL_AUDIT_V1')
                if (proof.get('event')!='INDEPENDENT_RESTORE_PASS' or proof.get('base_asset_id')!=asset['asset_id']
                        or proof.get('sha256')!=asset['sha256'] or proof.get('identity')!=asset['identity']):return False
            return bool(asset.get('files')) and all(pool_files.file_record(Path(f['path']))==f for f in asset['files'])
        except (StorageError,OSError,KeyError,ValueError):return False

    def _reason(self, a, index):
        if a['state']!='COMPLETED':return 'ASSET_'+a['state']
        if index['tasks'].get(a['task_id'],{}).get('state')!='COMPLETED':return 'TASK_NOT_COMPLETED'
        if a.get('references'):return 'REFERENCED'
        if a.get('recovery_required'):return 'RECOVERY_RESPONSIBILITY'
        if not a.get('integrity_verified') or not a.get('restore_verified'):return 'UNVERIFIED'
        if a['asset_id'] in index['latest'].values():return 'LATEST_VALID_RECOVERY'
        if a['kind']=='database_backup':
            if not self._valid(a):return 'RESTORE_PROOF_OR_BACKUP_CHANGED'
            alternate=index['assets'].get(a.get('alternative_id'))
            if not alternate or alternate['asset_id']==a['asset_id'] or not self._valid(alternate):return 'NO_INDEPENDENT_RECOVERY'
            if alternate.get('sha256')!=a.get('sha256') or alternate.get('identity')!=a.get('identity'):return 'ALTERNATIVE_VERSION_MISMATCH'
        elif a['kind'] in ('restore_test','kopia_stage','kopia_bundle'):
            source=index['assets'].get(a.get('source_asset'))
            if not source or not self._valid(source):return 'SOURCE_RECOVERY_NOT_VALID'
            if a['kind']=='kopia_bundle':
                from .pool_bundles import valid_release_proof
                if not valid_release_proof(self,a):return 'KOPIA_ROUNDTRIP_NOT_VERIFIED'
        elif a['kind']!='temporary':return 'UNKNOWN_KIND'
        return None

    def _delete(self, a, index):
        reason=self._reason(a,index)
        if reason: return {'asset_id':a['asset_id'],'status':'KEPT','reason':reason}
        mutation_started=False
        try:
            with pool_files.os_lock(self.root/'leases'/(a['asset_id']+'.lock')):
                before=self._usage()['allocated_bytes'];free_before=shutil.disk_usage(self.root).free
                def check():
                    if self._reason(a,index):raise StorageError('POOL_DISPOSITION_CHANGED')
                # A durable intent makes a partial file-group deletion visible after a crash.
                self._audit('DELETE_INTENT',asset_id=a['asset_id'],files=a['files'])
                mutation_started=True
                if a['kind']=='kopia_bundle':
                    from .pool_bundles import check_tree,check_layout,remove_empty_directories
                    check_tree(self,a)
                    pool_files.delete_group(a['files'],root=self.root,final_check=lambda:(check(),check_layout(self,a)),
                                            owned_directory=Path(a['path']))
                    remove_empty_directories(self,a)
                else:pool_files.delete_group(a['files'],root=self.root,final_check=check)
                a['state']='DELETED';a['deleted_at']=now()
                self._write('index.json',index)
                after=self._usage()['allocated_bytes'];free_after=shutil.disk_usage(self.root).free
                result={'asset_id':a['asset_id'],'status':'DELETED','files':a['files'],
                    'pool_allocated_bytes_before':before,'pool_allocated_bytes_after':after,
                    'actual_pool_bytes_released':max(0,before-after),
                    'allocated_file_bytes_deleted':sum(f['allocated_bytes'] for f in a['files']),
                    'observed_volume_free_delta':free_after-free_before}
                self._audit('ASSET_DELETED',**result)
                return result
        except (StorageError,OSError) as exc:
            busy=str(exc) in ('POOL_FILE_IN_USE','FILE_IN_USE_OR_ACCESS_DENIED')
            return {'asset_id':a['asset_id'],'status':'KEPT' if busy else 'ERROR',
                    'reason':str(exc),'mutation_may_have_started':mutation_started}

    def _checkpoint(self,index,expected=0,*,force_temporaries=False,preview=False):
        if type(expected) is not int or expected<0:raise StorageError('POOL_RESERVATION_INVALID')
        before=self._usage();outstanding=self._outstanding(index)
        control_reserve=max(OVERHEAD,2*(self.root/'index.json').stat().st_size+OVERHEAD)
        projected=before['allocated_bytes']+outstanding+expected+control_reserve
        trigger=projected>=self.policy.max_bytes
        result={'usage_before':before,'estimated_new_bytes':expected,'outstanding_reserved_bytes':outstanding,
            'projected_bytes':projected,'triggered':trigger,'max_bytes':self.policy.max_bytes,
            'target_bytes':self.policy.target_bytes,'control_reserve_bytes':control_reserve,'actions':[],'preview':preview}
        candidates=sorted(index['assets'].values(),key=lambda a:(a['kind']=='database_backup',a['sequence']))
        if trigger or force_temporaries:
            for a in candidates:
                current=self._usage()['allocated_bytes']+self._outstanding(index)
                if trigger:
                    if current<=self.policy.target_bytes and current+expected+control_reserve<self.policy.max_bytes:break
                elif a['kind']=='database_backup':continue
                reason=self._reason(a,index)
                if reason:
                    if a['state']!='DELETED':result['actions'].append({'asset_id':a['asset_id'],'status':'KEPT','reason':reason})
                    continue
                if preview:result['actions'].append({'asset_id':a['asset_id'],'status':'ELIGIBLE'});continue
                if self.policy.automatic_cleanup:result['actions'].append(self._delete(a,index))
        result['usage_after']=self._usage();result['projected_after']=result['usage_after']['allocated_bytes']+self._outstanding(index)+expected+control_reserve
        result['blocked']=(bool(result['usage_after']['uncertain_paths']) or result['projected_after']>=self.policy.max_bytes
                           or (trigger and result['usage_after']['allocated_bytes']+self._outstanding(index)>self.policy.target_bytes))
        errors=[x for x in result['actions'] if x.get('status')=='ERROR']
        result['status']=('PREVIEW' if preview else 'CLEANUP_INCOMPLETE' if errors else 'BLOCKED' if result['blocked'] else 'PASS')
        if not preview and (trigger or force_temporaries):
            self._audit('CAPACITY_CHECKPOINT',report=result)
        if errors and not preview:raise PoolCleanupError(result)
        if result['blocked'] and not preview:raise PoolCapacityError(result)
        return result

    def checkpoint(self,expected=0,*,force_temporaries=False,preview=False):
        with self._lock() as index:return self._checkpoint(index,expected,force_temporaries=force_temporaries,preview=preview)

    def status(self):
        with self._lock() as index:
            return {'contract':'BACKUP_POOL_STATUS_V1','root':str(self.root),'policy':asdict(self.policy),
                'usage':self._usage(),'outstanding_reserved_bytes':self._outstanding(index),
                'latest':index['latest'],'assets':list(index['assets'].values()),'tasks':index['tasks']}

    def _reserve(self,specs):
        for s in specs:
            if (type(s.get('expected_bytes')) is not int or s['expected_bytes']<0
                    or s.get('kind') not in ('database_backup','restore_test','kopia_stage','temporary','kopia_bundle')):
                raise StorageError('POOL_RESERVATION_INVALID')
        with self._lock() as index:
            expected=sum(s['expected_bytes']+OVERHEAD for s in specs)
            self._checkpoint(index,expected)
            if shutil.disk_usage(self.root).free<expected+OVERHEAD:raise StorageError('POOL_VOLUME_SPACE_INSUFFICIENT')
            result=[]
            for s in specs:
                if not s['task_id'] or not s.get('source') or not s.get('purpose'):raise StorageError('POOL_ASSET_IDENTITY_REQUIRED')
                task=index['tasks'].setdefault(s['task_id'],{'state':'RUNNING','created_at':now()})
                if task['state']!='RUNNING':raise StorageError('POOL_TASK_NOT_RUNNING')
                token=uuid.uuid4().hex;index['sequence']+=1
                folder='active' if s['kind']=='database_backup' else 'staging'
                suffix='.sqlite3' if s['kind']!='temporary' else ('.json' if s.get('format')=='json' else '.bin')
                path=self.root/folder/(token+suffix)
                if s['kind']=='kopia_bundle':path=self.root/'staging'/('bundle-'+token)
                a={**s,'asset_id':token,'path':str(path),'paths':[str(path)],'sequence':index['sequence'],
                   'state':'CREATING','created_at':now(),'references':[],
                   'recovery_required':s.get('recovery_required',True),
                   'reservation_bytes':s['expected_bytes']+OVERHEAD,'files':[],
                   'integrity_verified':False,'restore_verified':False}
                if s['kind']=='database_backup':a['paths'] += [str(path)+'.asset.json',str(path)+'.failure.json']
                if s['kind']=='kopia_bundle':
                    a['paths']=[str(path/'backup-set.json')]+[str(path/v['snapshot_path']) for v in s['plan']['assets']]
                index['assets'][token]=a;result.append(dict(a))
            self._write('index.json',index)
            return result

    def _db_identity(self,path):
        with read_database(path,immutable=True) as db:
            value=database_identity(db);meta=value['metadata']
            if (meta.get('schema_family')!='ACTION_SQLITE_DATA' or meta.get('schema_version')!='2.0.0'
                    or meta.get('database_role') not in ('PRIMARY','SHADOW')):raise StorageError('POOL_DATABASE_IDENTITY_INVALID')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                raise StorageError('POOL_DATABASE_INTEGRITY_FAILED')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row=db.execute('SELECT * FROM export_sync WHERE commit_id=?',(value['commit_id'],)).fetchone() if 'export_sync' in tables else None
            value['export_sync']=dict(row) if row else None
            return value

    def _complete(self,asset_id):
        with self._lock() as index:
            a=index['assets'][asset_id];path=Path(a['path'])
            safe_path(path)
            files=[pool_files.file_record(Path(p)) for p in a['paths'] if Path(p).exists()]
            if sum(f['size_bytes'] for f in files)>a['reservation_bytes']:raise StorageError('POOL_RESERVATION_EXCEEDED')
            with path.open('rb') as stream:is_db=stream.read(16)==b'SQLite format 3\0'
            if a['kind']=='temporary' and is_db:raise StorageError('POOL_DATABASE_REQUIRES_RECOVERY_RECORD')
            a.update(files=files,sha256=sha256_file(path),state='COMPLETED',completed_at=now(),integrity_verified=True)
            if a['kind']!='temporary':a['identity']=self._db_identity(path)
            else:a['restore_verified']=True
            self._write('index.json',index)
            self._checkpoint(index)
            return dict(a)

    def _fail(self,assets,error):
        with self._lock() as index:
            for original in assets:
                a=index['assets'][original['asset_id']]
                a.update(state='FAILED',error=type(error).__name__)
                index['tasks'][a['task_id']]['state']='FAILED'
            self._write('index.json',index)
            self._audit('OPERATION_FAILED',asset_ids=[a['asset_id'] for a in assets],error_type=type(error).__name__)

    @contextmanager
    def temporary(self,*,task_id,expected_bytes,source,purpose,discardable=False,file_format='binary'):
        if type(discardable) is not bool:raise StorageError('POOL_DISCARDABLE_BOOLEAN_REQUIRED')
        if file_format not in ('binary','json'):raise StorageError('POOL_FORMAT_INVALID')
        a=self._reserve([{'task_id':task_id,'kind':'temporary','expected_bytes':expected_bytes,
                         'source':str(source),'purpose':purpose,'recovery_required':not discardable,'format':file_format}])[0]
        try:
            with pool_files.os_lock(self.root/'leases'/(a['asset_id']+'.lock')):
                yield a
                self._complete(a['asset_id'])
        except BaseException as exc:
            self._fail([a],exc);raise

    def finish_task(self,task_id,*,state='COMPLETED'):
        if state not in ('COMPLETED','FAILED','UNKNOWN'):raise StorageError('POOL_TASK_STATE_INVALID')
        with self._lock() as index:
            if task_id not in index['tasks']:raise StorageError('POOL_TASK_MISSING')
            if state=='COMPLETED' and any(a['task_id']==task_id and a['state'] not in ('COMPLETED','DELETED') for a in index['assets'].values()):
                raise StorageError('POOL_TASK_HAS_INCOMPLETE_ASSETS')
            index['tasks'][task_id].update(state=state,finished_at=now());self._write('index.json',index)
            return self._checkpoint(index,force_temporaries=True)

    def reference(self,asset_id,name,*,release=False):
        if not name:raise StorageError('POOL_REFERENCE_NAME_REQUIRED')
        with self._lock() as index:
            refs=index['assets'][asset_id]['references']
            if release:
                if name not in refs:raise StorageError('POOL_REFERENCE_NOT_FOUND')
                refs.remove(name)
            elif name not in refs:refs.append(name)
            self._write('index.json',index)

    def release_recovery(self,asset_id,*,replacement_id):
        with self._lock() as index:
            a=index['assets'][asset_id];b=index['assets'][replacement_id]
            if (index['tasks'][a['task_id']]['state']!='COMPLETED' or asset_id==replacement_id
                    or not self._valid(a) or not self._valid(b)
                    or a['sha256']!=b['sha256'] or a['identity']!=b['identity']):
                raise StorageError('POOL_REPLACEMENT_RECOVERY_NOT_VALID')
            a.update(recovery_required=False,alternative_id=replacement_id)
            self._write('index.json',index)

    def verify(self,asset_id):
        with self._lock() as index:
            a=dict(index['assets'][asset_id])
            if not self._valid(a):raise StorageError('POOL_ASSET_OR_RESTORE_NOT_VERIFIED')
            with pool_files.os_lock(self.root/'leases'/(asset_id+'.lock')):
                if a['kind']!='temporary' and self._db_identity(Path(a['path']))!=a['identity']:
                    raise StorageError('POOL_RESTORED_IDENTITY_CHANGED')
                return {'status':'PASS','asset_id':asset_id,'sha256':a['sha256'],'identity':a.get('identity')}

    def backup(self,source,*,task_id,code_version=None):
        source=safe_path(Path(source).absolute())
        if source.is_relative_to(self.root):raise StorageError('POOL_BACKUP_SOURCE_IS_POOL_ASSET')
        wal=Path(str(source)+'-wal');size=source.stat().st_size+(wal.stat().st_size if wal.exists() else 0)
        verify_task='verify_'+uuid.uuid4().hex
        assets=self._reserve([{'task_id':task_id,'kind':'database_backup','expected_bytes':size+OVERHEAD,
            'source':str(source),'purpose':'recovery_baseline'},
            {'task_id':verify_task,'kind':'restore_test','expected_bytes':size+OVERHEAD,
             'source':str(source),'purpose':'independent_restore','recovery_required':False}])
        base,restored=assets;result=None
        try:
            with ExitStack() as stack:
                for a in assets:stack.enter_context(pool_files.os_lock(self.root/'leases'/(a['asset_id']+'.lock')))
                p1=verified_backup(source,Path(base['path']),run_id=task_id,code_version=code_version)
                original=self._complete(base['asset_id'])
                with Path(base['path']).open('rb') as src,Path(restored['path']).open('xb') as dst:
                    shutil.copyfileobj(src,dst,1024*1024);dst.flush();os.fsync(dst.fileno())
                checked=self._complete(restored['asset_id'])
                if checked['sha256']!=original['sha256'] or checked['identity']!=original['identity']:
                    raise StorageError('POOL_INDEPENDENT_RESTORE_MISMATCH')
                with self._lock() as index:
                    a=index['assets'][base['asset_id']];b=index['assets'][restored['asset_id']]
                    a['restore_verified']=True;b.update(restore_verified=True,source_asset=a['asset_id'])
                    index['tasks'][verify_task]['state']='COMPLETED'
                    old=index['latest'].get(str(source))
                    if not old or index['assets'][old]['sequence']<a['sequence']:index['latest'][str(source)]=a['asset_id']
                    proof=self._audit('INDEPENDENT_RESTORE_PASS',base_asset_id=a['asset_id'],restore_asset_id=b['asset_id'],
                        sha256=a['sha256'],identity=a['identity'],integrity='PASS',foreign_keys='PASS')
                    a['restore_report']=proof;a['restore_report_sha256']=sha256_file(Path(proof));self._write('index.json',index)
                    result={**p1,'pool_asset_id':a['asset_id'],'pool_root':str(self.root),
                            'restore_rehearsed':True,'restore_report':proof,'permanent_restore_copy_created':False}
        except BaseException as exc:
            self._fail(assets,exc);raise
        self.checkpoint(force_temporaries=True)
        return result


def configured_pool(cfg):
    raw=cfg.get('backup_pool') or {}
    if type(raw.get('enabled',False)) is not bool:raise StorageError('POOL_ENABLED_BOOLEAN_REQUIRED')
    if not raw.get('enabled'):return None
    allowed={'enabled','root','max_bytes','target_bytes','automatic_cleanup'}
    if set(raw)-allowed:raise StorageError('POOL_CONFIG_UNKNOWN_FIELDS')
    policy=PoolPolicy(**{k:raw[k] for k in ('max_bytes','target_bytes','automatic_cleanup') if k in raw})
    if policy.max_bytes!=5*GIB or policy.target_bytes!=4*GIB:raise StorageError('POOL_PRODUCTION_LIMIT_MUST_BE_5_AND_4_GIB')
    root=Path(raw.get('root','runtime/backup_pool'))
    if not root.is_absolute():root=Path(cfg['project_root'])/root
    protected=[Path(v) for k,v in cfg.get('paths',{}).items() if k in ('master','backups')]
    from ..database.integration import database_path
    protected.append(database_path(cfg))
    return BackupPool(root,policy=policy,protected_paths=protected,initialize=True)
