"""LOCAL_ONLY P2.1 bounded evidence audit. No old file writes/deletes or new DB copies."""
import argparse
import csv
import json
import re
import subprocess
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

from action_tracker.operations.cleanup_evidence import (path_key,evidence_id,extract_backup_records,
    extract_path_references,reference_conclusion,task_completion,file_identity,inspect_database,
    hash_pair,legacy_certificate,normalize_restore_proof)
from action_tracker.operations.storage_foundation import sha256_file,source_signature
from action_tracker.operations.storage_lifecycle import cleanup_preview,write_new


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def export_csv(path,records,fields=None):
    fields=fields or list(dict.fromkeys(k for r in records for k in r))
    with Path(path).open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for record in records:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in record.items()})


def select_pilot(restores,limit=14):
    # Diversify by actual prior task families, capacity extrema and observed references.
    groups=defaultdict(list)
    for r in restores:groups[str(Path(r['path']).parent)].append(r)
    picked={}
    for group in sorted(groups):
        ordered=sorted(groups[group],key=lambda r:(int(r['size_bytes']),r['path']))
        for row in (ordered[0],ordered[-1]):picked[row['path']]=row
    linked=sorted(restores,key=lambda r:(-int(r.get('observed_referencing_evidence_count') or 0),r['path']))
    for row in linked:
        if len(picked)>=limit:break
        picked[row['path']]=row
    return list(picked.values())[:limit]


