"""Read-only evidence extraction for the existing P2 cleanup gate."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from .storage_foundation import (StorageError, database_identity, read_database,
                                sha256_file, source_signature)

REQUIRED_REFERENCE_DOMAINS = frozenset({'configuration','manifests','database_records',
    'runtime_logs','recovery_lists','review_apply','active_tasks','code_consumers'})


def path_key(value: str) -> str:
    p=PureWindowsPath(str(value))
    if p.is_absolute():return str(p).casefold()
    if str(value).startswith('/') and '\0' not in str(value):
        try:return str(Path(value).resolve())
        except (OSError,ValueError):pass
    return 'NON_ABSOLUTE:'+str(value)


def walk(value, pointer='$'):
    yield pointer,value
    if isinstance(value,dict):
        for key,item in value.items():yield from walk(item,pointer+'/'+str(key))
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from walk(item,pointer+'/'+str(i))


def evidence_id(source: str, source_sha256: str, pointer: str, method: str) -> str:
    return hashlib.sha256(json.dumps([path_key(source),source_sha256,pointer,method]).encode()).hexdigest()


def extract_backup_records(document, *, source: str, source_sha256: str) -> list[dict]:
    """Bind a hash to the path in the same object, never to a nearby JSON node."""
    result=[]
    for pointer,node in walk(document):
        if not isinstance(node,dict):continue
        backup=node.get('backup_path') or node.get('backup')
        restore=node.get('restore')
        sha=node.get('sha256')
        if not isinstance(backup,str) or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):continue
        if not backup.lower().endswith(('.db','.sqlite','.sqlite3')):continue
        valid=(node.get('integrity') in ('ok','PASS') and
               (node.get('foreign_key_violations')==[] or node.get('foreign_keys')=='PASS'))
        result.append({'backup_path':backup,'restore_path':restore if isinstance(restore,str) else None,
            'backup_sha256':sha,'backup_validation_status':'PASS' if valid else 'UNKNOWN',
            'historical_restore_rehearsed':valid and node.get('logical_parity') is True,
            'run_id':node.get('run_id'),'commit_id':node.get('commit_id'),
            'source_file':source,'source_sha256':source_sha256,'json_pointer':pointer,
            'evidence_id':evidence_id(source,source_sha256,pointer,'SCOPED_BACKUP_OBJECT')})
    return result


def extract_path_references(document, *, targets: set[str]) -> list[dict]:
    hits=[]
    for pointer,node in walk(document):
        if not isinstance(node,str):continue
        if path_key(node) in targets:
            hits.append({'json_pointer':pointer,'referenced_path':node,'match':'EXACT_NORMALIZED_PATH'})
        else:
            # Embedded command/log paths remain observed references, not proof of liveness.
            for match in re.finditer(r'[A-Za-z]:[\\/][^\r\n"<>|;]*?\.(?:sqlite3|sqlite|db)(?:-wal|-shm)?',node):
                raw=match.group(0)
                if path_key(raw) in targets:hits.append({'json_pointer':pointer,'referenced_path':raw,'match':'EMBEDDED_NORMALIZED_PATH'})
    return hits


def reference_conclusion(hits: list[dict], coverage: dict) -> dict:
    covered=set(coverage.get('completed_domains',[]))
    complete=(coverage.get('complete') is True and REQUIRED_REFERENCE_DOMAINS<=covered and
              coverage.get('unresolved_dynamic_references')==0 and coverage.get('excluded_required_sources')==0)
    return {'observed_reference_count':len(hits),'references_verified':complete,
            'reference_count':len(hits) if complete else None,
            'reference_status':'REFERENCED' if complete and hits else 'NO_REFERENCES' if complete else 'REFERENCES_UNKNOWN',
            'coverage':coverage,'hits':hits}


def task_completion(document, *, run_id: str | None, database_path: str) -> dict:
    """QA PASS or co-location cannot substitute for terminal task ownership."""
    if not run_id:return {'task_status':'UNKNOWN','reason':'TASK_RUN_BINDING_MISSING'}
    for pointer,node in walk(document):
        if not isinstance(node,dict) or node.get('run_id')!=run_id:continue
        bound=node.get('database_path') or node.get('backup_path')
        if not isinstance(bound,str) or path_key(bound)!=path_key(database_path):continue
        if node.get('state')=='SUCCESS' and node.get('finished_at'):
            try:datetime.fromisoformat(node['finished_at'])
            except (ValueError,TypeError):
                return {'task_status':'UNKNOWN','reason':'MALFORMED_TERMINAL_TIMESTAMP','json_pointer':pointer}
            return {'task_status':'COMPLETED','run_id':run_id,'json_pointer':pointer,'scope':'EXACT_RUN_AND_DATABASE'}
        if node.get('state') in {'RUNNING','PAUSED','DEGRADED','FAILED','BLOCKED'}:
            return {'task_status':node['state'],'run_id':run_id,'json_pointer':pointer}
    return {'task_status':'UNKNOWN','reason':'NO_BOUND_TERMINAL_RECORD'}


def file_identity(path: Path) -> dict:
    path=Path(path).resolve()
    try:
        stat=path.stat()
    except OSError as exc:return {'path':str(path),'exists':False,'status':'ASSET_UNAVAILABLE','error_type':type(exc).__name__}
    return {'path':str(path),'exists':path.is_file(),'size_bytes':stat.st_size,
            'mtime_ns':stat.st_mtime_ns,'file_id':f'{stat.st_dev}:{stat.st_ino}',
            'hardlink_count':stat.st_nlink,'source_signature':source_signature(path)}


def inspect_database(path: Path, *, full_integrity=False, protected_paths=()) -> dict:
    """Immutable reads never create/checkpoint old WAL/SHM. A nonempty WAL blocks."""
    identity=file_identity(path)
    if not identity['exists']:return identity
    for protected in protected_paths:
        protected=Path(protected)
        if protected.is_file() and Path(path).samefile(protected):
            return {**identity,'status':'RETAIN_REQUIRED','reason':'ACTIVE_PRODUCTION_FILE_OR_ALIAS',
                    'production_dependency':True,'production_dependency_verified':True,
                    'current_integrity':'NOT_CHECKED','current_foreign_keys':'NOT_CHECKED'}
    try:
        before=source_signature(path)
        with read_database(path,immutable=True) as db:
            db_identity=database_identity(db)
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0] if full_integrity else None
            fk=db.execute('PRAGMA foreign_key_check').fetchall() if full_integrity else None
        if source_signature(path)!=before:raise StorageError('OLD_DATABASE_CHANGED_DURING_READ')
        return {**identity,'status':'IDENTIFIED_READ_ONLY','database_identity':db_identity,
            'current_integrity':'PASS' if integrity=='ok' else 'FAIL' if integrity is not None else 'NOT_CHECKED',
            'current_foreign_keys':'PASS' if fk==[] else 'FAIL' if fk is not None else 'NOT_CHECKED',
            'inspection_method':'IMMUTABLE_QUERY_ONLY_NO_WAL_OR_SHM_WRITES'}
    except (StorageError,sqlite3.Error,OSError) as exc:
        return {**identity,'status':'REVIEW_REQUIRED','error_type':type(exc).__name__,'reason':str(exc),
                'current_integrity':'UNKNOWN','current_foreign_keys':'UNKNOWN'}


def hash_pair(candidate: Path, backup: Path, *, budget_bytes: int) -> dict:
    c,b=Path(candidate),Path(backup)
    needed=c.stat().st_size+b.stat().st_size
    if needed>budget_bytes:raise StorageError('EVIDENCE_HASH_BUDGET_EXCEEDED')
    before=[source_signature(p) for p in (c,b)]
    hashes=[sha256_file(p) for p in (c,b)]
    if before!=[source_signature(p) for p in (c,b)]:raise StorageError('OLD_DATABASE_CHANGED_DURING_HASH')
    return {'method':'FRESH_FULL_SHA256_PAIR','candidate_sha256':hashes[0],'backup_sha256':hashes[1],
        'content_equal':hashes[0]==hashes[1],'unique_business_data_relative_to_backup':False if hashes[0]==hashes[1] else None,
        'read_bytes':needed,'signatures':before,'verified_at':datetime.now(timezone.utc).isoformat()}


def legacy_certificate(inspection: dict, *, task: dict, references: dict,
                       recovery: dict | None=None, retention: dict | None=None) -> dict:
    """Populate only explicit evidence; absence remains UNKNOWN for P2's gate."""
    recovery=recovery or {};retention=retention or {}
    return {**inspection,'evidence_contract':'LEGACY_CLEANUP_EVIDENCE_V1',
        'task_status':task.get('task_status','UNKNOWN'),'task_evidence':task,
        'references_verified':references.get('references_verified') is True,
        'observed_reference_count':references.get('observed_reference_count'),
        'reference_count':references.get('reference_count'),'reference_evidence':references,
        'recovery_required':recovery.get('recovery_required'),
        'independent_rollback_obligation':recovery.get('independent_rollback_obligation'),
        'recovery_release_verified':recovery.get('release_verified') is True,
        'alternative_recovery':recovery.get('alternative_recovery'),
        'retain_until':retention.get('retain_until'),
        'retention_policy_verified':retention.get('policy_verified') is True,
        'retention_evidence':retention,'production_dependency':references.get('production_dependency'),
        'production_dependency_verified':references.get('production_dependency_verified') is True,
        'task_terminal_verified':task.get('scope')=='EXACT_RUN_AND_DATABASE' and task.get('task_status')=='COMPLETED',
        'delete_allowed':False}


def normalize_restore_proof(pair: dict, original: dict, restored: dict) -> dict:
    """Translate real existing-copy checks into P1/P2's restore proof contract."""
    equal_identity=original.get('database_identity')==restored.get('database_identity')
    valid=(pair.get('content_equal') is True and pair.get('backup_sha256')==pair.get('candidate_sha256') and
        equal_identity and all(r.get('current_integrity')=='PASS' and r.get('current_foreign_keys')=='PASS'
                               for r in (original,restored)))
    return {'contract':'RESTORE_VALIDATION_V1','status':'PASS' if valid else 'REVIEW_REQUIRED',
        'backup_sha256':pair.get('backup_sha256'),'candidate_sha256':pair.get('candidate_sha256'),
        'backup_path':pair.get('backup_path'),'restore_check_path':pair.get('candidate_path'),
        'database_identity':original.get('database_identity'),'integrity':'PASS' if valid else 'UNKNOWN',
        'foreign_keys':'PASS' if valid else 'UNKNOWN',
        'method':'EXISTING_RESTORE_COPY_FULL_BYTES_AND_IMMUTABLE_INTEGRITY_NO_NEW_COPY',
        'new_database_copies_created':0}
