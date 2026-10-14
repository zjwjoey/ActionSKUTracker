"""LOCAL_ONLY: bounded isolated copies from P0.6 inventories, no production writes.

Run with PYTHONPATH=src. Inputs are explicit; no whole-drive enumeration.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from action_tracker.operations.storage_foundation import (database_identity, projection_audit,
    read_database, resolve_asset, sha256_file, retention_preview)
from action_tracker.operations.storage_lifecycle import (ScratchTask, classify_legacy_database,
    cleanup_preview, create_candidate, materialize_plan, resolve_legacy_binding, write_new)


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def dump_csv(path, records, fields=None):
    fields = fields or list(dict.fromkeys(k for r in records for k in r))
    with Path(path).open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in records:
            w.writerow({k: json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v
                        for k,v in r.items()})


def asset(path, root, relative, *, sha=None, **metadata):
    p = Path(path).resolve()
    return {'original_path':str(p),'root_id':root,'relative_path':relative,
            'sha256':sha or sha256_file(p),'size_bytes':p.stat().st_size,**metadata}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--audit-root',required=True);p.add_argument('--baseline',required=True)
    p.add_argument('--data-root',required=True);p.add_argument('--workspace',required=True)
    p.add_argument('--scratch-root',required=True);p.add_argument('--reports',required=True)
    args=p.parse_args()
    audit=Path(args.audit_root);p06=audit/'ActionSKUTracker_P06_20261014';p05=audit/'ActionSKUTracker_P05_20261014'
    data=Path(args.data_root).resolve();workspace=Path(args.workspace).resolve();reports=Path(args.reports).resolve()
    reports.mkdir(parents=True,exist_ok=False)
    protection=[data,Path('F:/按日期整理'),Path('F:/Action_Master'),Path('F:/ActionSKUTracker_production')]
    roots={'data':str(workspace/'ActionData'),'archive':str(workspace/'ActionArchive'),
           'scratch':str(Path(args.scratch_root).resolve())}
    for value in roots.values(): create_candidate(Path(value),protected_roots=protection)
    baseline=Path(args.baseline).resolve()
    m=json.loads(Path(str(baseline)+'.asset.json').read_text(encoding='utf-8'))
    required=rows(p06/'MIGRATION_REQUIRED_FILES.csv')
    originals={r['path']:r for r in required}
    plan=[]
    for r in required:
        if r['asset_type'] in {'AUTHORITATIVE_DATABASE','SQLITE_CONSISTENCY_SIDECAR'}:continue
        original=Path(r['path']).resolve()
        plan.append(asset(original,'data',original.relative_to(data).as_posix(),sha=r['content_sha256'],
                    asset_type=r['asset_type'],primary_commit_id=m['commit_id'],reference_by=r['reference_by']))
    plan.append(asset(baseline,'data','runtime/db/action_tracker.db',sha=m['sha256'],
                       asset_type='VERIFIED_PRIMARY_CANDIDATE',verified_database=m,primary_commit_id=m['commit_id']))
    # Exact current identities and translation relationships come from the closed verified copy.
    with read_database(baseline,immutable=True) as db:
        current={r[0] for r in db.execute("SELECT official_sku FROM products WHERE status='CURRENT'")}
        latest=db.execute("SELECT * FROM commit_batches WHERE snapshot_path IS NOT NULL AND status='COMMITTED' "
                          "ORDER BY committed_at DESC LIMIT 1").fetchone()
        orphan_units=db.execute('SELECT COUNT(*) FROM translation_units u LEFT JOIN translation_source_versions s '
                               'ON u.source_version_id=s.source_version_id WHERE s.source_version_id IS NULL').fetchone()[0]
        source_count=db.execute('SELECT COUNT(*) FROM translation_source_versions').fetchone()[0]
        translation_sample=dict(db.execute('SELECT u.unit_id,u.field_name,u.source_version_id,u.source_text_hash,'
            's.official_sku,s.source_hash,s.source_run_id FROM translation_units u JOIN translation_source_versions s '
            'ON u.source_version_id=s.source_version_id LIMIT 1').fetchone())
    image_rows=rows(data/'runtime/images/manifests/image_manifest.csv')
    current_images=[r for r in image_rows if r['sku'] in current]
    for r in current_images:
        original=Path(r['master_image_path']).resolve()
        plan.append(asset(original,'data',original.relative_to(data).as_posix(),sha=r['master_hash'],
            asset_type='IMAGE_MASTER',sku=r['sku'],canonical_id=r['canonical_id'],
            source_version=r['source_hash'],source_url=r['source_image_url']))
    # Copy only the current completed daily snapshot and its actual referenced source evidence.
    snapshot=Path(latest['snapshot_path'])
    if not snapshot.is_dir(): raise RuntimeError('CURRENT_SNAPSHOT_NOT_DIRECTORY')
    for original in snapshot.iterdir():
        if original.is_file():
            plan.append(asset(original,'data',original.resolve().relative_to(data).as_posix(),
                 asset_type='CURRENT_SNAPSHOT_EVIDENCE',run_id=latest['run_id'],primary_commit_id=latest['commit_id']))
    # Keep daily resume state and current committed-run evidence when available, bounded to known run directories.
    daily=data/'runtime/reports/daily'/snapshot.parent.name/latest['run_id']
    if daily.is_dir():
        for original in daily.iterdir():
            if original.is_file() and original.suffix in {'.json','.md','.csv'}:
                plan.append(asset(original,'data',original.relative_to(data).as_posix(),asset_type='CURRENT_TASK_RESUME_EVIDENCE',run_id=latest['run_id']))
    # Configuration is copied exactly. Safe storage commands use explicit project_root_override.
    active_copies=materialize_plan(plan,roots,max_bytes=8*1024**3)
    write_new(reports/'ACTIVE_DATA_CANDIDATE_MANIFEST.json',{'contract':'ACTIVE_DATA_CANDIDATE_V1',
        'production_enabled':False,'roots':roots,'assets':active_copies,'commit_id':m['commit_id'],
        'original_deployment_record_preserved':True,'credentials_included':False,
        'legacy_consumer_path_migration_complete':False})
    write_new(Path(roots['data'])/'asset_index.json',{'contract':'ASSET_INDEX_V1','assets':active_copies})
    projections={'master':Path(roots['data'])/'runtime/master/Action_Master.xlsx',
                 'known':Path(roots['data'])/'runtime/state/known_skus.csv',
                 'offline':Path(roots['data'])/'runtime/state/offline_skus.csv'}
    audit_result=projection_audit(Path(roots['data'])/'runtime/db/action_tracker.db',projections)
    assert audit_result['status']=='PASS'
    # Bounded archive sample: a previous completed snapshot, raw Spanish index/workbook, image versions.
    history=rows(p06/'HISTORICAL_ARCHIVE_ASSETS.csv')
    sample=[]
    for r in history:
        if r['asset_type']=='OFFLINE_SPANISH_REFERENCE_INDEX' and not sample: sample.append(r)
    sample.append(next(r for r in history if r['asset_type']=='HISTORICAL_SPANISH_ORIGINAL' and '20260810' in r['path']))
    previous=data/'runtime/snapshots/2026-10-13/2026-10-13_033127'
    for name in ('category_structure.csv','coverage.json','run_report.json','products_normalized.csv'):
        original=previous/name
        r=next(r for r in history if Path(r['path']).resolve()==original.resolve())
        sample.append(r)
    sample.extend(r for r in history if r['asset_type']=='MANIFEST_ORIGINAL_IMAGE')
    sample=sample[:6]+sample[6:8]
    archive_plan=[]
    for r in sample:
        sha=r['content_sha256'] if len(r['content_sha256'])==64 else sha256_file(Path(r['path']))
        original=Path(r['path']);relative='objects/'+sha+original.suffix
        archive_plan.append(asset(original,'archive',relative,sha=sha,asset_type=r['asset_type'],
            sku=r['business_identity'],source_version=r['source_version'],run_id=r['run_id'],
            primary_commit_id=r['primary_commit_id'],reference_by=r['reference_by'],
            recovery_obligation=r['recovery_obligation']))
    archived=materialize_plan(archive_plan,roots,max_bytes=256*1024**2)
    write_new(Path(roots['archive'])/'asset_index.json',{'contract':'ASSET_INDEX_V1','assets':archived})
    mapped={a['original_path']:a for a in archived}
    inventory=[];future=[]
    for r in history:
        original=str(Path(r['path']).resolve());copied=mapped.get(original)
        sha=r['content_sha256'] if len(r['content_sha256'])==64 else None
        logical='objects/'+sha+Path(original).suffix if sha else 'unverified/'+r['asset_id'].replace(':','_')
        entry={**r,'original_path':original,'target_logical_path':copied['relative_path'] if copied else logical,
            'archive_status':'COPIED_VERIFIED' if copied else 'PLANNED_NOT_COPIED',
            'classification':'RECOVERY_REQUIRED' if r['asset_type']=='DATABASE_RECOVERY_POINT' else 'ARCHIVE_REQUIRED',
            'reference_count':'UNKNOWN','retain_responsibility':r['recovery_obligation'],'delete_allowed':False}
        inventory.append(entry)
        if sha and r['asset_type']!='DATABASE_RECOVERY_POINT' and not copied:
            future.append({'original_path':original,'root_id':'archive','relative_path':logical,'sha256':sha,
                'size_bytes':int(r['size_bytes']),'asset_type':r['asset_type'],'source_version':r['source_version'],
                'run_id':r['run_id'],'sku':r['business_identity'],'reference_by':r['reference_by']})
    dump_csv(reports/'ARCHIVE_ASSET_INVENTORY.csv',inventory)
    write_new(reports/'ARCHIVE_MIGRATION_PLAN.json',{'status':'EXECUTABLE_BOUNDED_COPY_PLAN',
        'automatic_execution':False,'assets':future,'blocked_assets_without_full_hash':sum(not r['content_sha256'] or len(r['content_sha256'])!=64 for r in history),
        'instruction':'Select a capacity-controlled subset; call materialize_plan with explicit budget; never move originals.'})
    # Reuse all known database inventories/references. No whole-drive or full database hash scan.
    refs=json.loads((p05/'database_manifest_references.json').read_text(encoding='utf-8'))
    reference_map=defaultdict(list)
    for evidence in refs['references']:
        for link in evidence['db_references']:
            reference_map[str(Path(link['path']).resolve())].append(evidence['evidence_file'])
    duplicates=json.loads((p05/'verified_database_duplicates.json').read_text(encoding='utf-8'))
    hashes={f['path']:f['sha256'] for pair in duplicates['pairs'] for f in pair['files']}
    legacy=[]
    for r in rows(p05/'DATABASE_ASSET_INVENTORY.csv'):
        record=classify_legacy_database(r,evidence_files=reference_map[str(Path(r['path']).resolve())],full_hash=hashes.get(r['path']))
        path=Path(r['path'])
        record['current_exists']=path.is_file()
        record['size_bytes']=int(r['size_bytes'])
        if record['current_exists']:
            record['current_size_bytes']=path.stat().st_size
        if record['duplicate_content_verified']:
            record['unique_business_data']=False # Exact full-SHA pair evidence, not permission to delete.
        legacy.append(record)
    dump_csv(reports/'LEGACY_DATABASE_CLASSIFICATION.csv',legacy)
    cleanup=cleanup_preview(legacy)
    dump_csv(reports/'SAFE_CLEANUP_PREVIEW.csv',cleanup['assets'])
    dump_csv(reports/'SAFE_CLEANUP_WHITELIST.csv',cleanup['whitelist'],fields=['path','size_bytes','sha256',
        'cleanup_status','reference_count','alternative_recovery','risk_level','estimated_releasable_bytes','delete_allowed'])
    write_new(reports/'SAFE_CLEANUP_WHITELIST.json',cleanup)
    # Real recovery lifecycle: reuse P1 backup, restore and per-group retention.
    scratch=ScratchTask(Path(roots['scratch']),owner_task='p2-real-primary-validation',run_id='p2-primary',budget_bytes=2*1024**3)
    recovery=scratch.backup(baseline)
    resumed=ScratchTask.resume(Path(roots['scratch']),scratch.directory,
        owner_task='p2-real-primary-validation',run_id='p2-primary')
    again=resumed.backup(baseline)
    assert again['backup']['reused']
    retained=retention_preview([recovery['backup']])
    scratch_records=[dict(i) for i in scratch.assets.values()]
    # A small, newly created regeneratable file demonstrates the authorized scratch-only disposal boundary.
    disposable=ScratchTask(Path(roots['scratch']),owner_task='p2-small-owned-lifecycle',run_id='p2-small',budget_bytes=8192)
    content=b'owned scratch lifecycle evidence\n'
    source=reports/'retained_lifecycle_source.txt';source.write_bytes(content)
    proof=reports/'small_alternative_recovery.json';write_new(proof,{'status':'PASS','backup_sha256':sha256_file(source)})
    disposable.register('temporary.txt',source=str(source),expected_bytes=len(content),recovery_required=False)
    (disposable.directory/'temporary.txt').write_bytes(content)
    disposable.complete('temporary.txt',reference_count=0,references_verified=True,unique_business_data=False,
        retain_until=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
        alternative_recovery={'status':'PASS','path':str(source),'sha256':sha256_file(source),'evidence_path':str(proof)})
    disposed=disposable.discard_owned('temporary.txt');scratch_records.append(disposed)
    dump_csv(reports/'SCRATCH_LIFECYCLE_INVENTORY.csv',scratch_records)
    # Full file bytes + original identities were verified above; exercise roots with archive intentionally offline.
    image=next(a for a in active_copies if a['asset_type']=='IMAGE_MASTER')
    image_result=resolve_legacy_binding(image['original_path'],active_copies,roots)
    source_result=resolve_legacy_binding(archived[0]['original_path'],archived,roots)
    snapshot_sample=next(a for a in active_copies if a['asset_type']=='CURRENT_SNAPSHOT_EVIDENCE')
    snapshot_result=resolve_legacy_binding(snapshot_sample['original_path'],active_copies,roots)
    offline_archive=resolve_asset(archived[0],{'data':roots['data']})
    offline_image=resolve_legacy_binding(image['original_path'],active_copies,{'data':roots['data']})
    assert image_result['status']=='AVAILABLE' and source_result['status']=='AVAILABLE' and snapshot_result['status']=='AVAILABLE'
    assert offline_archive['status']=='ASSET_UNAVAILABLE' and offline_image['status']=='AVAILABLE'
    with read_database(Path(roots['data'])/'runtime/db/action_tracker.db',immutable=True) as db:
        candidate_identity=database_identity(db)
        candidate_orphans=db.execute('SELECT COUNT(*) FROM translation_units u LEFT JOIN translation_source_versions s '
                               'ON u.source_version_id=s.source_version_id WHERE s.source_version_id IS NULL').fetchone()[0]
    assert candidate_identity['commit_id']==m['commit_id'] and candidate_orphans==orphan_units
    original_guards=[{'path':a['original_path'],'sha256':a['sha256'],'size_bytes':a['size_bytes']} for a in active_copies+archived]
    write_new(reports/'ORIGINAL_FILE_GUARDS.json',original_guards)
    accounting=json.loads((p06/'migration_accounting.json').read_text(encoding='utf-8'))
    metrics={'status':'PASS','scope':'ISOLATED_STORAGE_CANDIDATE_NOT_PRODUCTION_DAILY','roots':roots,
        'active_files':len({a['target_path'] for a in active_copies}),
        'active_bytes':sum({a['target_path']:a['size_bytes'] for a in active_copies}.values()),
        'current_skus':len(current),'current_image_files':len(current_images),
        'missing_current_image_index_skus':sorted(current-{r['sku'] for r in current_images}),
        'archive_classified_files':len(inventory),'archive_classified_logical_bytes':sum(int(r['size_bytes']) for r in inventory),
        'archive_copied_bindings':len(archived),'archive_copied_files':len({a['target_path'] for a in archived}),
        'archive_copied_bytes':sum({a['target_path']:a['size_bytes'] for a in archived}.values()),
        'scratch_registered_assets':len(scratch_records),'scratch_retained_bytes':sum(r['size_bytes'] for r in scratch_records if r['task_status']!='DISPOSED'),
        'new_owned_scratch_disposed_bytes':disposed['released_bytes'],'old_assets_deleted':0,
        'legacy_database_inventory_files':len(legacy),'restore_check_role_candidates':sum(r['purpose_class']=='RESTORE_VALIDATION_COPY' for r in legacy),
        'legacy_database_role_counts':dict(Counter(r['purpose_class'] for r in legacy)),
        'verified_duplicate_database_paths':len(hashes),'cleanup_whitelist_files':len(cleanup['whitelist']),
        'estimated_legacy_releasable_bytes':cleanup['estimated_releasable_bytes'],
        'unknown_resolved_bytes':0,'remaining_unknown_bytes_prior_scope':accounting['classification']['M5_UNKNOWN'][1],
        'unknown_scope':'P06 M5 baseline retained; lifecycle candidacy is not business-role resolution',
        'projections':audit_result,'image_relocated':image_result,'source_relocated':source_result,
        'archive_offline':offline_archive,'current_image_with_archive_offline':offline_image,
        'snapshot_relocated':snapshot_result,
        'translation_source_versions':source_count,'translation_orphan_units_before':orphan_units,
        'translation_orphan_units_after':candidate_orphans,'translation_binding_sample':translation_sample,
        'recovery':recovery,'backup_resume_reused':again['backup']['reused'],'retention_preview':retained,
        'candidate_database_sha256':sha256_file(Path(roots['data'])/'runtime/db/action_tracker.db')}
    write_new(reports/'P2_REAL_DATA_VALIDATION.json',metrics)
    print(json.dumps({k:v for k,v in metrics.items() if k in ('status','active_files','active_bytes','archive_copied_files','archive_copied_bytes','current_image_files','cleanup_whitelist_files')},ensure_ascii=False))


if __name__=='__main__': main()