def main():
    p=argparse.ArgumentParser();p.add_argument('--audit-root',required=True);p.add_argument('--output',required=True)
    p.add_argument('--code-root',required=True);p.add_argument('--stage',choices=['pilot','overview'],required=True)
    args=p.parse_args();audit=Path(args.audit_root);out=Path(args.output).resolve();code=Path(args.code_root).resolve()
    p2=audit/'ActionSKUTracker_P2_20261014';p06=audit/'ActionSKUTracker_P06_20261014'
    from action_tracker.config import load_settings
    policy=load_settings(project_root_override=code)['storage_foundation']['cleanup_review']
    pilot_limit=policy['pilot_limit'];file_max=policy['evidence_file_max_bytes']
    read_budget=policy['evidence_read_budget_bytes'];hash_budget=policy['full_hash_budget_bytes']
    pair_limit=policy['fresh_pair_limit']
    if (policy['legacy_deletion_enabled'] is not False or type(pilot_limit) is not int or
            not 10<=pilot_limit<=20 or any(type(v) is not int or v<0 for v in (file_max,read_budget,hash_budget,pair_limit))):
        raise ValueError('CLEANUP_REVIEW_POLICY_INVALID')
    out.mkdir(parents=True,exist_ok=True)
    protected_paths=[Path(v) for v in read(out/'PRODUCTION_BEFORE.json') if v!='production_git']
    legacy=csv_rows(p2/'LEGACY_DATABASE_CLASSIFICATION.csv')
    restores=[r for r in legacy if r['purpose_class']=='RESTORE_VALIDATION_COPY']
    dev=[r for r in legacy if r['purpose_class']=='DEVELOPMENT_VALIDATION_COPY_CANDIDATE']
    candidates=restores+dev;target_keys={path_key(r['path']) for r in candidates}
    chosen=select_pilot(restores,limit=pilot_limit)
    cache=read(p2/'LEGACY_DUPLICATE_REVALIDATION.json')
    cached={path_key(f['path']):f for f in cache['files']}
    # Include the valid prior full-hash pair without selecting only easy cases.
    c=next((r for r in restores if path_key(r['path']) in cached),None)
    if c and c['path'] not in {r['path'] for r in chosen}:chosen[-1]=c
    if args.stage=='overview':
        # A zero-eligible pilot is a stop for expensive expansion, not for recording known metadata.
        pilot=read(out/'PILOT_RESULT.json')
        write_new(out/'EXPANSION_DECISION.json',{'pilot_eligible':pilot['eligible_files'],
            'deep_expansion':'NOT_STARTED_MISSING_OWNERSHIP_REFERENCE_RETENTION_PROOF' if not pilot['eligible_files'] else 'READY_FOR_EXPLICIT_NEXT_BATCH',
            'method':'remaining targets receive bounded metadata/reference review only; no repeated full DB hashing'})
        export_csv(out/'RESTORE_CHECK_89_REVIEW.csv',pilot['restore_overview'])
        export_csv(out/'DEV_DATABASE_REVIEW.csv',pilot['development_overview'])
        print(json.dumps({'stage':'overview','restore_metadata_files':len(restores),'development_metadata_files':len(dev),'no_deep_expansion':not pilot['eligible_files']}))
        return
    # Existing inventory determines the source list; no whole-drive enumeration.
    keywords=re.compile('backup|acceptance|summary|manifest|state|resume|preflight|validation|final',re.I)
    sources={}
    for r in csv.DictReader((p06/'ALL_ASSET_DISPOSITION.csv').open(encoding='utf-8-sig')):
        raw=r['path'];normal=raw.replace('\\','/').lower();path=Path(raw)
        if (('/actionskutracker/runtime/reports/' in normal or '/action-es-data-system/outputs/' in normal) and
             path.suffix.lower()=='.json' and int(r['size_bytes'])<=file_max and keywords.search(path.name)):
            sources[str(path.resolve())]='manifest/runtime/review-apply evidence'
    # Real configs and current code consumers; do not read browser cookies, .env or secret stores.
    for checkout in (code,Path('F:/ActionSKUTracker_production')):
        paths=subprocess.check_output(['git','--no-optional-locks','-C',str(checkout),'ls-files','-z']).decode().split('\0')
        for relative in paths:
            if relative.startswith(('src/','scripts/','config/')) and Path(relative).suffix.lower() in {'.py','.ps1','.yaml','.json'}:
                sources[str((checkout/relative).resolve())]='code/config consumer'
    for r in csv_rows(p06/'MIGRATION_REQUIRED_FILES.csv'):
        path=Path(r['path'])
        if path.suffix.lower() in {'.yaml','.json'}:sources[str(path.resolve())]='effective configuration/recovery list'
    hits=defaultdict(list);backup_records=[];documents=[];scan=[];errors=[];read_bytes=0
    for source,domain in sorted(sources.items()):
        path=Path(source)
        if not path.is_file():errors.append({'path':source,'reason':'ASSET_UNAVAILABLE'});continue
        if path.stat().st_size>file_max or read_bytes+path.stat().st_size>read_budget:
            errors.append({'path':source,'reason':'EVIDENCE_READ_BUDGET'});continue
        raw=path.read_bytes();read_bytes+=len(raw);sha=__import__('hashlib').sha256(raw).hexdigest()
        try:text=raw.decode('utf-8-sig')
        except UnicodeError:errors.append({'path':source,'reason':'ENCODING_UNRESOLVED'});continue
        if path.suffix.lower()=='.json':
            try:doc=json.loads(text)
            except ValueError:errors.append({'path':source,'reason':'JSON_PARSE_UNRESOLVED'});continue
            backup_records.extend(extract_backup_records(doc,source=source,source_sha256=sha));documents.append((source,sha,doc))
        else:doc={'text':text}
        for hit in extract_path_references(doc,targets=target_keys):
            key=path_key(hit['referenced_path'])
            hits[key].append({**hit,'source_file':source,'source_sha256':sha,'domain':domain,
                'evidence_id':evidence_id(source,sha,hit['json_pointer'],'OBSERVED_PATH_REFERENCE')})
        scan.append({'path':source,'sha256':sha,'bytes':len(raw),'domain':domain})
    # Current authoritative metadata is read from the verified closed P1 copy, never the live WAL/SHM.
    from action_tracker.operations.storage_foundation import read_database
    baseline=audit/'ActionSKUTracker_P1_20261014/baseline/primary.sqlite3'
    db_scope=[]
    with read_database(baseline,immutable=True) as db:
        for table,column in [('artifacts','file_path'),('artifacts','manifest_path'),('run_evidence','snapshot_path'),('commit_batches','snapshot_path')]:
            count=0
            for (value,) in db.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'):
                count+=1
                if isinstance(value,str) and path_key(value) in target_keys:
                    hits[path_key(value)].append({'source_file':str(baseline),'domain':'database_records',
                        'json_pointer':table+'.'+column,'referenced_path':value,
                        'evidence_id':evidence_id(str(baseline),sha256_file(baseline),table+'.'+column,'EXACT_DB_PATH')})
            db_scope.append({'table':table,'column':column,'rows_checked':count})
    coverage={'complete':False,'completed_domains':[],
        'partially_checked_domains':['configuration','manifests','code_consumers','database_records','runtime_logs','recovery_lists','review_apply','active_tasks'],
        'unresolved_dynamic_references':None,'excluded_required_sources':None,'source_read_errors':len(errors),
        'reason':'external task state, dynamic/relative consumer paths, all DB JSON payloads and explicit owner release not closed',
        'database_columns':db_scope,'source_files':len(scan),'read_bytes':read_bytes,'errors':errors}
    write_new(out/'REFERENCE_COVERAGE.json',coverage)
    write_new(out/'EVIDENCE_SOURCE_INVENTORY.json',scan)
    write_new(out/'BACKUP_BINDING_EVIDENCE.json',backup_records)
    keyed=defaultdict(list)
    for b in backup_records:
        if b['restore_path']:keyed[path_key(b['restore_path'])].append(b)
        keyed[path_key(b['backup_path'])].append(b)
    inspections={};pilot_records=[];pair_bytes=0;fresh_pairs=0
    for row in chosen:
        path=Path(row['path']);key=path_key(path);bindings=keyed[key]
        # Inspect each sampled restore immutable. Full integrity only for bounded fresh pair validation.
        state=inspect_database(path,full_integrity=False,protected_paths=protected_paths);inspections[key]=state
        refs=reference_conclusion(hits[key],coverage)
        if state.get('production_dependency') is True:
            refs.update(production_dependency=True,production_dependency_verified=True)
        known_run=next((b['run_id'] for b in bindings if b.get('run_id')),None)
        task={'task_status':'UNKNOWN','reason':'NO_BOUND_TERMINAL_RECORD'}
        for source,sha,doc in documents:
            found=task_completion(doc,run_id=known_run,database_path=str(path))
            if found['task_status']!='UNKNOWN':task={**found,'evidence_file':source,'evidence_id':evidence_id(source,sha,found['json_pointer'],'TASK_TERMINAL')};break
        cert=legacy_certificate(state,task=task,references=refs)
        cert.update(owner_task=row['task_or_run_id'],owner_binding_quality='PRIOR_FILENAME_HINT_ONLY',
            review_level='PILOT_DEEP_EVIDENCE_REVIEW',backup_bindings=bindings,
            historical_restore_rehearsed=any(b['historical_restore_rehearsed'] for b in bindings),
            sha256=None,identity_verified=False,current_identity_check='UNKNOWN',unique_business_data=None,
            original_backup_exists=None,alternative_recovery=None,reviewed_at=datetime.now(timezone.utc).isoformat())
        possible={b['backup_path'] for b in bindings if b['restore_path'] and path_key(b['restore_path'])==key}
        cert['original_backup_paths_proven_by_manifest']=sorted(possible)
        if len(possible)==1 and state['exists']:
            backup=Path(next(iter(possible)));cert['original_backup_exists']=backup.is_file()
            if backup.is_file():
                cached_c,cached_b=cached.get(key),cached.get(path_key(backup))
                if cached_c and cached_b and state['status']=='IDENTIFIED_READ_ONLY' and source_signature(path)==cached_c['source_signature'] and source_signature(backup)==cached_b['source_signature']:
                    cert.update(sha256=cached_c['sha256'],identity_verified=True,current_identity_check='PASS',
                        current_integrity=cache['integrity'],current_foreign_keys=cache['foreign_keys'],unique_business_data=False,
                        hash_method='REUSED_P2_FULL_SHA_IDENTICAL_SIGNATURE',hash_evidence_file=str(p2/'LEGACY_DUPLICATE_REVALIDATION.json'))
                elif fresh_pairs<pair_limit and pair_bytes+path.stat().st_size+backup.stat().st_size<=hash_budget and state['status']=='IDENTIFIED_READ_ONLY':
                    pair=hash_pair(path,backup,budget_bytes=hash_budget-pair_bytes)
                    pair_bytes+=pair['read_bytes'];fresh_pairs+=1
                    original=inspect_database(backup,full_integrity=True,protected_paths=protected_paths)
                    restored=inspect_database(path,full_integrity=True,protected_paths=protected_paths)
                    pair['candidate_path']=str(path);pair['backup_path']=str(backup)
                    evidence_file=out/f'pair-{fresh_pairs:02d}.json';write_new(evidence_file,{'pair':pair,'original':original,'restore':restored})
                    proof_file=out/f'restore-{fresh_pairs:02d}-validation.json'
                    proof=normalize_restore_proof(pair,original,restored);write_new(proof_file,proof)
                    cert.update(sha256=pair['candidate_sha256'],identity_verified=True,current_identity_check='PASS',
                        current_integrity=restored.get('current_integrity'),current_foreign_keys=restored.get('current_foreign_keys'),
                        unique_business_data=pair['unique_business_data_relative_to_backup'],hash_method=pair['method'],
                        hash_evidence_file=str(evidence_file),original_backup_validation=original,
                        alternative_recovery={'path':str(backup),'sha256':pair['backup_sha256'],'status':proof['status'],'evidence_path':str(proof_file)})
        pilot_records.append(cert)
    result=cleanup_preview(pilot_records)
    export_csv(out/'PILOT_REVIEW.csv',result['assets'])
    # Metadata/reference review is useful but never described as full identity/integrity certification.
    overview=[]
    completed={path_key(r['path']):r for r in result['assets']}
    for row in candidates:
        key=path_key(row['path'])
        if key in completed:record=completed[key]
        else:
            state=file_identity(Path(row['path']))
            refs=reference_conclusion(hits[key],coverage)
            record=legacy_certificate(state,task={'task_status':'UNKNOWN','reason':'TERMINAL_OWNER_BINDING_NOT_PROVEN'},references=refs)
            record.update(review_level='METADATA_REFERENCE_ONLY_NOT_DEEP_CERTIFIED',sha256=None,identity_verified=False,
                unique_business_data=None,current_identity_check='UNKNOWN',backup_bindings=keyed[key])
            record=cleanup_preview([record])['assets'][0]
        overview.append({**record,'purpose_class':row['purpose_class'],'prior_size_bytes':int(row['size_bytes']),
            'estimated_releasable_bytes':0,'delete_allowed':False})
    restore_overview=[r for r in overview if r['purpose_class']=='RESTORE_VALIDATION_COPY']
    development_overview=[r for r in overview if r['purpose_class']=='DEVELOPMENT_VALIDATION_COPY_CANDIDATE']
    summary={'pilot_files':len(chosen),'pilot_logical_bytes':sum(r.get('size_bytes',0) for r in result['assets']),
        'eligible_files':len(result['whitelist']),'eligible_bytes':result['estimated_releasable_bytes'],
        'fresh_hash_pairs':fresh_pairs,'full_hash_read_bytes':pair_bytes,'hash_budget_bytes':hash_budget,
        'restore_current_files':sum(r['exists'] for r in restore_overview),'restore_prior_files':len(restores),
        'restore_current_bytes':sum(r.get('size_bytes',0) for r in restore_overview if r['exists']),
        'development_current_files':sum(r['exists'] for r in development_overview),'development_prior_files':len(dev),
        'development_current_bytes':sum(r.get('size_bytes',0) for r in development_overview if r['exists']),
        'restore_overview':restore_overview,'development_overview':development_overview,'reference_coverage':coverage,
        'old_files_deleted':0,'new_database_copies_created':0,'expanded_deep_hashing':False,
        'evidence_mechanism_findings':['Legacy restore reports prove validation step completion, not full owner-task termination.',
            'No approved legacy retention policy or per-file independent recovery release found in the checked evidence.',
            'Static/bounded references are observed links; external task/dynamic/DB payload closure remains unknown.']}
    write_new(out/'PILOT_RESULT.json',summary)
    export_csv(out/'SAFE_CLEANUP_ELIGIBLE.csv',result['whitelist'],fields=['file_id','path','size_bytes','sha256',
        'database_identity','owner_task','task_evidence','reference_evidence','original_backup_paths_proven_by_manifest',
        'alternative_recovery','retain_until','cleanup_status','estimated_releasable_bytes','evidence_files','delete_allowed'])
    print(json.dumps({k:v for k,v in summary.items() if k in ('pilot_files','pilot_logical_bytes','eligible_files','fresh_hash_pairs','full_hash_read_bytes','restore_current_files','development_current_files')},ensure_ascii=False))


if __name__=='__main__':main()
