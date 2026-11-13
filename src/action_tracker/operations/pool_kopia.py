"""Explicit pooled Kopia roundtrip, reusing the existing backend/set validators.

No daily hook, scheduler, snapshot expiration, history adoption or source writes.
One selected task baseline, one stage and one independent restore per job.
"""
from contextlib import ExitStack,contextmanager
from pathlib import Path
import json,os,time,uuid

from .backup_sets import (make_plan,verify_restored,write_immutable,read_json,validate_seal,seal,
                          digest,canonical,stage_plan)
from .backup_operations import OperationPolicy,public_backend_identity,_repository_matches
from .kopia_backend import _source_inventory
from .pool_files import os_lock
from . import pool_bundles
from .cleanup_executor import safe_path
from .storage_foundation import StorageError,sha256_file


def own_root(pool):
    root=safe_path(pool.root/'kopia',must_exist=False)
    with pool._lock() as index:
        if root.exists() and index.get('kopia_owned_root')!=str(root):
            raise StorageError('KOPIA_POOL_DIRECTORY_UNOWNED')
        root.mkdir(exist_ok=True)
        index['kopia_owned_root']=str(root);pool._write('index.json',index)
    for name in ('jobs','sessions'):(root/name).mkdir(exist_ok=True)
    return root


def _backend_gate(pool,backend):
    if backend.config.provider not in ('local','filesystem'):
        raise StorageError('POOL_KOPIA_MANUAL_LOCAL_ONLY')
    root=own_root(pool)
    repo=safe_path(Path(backend.config.repository_path))
    workspace=safe_path(Path(backend.workspace),must_exist=False)
    if not repo.is_relative_to(root) or not workspace.is_relative_to(root/'sessions'):
        raise StorageError('POOL_KOPIA_REPOSITORY_OR_SESSION_OUTSIDE_POOL')
    identity=public_backend_identity(backend)
    if not identity['repository_id'] or backend.repository_status()['repository_id']!=identity['repository_id']:
        raise StorageError('POOL_KOPIA_REPOSITORY_ID_MISMATCH')
    return root,identity


def _control(pool,name,value):
    path=pool.root/'active'/name
    with pool._lock() as index:
        pool._checkpoint(index,len(canonical(value))+65536)
        write_immutable(path,value)
        index.setdefault('kopia_controls',{})[name]=str(path);pool._write('index.json',index)
    return path


