"""LOCAL_ONLY: supplement a known P2 candidate with release/ZIP/derivative evidence."""
import argparse
import csv
import json
from pathlib import Path

from action_tracker.operations.storage_foundation import read_database,sha256_file,resolve_asset
from action_tracker.operations.storage_lifecycle import materialize_plan,materialize_zip_member,write_new
from storage_p2_candidate import asset,dump_csv,rows


def revise(path, value):
    # Only revise this task's known generated JSON, preserving the complete prior version.
    path=Path(path);before=path.read_bytes();sha=sha256_file(path)
    archive=path.with_name(path.stem+'.prior-'+sha[:12]+path.suffix)
    with archive.open('xb') as out: out.write(before)
    temporary=path.with_name(path.name+'.next')
    write_new(temporary,value)
    if sha256_file(path)!=sha: raise RuntimeError('GENERATED_MANIFEST_CHANGED_CONCURRENTLY')
    temporary.replace(path)


def main():
    p=argparse.ArgumentParser();p.add_argument('--reports',required=True);p.add_argument('--audit-root',required=True)
    p.add_argument('--data-root',required=True);args=p.parse_args()
    reports=Path(args.reports);p06=Path(args.audit_root)/'ActionSKUTracker_P06_20261014';data=Path(args.data_root).resolve()
    manifest=json.loads((reports/'ACTIVE_DATA_CANDIDATE_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('contract')!='ACTIVE_DATA_CANDIDATE_V1' or manifest.get('production_enabled') is not False:
        raise RuntimeError('UNKNOWN_MANIFEST')
    roots=manifest['roots'];db=Path(roots['data'])/'runtime/db/action_tracker.db'
    release=data/'runtime/exports/20261007Action商品全量_中文版_不带图.manifest.json'
    d=json.loads(release.read_text(encoding='utf-8'))
    if d.get('release_mode')!='production_release' or d.get('export_repair',{}).get('release_ready') is not True:
        raise RuntimeError('PRODUCTION_RELEASE_EVIDENCE_MISSING')
    with read_database(db,immutable=True) as conn:
        commit=conn.execute('SELECT status FROM commit_batches WHERE commit_id=?',(d['artifact_source_commit_id'],)).fetchone()
    if not commit or commit[0]!='COMMITTED':raise RuntimeError('EXPORT_SOURCE_COMMIT_UNAVAILABLE')
    exports=[release,release.parent/d['output_file'],release.parent/d['export_repair']['repair_report']]
    plan=[asset(f,'data',f.relative_to(data).as_posix(),asset_type='EXISTING_DECLARED_PRODUCTION_RELEASE',
          primary_commit_id=d['artifact_source_commit_id'],run_id=d['run_id'],
          source_version=d['source_master_hash']) for f in exports]
    copied=materialize_plan(plan,roots,max_bytes=128*1024**2)
    # Existing image derivative is distinct, with explicit master Hash/profile linkage.
    derivative=data/'runtime/images/derivatives/excel_250/10280.png'
    derivative_evidence=derivative.with_suffix('.json')
    derivative_result={'status':'ASSET_UNAVAILABLE','source_quality_promoted':False}
    if derivative.is_file() and derivative_evidence.is_file():
        metadata=json.loads(derivative_evidence.read_text(encoding='utf-8'))
        master=next(a for a in rows(data/'runtime/images/manifests/image_manifest.csv') if a['sku']=='10280')
        if metadata.get('master_hash')==master['master_hash']:
            more=materialize_plan([asset(f,'data',f.relative_to(data).as_posix(),
                asset_type='IMAGE_DERIVATIVE_EVIDENCE',sku='10280',source_version=metadata['profile_version'],
                master_hash=metadata['master_hash']) for f in (derivative,derivative_evidence)],roots,max_bytes=16*1024**2)
            copied.extend(more);derivative_result={'status':'COPIED_VERIFIED','assets':more}
        else:derivative_result={'status':'STALE_DERIVATIVE','source_quality_promoted':False}
    # The extra CURRENT SKU was already bound to source URL/full member SHA by P0.6.
    zip_rows=rows(p06/'ARCHIVED_IMAGE_ENTRY_BINDINGS.csv')
    z=next(r for r in zip_rows if r['sku']=='2530200')
    existing=json.loads((p06/'additional_current_archived_image_verified.json').read_text(encoding='utf-8'))
    if z['recorded_full_sha256']!=existing['sha256'] or z['source_url']!=existing['source_url']:
        raise RuntimeError('ZIP_SOURCE_BINDING_CONFLICT')
    zip_item={'container_path':str(Path('F:/按日期整理/图片汇总.zip').resolve()),'member':z['entry'],
        'root_id':'archive','relative_path':'zip-members/'+existing['sha256']+'.png',
        'sha256':existing['sha256'],'size_bytes':int(z['bytes']),'sku':z['sku'],
        'asset_type':'ZIP_IMAGE_MEMBER','source_url':z['source_url'],
        'source_version':'P06_PRIOR_VERIFICATION_20261013','reference_by':z['source_evidence']}
    extracted=materialize_zip_member(zip_item,roots)
    if extracted['status']=='COPIED_VERIFIED':
        zip_copy=asset(Path(extracted['target_path']),'data','runtime/images/assets/2530200/master.png',
            sha=extracted['sha256'],asset_type='ZIP_IMAGE_MEMBER_CANDIDATE',sku='2530200',
            source_url=z['source_url'],source_version=zip_item['source_version'],
            original_container_path=zip_item['container_path'],original_member=zip_item['member'])
        copied.extend(materialize_plan([zip_copy],roots,max_bytes=16*1024**2))
    before_sha=sha256_file(reports/'ACTIVE_DATA_CANDIDATE_MANIFEST.json')
    manifest['assets'].extend(copied);manifest['previous_manifest_sha256']=before_sha;manifest['revision']=2
    revise(reports/'ACTIVE_DATA_CANDIDATE_MANIFEST.json',manifest)
    revise(Path(roots['data'])/'asset_index.json',{'contract':'ASSET_INDEX_V1','assets':manifest['assets']})
    archive_index=Path(roots['archive'])/'asset_index.json'
    if extracted['status']=='COPIED_VERIFIED':
        archived=json.loads(archive_index.read_text(encoding='utf-8'));archived['assets'].append(extracted)
        revise(archive_index,archived)
    extra={'status':'PASS' if extracted['status']=='COPIED_VERIFIED' else 'PASS_WITH_ASSET_UNAVAILABLE',
        'export_assets':copied[:3],'export_source_commit':d['artifact_source_commit_id'],
        'export_date':d['export_date'],'export_is_current_head':False,'zip_member':extracted,
        'zip_member_resolver':resolve_asset(extracted,roots) if extracted['status']=='COPIED_VERIFIED' else extracted,
        'derivative':derivative_result,
        'candidate_current_image_missing_skus_after_zip':38 if extracted['status']=='COPIED_VERIFIED' else 39,
        'registry_or_official_manifest_modified':False,
        'zip_whole_container_integrity':'UNVERIFIED','copied_assets':copied}
    write_new(reports/'P2_SUPPLEMENT_VALIDATION.json',extra)
    guards=json.loads((reports/'ORIGINAL_FILE_GUARDS.json').read_text(encoding='utf-8'))
    guards.extend({'path':a['original_path'],'sha256':a['sha256'],'size_bytes':a['size_bytes']} for a in copied)
    revise(reports/'ORIGINAL_FILE_GUARDS.json',guards)
    print(json.dumps({'status':extra['status'],'supplement_files':len(copied),'zip_member_status':extracted['status'],
                     'derivative':derivative_result['status'],
                     'missing_current_image_candidates':extra['candidate_current_image_missing_skus_after_zip']}))


if __name__=='__main__':main()
