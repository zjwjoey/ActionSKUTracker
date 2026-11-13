"""Task-owned recovery baselines and durable, row-level transaction journals.

No business schema, historic adoption, automatic production restore or Kopia.
The pool remains the sole owner of full copies. SQLite keeps atomic rollback.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import uuid
from threading import RLock

from .backup_pool import BackupPool, configured_pool, now
from .backup_sets import seal, validate_seal, write_immutable
from .pool_files import os_lock
from .storage_foundation import StorageError, read_database, database_identity, sha256_file
from .cleanup_executor import safe_path

_policies = {}
_policy_lock = RLock()
_current = ContextVar('task_backup_current', default=None)


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def key(path):
    return os.path.normcase(str(Path(path).resolve()))


def register_policy(cfg, path):
    raw=cfg.get('task_backup') or {}
    if type(raw.get('enabled',False)) is not bool:raise StorageError('TASK_BACKUP_BOOLEAN_REQUIRED')
    if set(raw)-{'enabled'}:raise StorageError('TASK_BACKUP_CONFIG_UNKNOWN_FIELDS')
    if raw.get('enabled'):
        if (cfg.get('backup_pool') or {}).get('enabled') is not True:
            raise StorageError('TASK_BACKUP_REQUIRES_ENABLED_POOL')
        with _policy_lock:
            previous=_policies.get(key(path))
            if previous:
                def root(c):
                    p=Path(c['backup_pool'].get('root','runtime/backup_pool'))
                    return key(p if p.is_absolute() else Path(c['project_root'])/p)
                if root(previous)!=root(cfg):raise StorageError('TASK_BACKUP_POOL_CHANGED_IN_ACTIVE_PROCESS')
            _policies[key(path)]=dict(cfg)
    elif _policies and key(path) in _policies:raise StorageError('TASK_BACKUP_POLICY_CANNOT_BE_DISABLED_IN_ACTIVE_PROCESS')


def current_task(path):
    current=_current.get()
    return current if current and key(current[0].source)==key(path) else None


@contextmanager
def task_scope(cfg, *, task_id, batch_id=None, source=None, inherit=False):
    raw=cfg.get('task_backup') or {}
    if type(raw.get('enabled',False)) is not bool:raise StorageError('TASK_BACKUP_BOOLEAN_REQUIRED')
    if not raw.get('enabled'):
        if source is not None:register_policy(cfg,source)
        yield None;return
    if source is None:
        from ..database.integration import database_path
        source=database_path(cfg)
    register_policy(cfg,source)
    if key(source) not in _policies:
        yield None;return
    parent=current_task(source)
    if parent:
        if not inherit and str(task_id)!=parent[0].task_id:raise StorageError('TASK_BACKUP_NESTED_TASK_MISMATCH')
        manager=parent[0]
    else:manager=TaskBackupManager(configured_pool(cfg),source,task_id=str(task_id))
    token=_current.set((manager,str(batch_id or task_id)))
    try:yield manager
    except BaseException as exc:
        if parent is None and manager.path.exists():
            try:
                with manager.source_lock():
                    state=manager.load();completed=state['state']=='COMPLETED'
                    if not completed:state['state']='EVIDENCE_PENDING' if state.get('pending') else 'FAILED'
                    state['error_type']=type(exc).__name__;manager.save(state)
                    if not completed:manager.pool.finish_task(manager.owner,state='FAILED')
            except Exception as evidence_error:
                exc.add_note('Recovery baseline retained; task failure evidence: '+str(evidence_error))
        raise
    finally:_current.reset(token)


def manager_for(path):
    active=current_task(path)
    if active:return active
    if not _policies:return None
    cfg=_policies.get(key(path))
    if cfg is None:return None
    raise StorageError('TASK_BACKUP_TASK_SCOPE_REQUIRED')


def policy_enabled(path):
    return current_task(path) is not None or bool(_policies) and key(path) in _policies


def formal_write(fn):
    """One public operation owns all of its connections unless a task owns it.

    Optional backup_task_id/backup_batch_id are management identities, never
    replacements for the writer's run_id, Commit ID or approval evidence.
    """
    @wraps(fn)
    def wrapped(*args,**kwargs):
        task_id=kwargs.pop('backup_task_id',None);batch_id=kwargs.pop('backup_batch_id',None)
        first=args[0] if args else kwargs.get('path')
        path=Path(first.path if hasattr(first,'path') else first)
        parent=current_task(path)
        cfg=_policies.get(key(path)) if _policies else None
        if parent:
            if task_id is not None and str(task_id)!=parent[0].task_id:
                raise StorageError('TASK_BACKUP_NESTED_TASK_MISMATCH')
            token=_current.set((parent[0],str(batch_id or kwargs.get('run_id') or parent[1])))
            try:return fn(*args,**kwargs)
            finally:_current.reset(token)
        if cfg is None:
            if task_id is not None:raise StorageError('TASK_BACKUP_POLICY_REQUIRED')
            return fn(*args,**kwargs)
        run_id=kwargs.get('run_id') or (getattr(args[1],'run_id',None) if len(args)>1 else None)
        task_id=str(task_id or run_id or (fn.__name__+'-'+uuid.uuid4().hex))
        with task_scope(cfg,task_id=task_id,batch_id=batch_id or run_id or task_id,source=path):
            return fn(*args,**kwargs)
    return wrapped


def identity(db, source):
    value=database_identity(db)
    schema=[list(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    value['schema_sha256']=digest(schema)
    stat=Path(source).stat();value['source_file_id']=f'{stat.st_dev}:{stat.st_ino}'
    return value


class PostCommitEvidenceError(StorageError):
    committed=True
    def __init__(self, reason, *, commit_id, intent):
        self.commit_id=commit_id;self.intent=intent
        super().__init__('TASK_BACKUP_COMMITTED_EVIDENCE_PENDING:'+str(reason))


class TaskBackupManager:
    def __init__(self,pool,source,*,task_id):
        if not task_id or pool is None:raise StorageError('TASK_BACKUP_ID_AND_POOL_REQUIRED')
        self.pool=pool;self.source=safe_path(Path(source).absolute(),must_exist=False);self.task_id=str(task_id)
        if self.source.is_relative_to(pool.root):raise StorageError('TASK_BACKUP_SOURCE_IS_POOL')
        self.token=digest({'source':key(self.source),'task_id':self.task_id})
        self.owner='task-'+self.token
        self.relative='active/task-'+self.token+'.json'
        self.path=pool.root/self.relative
        self.verified=None
        self.event_cache={}

    @contextmanager
    def source_lock(self):
        # Different tasks for the same database serialize, including metadata.
        filename='source-'+digest(key(self.source))+'.lock'
        path=safe_path(self.pool.root/'leases'/filename,must_exist=False)
        with self.pool._lock() as index:
            controls=index.setdefault('source_locks',{})
            if filename not in controls:
                controls[filename]={'source':key(self.source),'purpose':'shared database writer arbitration',
                                    'sha256':hashlib.sha256(b'0').hexdigest()}
                self.pool._write('index.json',index)
        if path.exists():
            stat=path.stat()
            if stat.st_nlink!=1 or stat.st_size!=1:raise StorageError('TASK_BACKUP_SOURCE_LOCK_IDENTITY_INVALID')
        with os_lock(path,wait=30) as held:
            stat=os.fstat(held[0].fileno())
            if stat.st_nlink!=1 or stat.st_size!=1 or held[0].read()!=b'0':
                raise StorageError('TASK_BACKUP_SOURCE_LOCK_IDENTITY_INVALID')
            yield

    def load(self):
        value=validate_seal(json.loads(safe_path(self.path).read_text('utf-8')),'TASK_BACKUP_V1')
        if value['task_id']!=self.task_id or value['source']!=key(self.source):
            raise StorageError('TASK_BACKUP_BINDING_CHANGED')
        return {k:v for k,v in value.items() if k!='document_sha256'}

    def save(self,value):
        with self.pool._lock() as index:
            self.pool._checkpoint(index,2*len(json.dumps(value,ensure_ascii=False).encode('utf-8'))+65536)
            self.pool._write(self.relative,value)
            index.setdefault('task_manifests',{})[self.token]=str(self.path)
            self.pool._write('index.json',index)

    def verify_base(self,state):
        with self.pool._lock() as index:
            asset=index['assets'].get(state['base_asset_id'])
            if not asset or asset.get('sha256')!=state['base_sha256']:
                raise StorageError('TASK_BACKUP_BASE_BINDING_CHANGED')
            stamps=[]
            for file in asset['files']:
                p=safe_path(Path(file['path']));s=p.stat()
                stamps.append((str(p),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_nlink))
            proof=safe_path(Path(asset['restore_report']))
            stamps.append(sha256_file(proof))
        if stamps!=self.verified:
            self.pool.verify(state['base_asset_id']);self.verified=stamps

    def ensure_locked(self):
        if not self.path.exists():
            with self.pool._lock() as index:
                existing=[a for a in index['assets'].values()
                          if a['task_id']==self.owner and a['kind']=='database_backup']
            if existing:
                # Recover an orphan publication only with full logical parity;
                # an incomplete/failed capture can never be silently replaced.
                if len(existing)!=1:raise StorageError('TASK_BACKUP_AMBIGUOUS_BASE')
                a=existing[0];self.pool.verify(a['asset_id'])
                from ..localization.history_audit import database_fingerprint
                with read_database(self.source) as src,read_database(Path(a['path']),immutable=True) as base:
                    if database_fingerprint(src)!=database_fingerprint(base):
                        raise StorageError('TASK_BACKUP_ORPHAN_BASE_SOURCE_CHANGED')
                result={'pool_asset_id':a['asset_id'],'sha256':a['sha256'],'backup_path':a['path']}
            else:
                from ..services.gitutil import git_commit_info
                result=self.pool.backup(self.source,task_id=self.owner,code_version=git_commit_info())
            with read_database(self.source) as db:current=identity(db,self.source)
            with read_database(Path(result['backup_path']),immutable=True) as db:
                base_identity=database_identity(db)
            if any(current[k]!=base_identity[k] for k in ('metadata','commit_id')):
                raise StorageError('TASK_BACKUP_SOURCE_CHANGED_DURING_BASE')
            state={'contract':'TASK_BACKUP_V1','task_id':self.task_id,'source':key(self.source),
                'base_asset_id':result['pool_asset_id'],'base_sha256':result['sha256'],
                'backup_path':result['backup_path'],'identity':current,'expected_head':current['commit_id'],
                'code_version':result.get('code_version','unknown'),
                'state':'RUNNING','created_at':now(),'transactions':[],'pending':None}
            self.pool.reference(state['base_asset_id'],'task:'+self.token)
            self.save(state)
        state=self.load();self.verify_base(state);self.verify_history(state)
        if state['state'] not in ('RUNNING','FAILED','EVIDENCE_PENDING','COMPLETED'):
            raise StorageError('TASK_BACKUP_NOT_RESUMABLE:'+state['state'])
        return state

    def ensure(self):
        with self.source_lock():
            state=self.ensure_locked()
            with read_database(self.source) as db:self.reconcile(state,db);self.check(state,db)
            return self.report(state)

    def report(self,state):
        return {'contract':'TASK_BACKUP_REPORT_V1','task_id':self.task_id,'pool_root':str(self.pool.root),
            'pool_asset_id':state['base_asset_id'],'backup_path':state['backup_path'],
            'sha256':state['base_sha256'],'base_commit_id':state['identity']['commit_id'],
            'expected_head':state['expected_head'],'integrity':'PASS','foreign_keys':'PASS',
            'commit_id':state['identity']['commit_id'],'source':str(self.source),
            'schema_family':state['identity']['metadata'].get('schema_family'),
            'schema_version':state['identity']['metadata'].get('schema_version'),
            'database_role':state['identity']['metadata'].get('database_role'),
            'code_version':state.get('code_version','unknown'),
            'restore_rehearsed':True,'permanent_restore_copy_created':False,'task_manifest':str(self.path)}

    def check(self,state,db):
        actual=identity(db,self.source)
        if (actual['commit_id']!=state['expected_head'] or
            any(actual[k]!=state['identity'][k] for k in ('metadata','schema_sha256','source_file_id'))):
            raise StorageError('TASK_BACKUP_EXTERNAL_HEAD_OR_IDENTITY_CHANGED')

    def reconcile(self,state,db):
        if not state.get('pending'):return
        intent,outcome=self.transaction_outcome(state,db)
        self.receipt(state,intent,committed=outcome=='COMMITTED',recovered=True)
        return outcome

    def transaction_outcome(self,state,db):
        intent=self.read_event(state['pending'])
        after=rows_match(db,intent['changes'],after=True)
        before=rows_match(db,intent['changes'],after=False)
        head=database_identity(db)['commit_id']
        if after and before and intent['after_head']==intent['before_head']:
            raise StorageError('TASK_BACKUP_PENDING_OUTCOME_UNKNOWN')
        if after and head==intent['after_head']:
            return intent,'COMMITTED'
        elif before and head==intent['before_head']:
            return intent,'ROLLED_BACK'
        else:raise StorageError('TASK_BACKUP_PENDING_OUTCOME_UNKNOWN')

    def read_event(self,ref):
        path=safe_path(Path(ref['path']))
        stat=path.stat();stamp=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_nlink,ref['sha256'])
        cached=self.event_cache.get(str(path))
        if cached and cached[0]==stamp:return cached[1]
        if stat.st_nlink!=1 or path.parent not in (self.pool.root/'active',self.pool.root/'staging') or sha256_file(path)!=ref['sha256']:
            raise StorageError('TASK_BACKUP_JOURNAL_CHANGED')
        value=validate_seal(json.loads(path.read_text('utf-8')),'TASK_BACKUP_EVENT_V1')
        if value.get('task_token')!=self.token:raise StorageError('TASK_BACKUP_JOURNAL_TASK_CHANGED')
        self.event_cache[str(path)]=(stamp,value)
        return value

    def verify_history(self,state):
        previous=None;head=state['identity']['commit_id']
        for ref in state['transactions']:
            receipt=self.read_event(ref);intent=self.read_event(receipt['intent'])
            if (receipt['event'] not in ('COMMITTED','ROLLED_BACK') or intent['previous_receipt']!=previous
                    or intent['before_head']!=head or digest(intent['changes'])!=intent['changes_sha256']):
                raise StorageError('TASK_BACKUP_COMMIT_CHAIN_CHANGED')
            head=receipt['after_head'];previous=ref
        if head!=state['expected_head']:raise StorageError('TASK_BACKUP_HEAD_CHAIN_MISMATCH')

    def event(self,payload):
        value=seal({'contract':'TASK_BACKUP_EVENT_V1','task_token':self.token,'time':now(),**payload})
        payload=json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')
        with self.pool.temporary(task_id=self.owner+'-journal-'+uuid.uuid4().hex,expected_bytes=len(payload)+65536,
                source=key(self.source),purpose='durable task transaction evidence',file_format='json') as asset:
            path=Path(asset['path'])
            with path.open('xb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        return {'path':str(path),'sha256':sha256_file(path)}

    def prepare(self,state,db,*,batch_id,changes):
        actual=identity(db,self.source)
        if any(actual[k]!=state['identity'][k] for k in ('metadata','schema_sha256','source_file_id')):
            raise StorageError('TASK_BACKUP_SCHEMA_OR_FILE_CHANGED')
        if not changes:return None
        if state['state']=='COMPLETED':raise StorageError('TASK_BACKUP_COMPLETED_TASK_CANNOT_WRITE')
        intent={'event':'PREPARED','task_id':self.task_id,'batch_id':batch_id,
            'before_head':state['expected_head'],'after_head':actual['commit_id'],
            'changes':changes,'changes_sha256':digest(changes),
            'previous_receipt':state['transactions'][-1] if state['transactions'] else None}
        state['pending']=self.event(intent);self.save(state)
        return intent

    def receipt(self,state,intent,*,committed,recovered=False):
        reference=self.event({'event':'COMMITTED' if committed else 'ROLLED_BACK',
            'batch_id':intent['batch_id'],'intent':state['pending'],'recovered':recovered,
            'before_head':intent['before_head'],'after_head':intent['after_head'] if committed else intent['before_head']})
        state['transactions'].append(reference)
        if committed:state['expected_head']=intent['after_head']
        state['pending']=None;state['state']='RUNNING';self.save(state)

    def finish(self,*,state='COMPLETED'):
        if state not in ('COMPLETED','FAILED','UNKNOWN'):raise StorageError('TASK_BACKUP_FINISH_STATE_INVALID')
        with self.source_lock():
            task=self.ensure_locked()
            with read_database(self.source) as db:self.reconcile(task,db);self.check(task,db)
            task['state']=state;task['finished_at']=now();self.save(task)
            # Keep durable task references and recovery responsibility. Do not
            # discard an old database version merely because a newer one exists.
            return self.pool.finish_task(self.owner,state=state)

    def recovery_plan(self):
        """Validate recovery even when PRIMARY is missing/corrupt; never restore it."""
        with self.source_lock():
            state=self.load();self.verify_base(state);self.verify_history(state)
            source_status='MISSING';head=None
            if self.source.exists():
                try:
                    with read_database(self.source) as db:head=database_identity(db)['commit_id']
                    source_status='EXPECTED_HEAD' if head==state['expected_head'] else 'EXTERNAL_HEAD_REVIEW_REQUIRED'
                except (OSError,sqlite3.Error,StorageError):source_status='UNREADABLE'
            return {**self.report(state),'automatic_restore':False,'production_database_modified':False,
                    'source_status':source_status,'current_head':head,'requires_exclusive_offline_restore':True}


def quote(name):return '"'+name.replace('"','""')+'"'


def rows_match(db,changes,*,after):
    grouped={}
    for change in changes:
        token=(change['table'],digest(change['key']))
        if after or token not in grouped:grouped[token]=change
    for change in grouped.values():
        keys=change['key'];where=' AND '.join(quote(k)+' IS ?' for k in keys)
        row=db.execute('SELECT * FROM '+quote(change['table'])+' WHERE '+where,tuple(keys.values())).fetchone()
        expected=change['after' if after else 'before']
        actual=dict(row) if row is not None else None
        if actual is not None:
            actual={k:({'$blob':v.hex().upper()} if isinstance(v,bytes) else v) for k,v in actual.items()}
        if actual!=expected:return False
    return True