class PoolKopiaJob:
    def __init__(self,pool,base_asset_id,backend,policy,projections=None):
        self.pool,self.base_id,self.backend,self.policy=pool,base_asset_id,backend,policy
        self.root,self.repository=_backend_gate(pool,backend)
        self.projections={k:str(safe_path(Path(v))) for k,v in (projections or {}).items()}
        self.id='task-'+digest({'base_asset_id':base_asset_id,'repository':self.repository,
                              'projections':self.projections})[:40]
        self.directory=self.root/'jobs'/self.id
        self.directory.mkdir(exist_ok=True)
        self.path=self.directory/'state.json'
        self.start=time.monotonic()
        self.new_stage_copies=0
        self.new_restore_copies=0

    def checkpoint(self):
        if time.monotonic()-self.start>=self.policy.max_runtime_seconds:raise StorageError('KOPIA_JOB_TIME_BUDGET_EXCEEDED')
        self.pool.checkpoint()

    def save(self,state):
        with self.pool._lock() as index:
            self.pool._checkpoint(index,len(canonical(state))+65536)
            self.pool._write(str(self.path.relative_to(self.pool.root)),state)

    def load_or_create(self):
        self.pool.verify(self.base_id)
        if self.path.exists():
            state=validate_seal(read_json(self.path),'POOL_KOPIA_JOB_V1')
            if (state['base_asset_id']!=self.base_id or state['repository']!=self.repository
                    or state['projection_sources']!=self.projections):
                raise StorageError('POOL_KOPIA_JOB_BINDING_CHANGED')
            return {k:v for k,v in state.items() if k!='document_sha256'}
        with self.pool._lock() as index:base=dict(index['assets'][self.base_id])
        binding=seal({'contract':'POOL_KOPIA_BASE_BINDING_V1','scope':'TASK_RECOVERY_BASELINE',
            'base_asset_id':self.base_id,'base_sha256':base['sha256'],'identity':base['identity'],
            'base_owner_task':base['task_id'],'repository':self.repository})
        context=_control(self.pool,self.id+'-binding.json',binding)
        recovery=_control(self.pool,self.id+'-recovery.json',read_json(Path(base['restore_report'])))
        assets=[{'asset_id':'primary','root_id':'baseline','relative_path':Path(base['path']).name,
                 'kind':'verified_database','sha256':base['sha256']},
                {'asset_id':'context','root_id':'baseline','relative_path':context.name,
                 'kind':'run_manifest','sha256':sha256_file(context)},
                {'asset_id':'recovery','root_id':'baseline','relative_path':recovery.name,
                 'kind':'recovery_record','sha256':sha256_file(recovery)}]
        roots={'baseline':str(Path(base['path']).parent)}
        if self.projections and set(self.projections)!={'master','known','offline'}:
            raise StorageError('CORE_PROJECTIONS_INCOMPLETE')
        for name,value in self.projections.items():
            path=Path(value);roots[name]=str(path.parent)
            assets.append({'asset_id':name,'root_id':name,'relative_path':path.name,'kind':'projection',
                           'projection_name':name,'sha256':sha256_file(path)})
        plan=make_plan(backup_set_id=self.id,run_id=self.id,profile='CORE_BACKUP' if self.projections else 'CURRENT_BUSINESS_BACKUP',
            roots=roots,assets=assets,repository=self.repository,config_version='POOL_KOPIA_V1',
            recovery_owner=base['task_id'],retention={'policy_id':'retain-local-baseline-and-pinned-snapshot','recovery_required':True},
            max_logical_bytes=self.policy.max_upload_bytes)
        if plan['logical_bytes']>self.policy.max_restore_bytes:raise StorageError('POOL_KOPIA_RESTORE_BUDGET_EXCEEDED')
        if plan['logical_bytes']*2+16*1024**2>self.policy.max_upload_bytes:
            raise StorageError('UPLOAD_PLANNING_BUDGET_EXCEEDED')
        state={'contract':'POOL_KOPIA_JOB_V1','job_id':self.id,'base_asset_id':self.base_id,
            'repository':self.repository,'projection_sources':self.projections,'roots':roots,'plan':plan,
            'stage_asset_id':None,'restore_asset_id':None,'snapshot':None,'write_started':False,
            'proof':None,'phase':'NEW','attempts':0}
        self.pool.reference(self.base_id,'kopia:'+self.id)
        self.save(state);return state

    def asset(self,asset_id):
        with self.pool._lock() as index:return dict(index['assets'][asset_id])

    def reserve(self,state,role):
        field=role+'_asset_id'
        if state[field] is None:
            # Reconcile a crash after pool reservation but before job.save().
            with self.pool._lock() as index:
                candidates=[dict(a) for a in index['assets'].values()
                    if a['task_id']==self.id+'-'+role and a['kind']=='kopia_bundle']
            if len(candidates)>1:raise StorageError('POOL_KOPIA_RESERVATION_AMBIGUOUS')
            if candidates:
                a=candidates[0]
                if (a['source_asset']!=self.base_id or a['role']!=role
                        or a['plan']!=state['plan']):
                    raise StorageError('POOL_KOPIA_RESERVATION_BINDING_CHANGED')
            else:
                a=pool_bundles.reserve(self.pool,task_id=self.id+'-'+role,source_asset=self.base_id,
                                       plan=state['plan'],role=role)
            state[field]=a['asset_id'];self.save(state)
        return self.asset(state[field])

    def stage(self,state,stack):
        a=self.reserve(state,'stage');path=Path(a['path'])
        stack.enter_context(os_lock(self.pool.root/'leases'/(a['asset_id']+'.lock')))
        if path.exists():
            # Complete files can be reconciled. An incomplete/unknown tree is
            # retained rather than deleting partials or silently copying again.
            verify_restored(state['plan'],path)
            if a['state'] in ('CREATING','FAILED'):pool_bundles.complete(self.pool,a['asset_id'])
            else:pool_bundles.check_tree(self.pool,a)
        else:
            if a['state']!='CREATING':raise StorageError('POOL_KOPIA_STAGE_MISSING')
            self.new_stage_copies+=1
            stage_plan(state['plan'],state['roots'],path,max_scratch_bytes=self.policy.max_scratch_bytes,checkpoint=self.checkpoint)
            pool_bundles.complete(self.pool,a['asset_id'])
        state['inventory']=_source_inventory(path,content_hash=True,checkpoint=self.checkpoint)
        state['phase']='STAGED';self.save(state)
        return path

    def find_snapshot(self,state):
        items=self.backend.list_snapshots(backup_set_id=self.id)
        if len(items)!=1:raise StorageError('POOL_KOPIA_SNAPSHOT_AMBIGUOUS_OR_MISSING')
        s=items[0];stage=self.asset(state['stage_asset_id'])
        if (s.get('incomplete') or s.get('backup_set_id')!=self.id
                or (state['snapshot'] and s['snapshot_id']!=state['snapshot']['snapshot_id'])
                or os.path.normcase(str(Path(s['source_path']).resolve()))!=os.path.normcase(stage['path'])
                or any(s.get(k)!=v for k,v in state['inventory'].items())):
            raise StorageError('POOL_KOPIA_SNAPSHOT_BINDING_MISMATCH')
        return s

    def verify_snapshot(self,state):
        _repository_matches(self.backend,state['plan'])
        snapshot=self.find_snapshot(state)
        with self.io_reservation('verify-cache',state['plan']['logical_bytes']+16*1024**2):
            verification=self.backend.verify_snapshot(snapshot['snapshot_id'])
        if (verification.get('status')!='SNAPSHOT_VERIFIED' or verification.get('verification')!='KOPIA_100_PERCENT_FILES'
                or verification.get('snapshot_id')!=snapshot['snapshot_id']
                or verification.get('file_count')!=len(state['plan']['assets'])+1):
            raise StorageError('POOL_KOPIA_100_PERCENT_VERIFICATION_REQUIRED')
        return verification

    @contextmanager
    def io_reservation(self,label,expected):
        # Repository objects and private client cache are included in _usage.
        # A durable reservation covers concurrent CLI growth until completion.
        with self.pool.temporary(task_id=self.id+'-'+label+'-'+uuid.uuid4().hex,expected_bytes=expected,
                source=self.repository['repository_id'],purpose='bounded Kopia I/O growth',discardable=True) as a:
            Path(a['path']).write_bytes(b'')
            yield
        self.pool.finish_task(a['task_id'])

    def restore(self,state,stack):
        a=self.reserve(state,'restore');path=Path(a['path'])
        stack.enter_context(os_lock(self.pool.root/'leases'/(a['asset_id']+'.lock')))
        if path.exists():
            checked=verify_restored(state['plan'],path)
        else:
            if a['state']!='CREATING':raise StorageError('POOL_KOPIA_RESTORE_MISSING_OR_FAILED')
            state['phase']='RESTORE_STARTED';self.save(state)
            try:
                self.new_restore_copies+=1
                self.backend.restore_snapshot(state['snapshot']['snapshot_id'],path)
                checked=verify_restored(state['plan'],path)
            except Exception as exc:
                self.pool._fail([a],exc);raise
        if a['state'] in ('CREATING','FAILED'):pool_bundles.complete(self.pool,a['asset_id'])
        else:pool_bundles.check_tree(self.pool,a)
        return checked

    def cleanup(self,state):
        proof=Path(state['proof']['path'])
        if sha256_file(proof)!=state['proof']['sha256']:raise StorageError('POOL_KOPIA_PROOF_CHANGED')
        checked=validate_seal(read_json(proof),'POOL_KOPIA_ROUNDTRIP_V1')
        if (checked['plan_sha256']!=state['plan']['document_sha256']
                or checked['repository']!=self.repository or checked['base_asset_id']!=self.base_id
                or checked['snapshot_id']!=state['snapshot']['snapshot_id']
                or any(checked[f]!=state[f] for f in ('stage_asset_id','restore_asset_id'))):
            raise StorageError('POOL_KOPIA_PROOF_BINDING_CHANGED')
        for field in ('stage_asset_id','restore_asset_id'):
            pool_bundles.authorize_release(self.pool,state[field],proof)
        report=self.pool.checkpoint(force_temporaries=True)
        if any(self.asset(state[f])['state']!='DELETED' for f in ('stage_asset_id','restore_asset_id')):
            raise StorageError('POOL_KOPIA_CLEANUP_INCOMPLETE')
        return report

    def run(self):
        with os_lock(self.directory/'job.lock',wait=0):
            state=self.load_or_create();self.checkpoint()
            if hasattr(self.backend,'set_deadline'):self.backend.set_deadline(self.start+self.policy.max_runtime_seconds)
            try:
                reused=bool(state['proof'])
                if reused:
                    self.verify_snapshot(state)
                else:
                    if state['attempts']>=self.policy.max_attempts:raise StorageError('BACKUP_ATTEMPT_BUDGET_EXHAUSTED')
                    state['attempts']+=1;self.save(state)
                    with ExitStack() as stack:
                        stage=self.stage(state,stack)
                        if not state['snapshot']:
                            uncertain=state['write_started'];state['write_started']=True;self.save(state)
                            with self.io_reservation('upload',state['plan']['logical_bytes']*2+16*1024**2):
                                state['snapshot']=self.backend.create_snapshot(stage,backup_set_id=self.id,
                                    upload_limit_bytes=self.policy.max_upload_bytes,resume_only=uncertain)
                            state['phase']='SNAPSHOT_KNOWN';self.save(state)
                        verification=self.verify_snapshot(state)
                        with self.io_reservation('restore-cache',state['plan']['logical_bytes']+16*1024**2):
                            checked=self.restore(state,stack)
                        proof=seal({'contract':'POOL_KOPIA_ROUNDTRIP_V1',**checked,
                            'base_asset_id':self.base_id,'repository':self.repository,
                            'snapshot_id':state['snapshot']['snapshot_id'],'snapshot_verification':verification,
                            'stage_asset_id':state['stage_asset_id'],'restore_asset_id':state['restore_asset_id']})
                        path=self.directory/'roundtrip.json';write_immutable(path,proof)
                        state.update(proof={'path':str(path),'sha256':sha256_file(path)},phase='ROUNDTRIP_PASS')
                        self.save(state)
                cleanup=self.cleanup(state)
                result={'status':'ROUNDTRIP_PASS','job_id':self.id,'base_asset_id':self.base_id,
                    'snapshot_id':state['snapshot']['snapshot_id'],'proof':state['proof'],
                    'reused':reused,'new_stage_copies':self.new_stage_copies,'new_restore_copies':self.new_restore_copies,
                    'cleanup':cleanup,'production_uploaded':False,'production_switched':False,'history_deleted':False}
                self.save(state);return result
            except Exception as exc:
                state['last_error']=type(exc).__name__;self.save(state);raise
            finally:
                if hasattr(self.backend,'set_deadline'):self.backend.set_deadline(None)


def roundtrip(pool,base_asset_id,*,backend,policy=None,projections=None):
    policy=policy or OperationPolicy()
    if not policy.enabled:return {'status':'DISABLED','side_effects':False}
    if hasattr(backend,'bound_private_cache'):backend.bound_private_cache()
    return PoolKopiaJob(pool,base_asset_id,backend,policy,projections).run()
