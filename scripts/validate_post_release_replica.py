"""Controlled official candidate validation and Phase 3; isolated replica only."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from audit_post_release_integrity import REPORT,save,ro,fingerprint,readiness


def validate_candidate():
    from action_tracker.database.production import ProductionWriter,CommitBundle,apply_detail_corrections
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.products.parser import _normalize_detail
    from action_tracker.products.updater import verified_detail_url
    replica=REPORT/'primary_replica.db'
    raw_path=REPORT/'3207872_official_raw.json'
    raw=json.loads(raw_path.read_text(encoding='utf-8'))
    sku='3207872'; url=raw.get('product_url')
    if raw.get('sku')!=sku or not verified_detail_url(sku,url):
        raise ValueError('OFFICIAL_IDENTITY_NOT_VERIFIED')
    detail=_normalize_detail(raw,url)
    if detail.get('current_price') is None or detail['current_price']<=0:
        raise ValueError('OFFICIAL_PRICE_NOT_VERIFIED')
    source=json.loads((REPORT/'04_sku_3207872_source_audit.json').read_text(encoding='utf-8'))
    if not source['official_verified']: raise ValueError('OFFICIAL_BROWSER_EVIDENCE_REQUIRED')
    repo=ProductionRepository(replica); info=repo.latest_commit_info()
    with ro(replica) as db:
        old=dict(db.execute('SELECT * FROM products WHERE official_sku=?',(sku,)).fetchone())
        protected={table:[tuple(r) for r in db.execute(f'SELECT * FROM {table} WHERE official_sku=? ORDER BY rowid',('2568365',))]
                   for table in ('products','product_localizations','localization_fields','price_history','event_history')}
        zh_before=[tuple(r) for r in db.execute("SELECT official_sku,name,cat1,cat2,spec,description,details FROM product_localizations WHERE language='zh' ORDER BY official_sku")]
    # Restore only the official identity/Spanish fields confirmed by this page.
    # No Chinese revision or approval is created. This companion candidate is
    # explicit, because correcting price alone still leaves a missing URL/name.
    identity_keys=('name_es','cat1_es','cat2_es','spec_es','desc_es','details_es','product_url','image_url')
    identity={k:detail[k] for k in identity_keys}
    detail_result=apply_detail_corrections(replica,parent_run_id=info['run_id'],
        details_by_sku={sku:identity},mode='APPLY')
    head=repo.current_head()
    with ro(replica) as db:
        current=dict(db.execute('SELECT * FROM products WHERE official_sku=?',(sku,)).fetchone())
    row={**current,'sku':sku,'current_price':detail['current_price']}
    run_id='replica_official_price_3207872_'+source['evidence_sha256'][:12]
    bundle=CommitBundle(run_id=run_id,observation_date='2026-10-08',qa_state='PASS',
        current_products=(row,),base_commit_id=head,
        price_events=({'sku':sku,'canonical_id':row['canonical_id'],'date':'2026-10-08',
                       'old_price':current['current_price'],'new_price':detail['current_price'],'change_type':'CORRECTION'},),
        run_record={'operation':'OFFICIAL_PRICE_CORRECTION_REPLICA_ONLY','qa_state':'PASS',
                    'evidence_sha256':source['evidence_sha256'],'evidence_time':source['evidence_time']})
    writer=ProductionWriter(replica,role='PRIMARY'); price_commit=writer.commit(bundle)
    after=fingerprint(replica)
    assert writer.commit(bundle)==price_commit
    assert fingerprint(replica)==after
    with ro(replica) as db:
        protected_after={table:[tuple(r) for r in db.execute(f'SELECT * FROM {table} WHERE official_sku=? ORDER BY rowid',('2568365',))] for table in protected}
        zh_after=[tuple(r) for r in db.execute("SELECT official_sku,name,cat1,cat2,spec,description,details FROM product_localizations WHERE language='zh' ORDER BY official_sku")]
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        fk=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        price_events=[dict(r) for r in db.execute('SELECT * FROM price_history WHERE run_id=?',(run_id,))]
    candidate={'status':'READY','sku':sku,'production_apply':False,'requires_separate_controlled_production_operation':True,
        'primary_base_head':info['commit_id'],'verified_price':detail['current_price'],'old_price':old['current_price'],
        'official_evidence':str(REPORT/'3207872_official_page.txt'),'evidence_time':source['evidence_time'],
        'evidence_sha256':source['evidence_sha256'],'raw_page_extraction_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        'qa':'PASS','price_change_fields':['current_price'],
        'companion_official_source_candidate':identity,'companion_detail_result':detail_result,
        'price_commit':price_commit,'price_history_events':price_events,'history_deletions':0,
        'idempotent':'PASS','protected_2568365_unchanged':protected==protected_after,
        'all_chinese_targets_unchanged':zh_before==zh_after,'integrity':integrity,'foreign_keys':fk,
        'qwen_calls':0,'owner_approvals_created':0,'metadata_rebind_applied':0}
    assert candidate['protected_2568365_unchanged'] and candidate['all_chinese_targets_unchanged']
    save('05_sku_3207872_repair_candidate.json',candidate)
    return candidate


def phase3(data_root):
    from action_tracker.config import load_settings
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.exporting.service import (ExportSource,build_final_zh_projection,validate_source_records,
        validate_spanish_source_fields,build_es_rows,validate_zh_rows_against_source,validate_production_release,
        export_catalog,canonical_export_rows_hash,_read_catalog_rows,ExportValidationError)
    from action_tracker.exporting.excel_writer import write_catalog_xlsx
    from action_tracker.localization.release_gate import audit_research_release,load_allowed_tokens
    from action_tracker.data_quality.master_gate import audit_master_quality
    from action_tracker.exporting.profiles import load_profile
    replica=REPORT/'primary_replica.db'
    cfg=load_settings(data_root/'config/settings.yaml')
    # All write-capable paths are explicit isolated paths. Dictionaries and
    # cached images can be read from the production assets; export is read-only.
    paths={k:REPORT/'isolated_paths'/k for k in cfg['paths']}
    paths['master']=REPORT/'isolated_paths/master/Action_Master.xlsx'
    for key in ('dictionary','dictionary_baseline','images'):
        original=Path(cfg['paths'][key])
        paths[key]=original if original.is_absolute() and original.is_relative_to(data_root) else data_root/original.relative_to(ROOT)
    cfg['paths']=paths
    cfg['storage']={'mode':'SQLITE_PRIMARY','db_path':replica}
    cfg['localization']['production_apply_enabled']=False
    cfg['localization']['auto_approval_enabled']=False
    cfg['localization']['ai']['enabled']=False
    cfg['knowledge']['production_apply_enabled']=False
    repo=ProductionRepository(replica)
    records=repo.load_current_export_records(); info=repo.latest_commit_info()
    source=ExportSource(export_date='2026-10-08',run_id=info['run_id'],kind='SQLITE_CURRENT',
        records=tuple(records),source_master_file_hash=None,source_commit_id=info['commit_id'])
    result={'mode':'ISOLATED_REPLICA','formal_publication':False,'current_skus':len(records),
            'replica_head':info['commit_id'],'qwen_calls':0,'owner_approvals_created':0,'checks':{},'blockers':[]}
    try:
        validate_source_records(records,export_date='2026-10-08')
        validate_spanish_source_fields(records)
        result['checks']['ES_AUDIT']='PASS'
    except ExportValidationError as exc:
        result['checks']['ES_AUDIT']='BLOCKED'; result['blockers'].append(str(exc))
    quality=audit_master_quality(replica).as_dict()
    result['master_quality']=quality
    result['checks']['MASTER_QUALITY_GATE']='PASS' if quality['release_ready'] else 'BLOCKED'
    strict=audit_research_release(records,allowed_tokens=load_allowed_tokens(ROOT/'data/dictionary'),master_quality=quality)
    result['strict_audit']=strict.as_dict(); result['blockers'].extend(strict.issues)
    result['checks']['ZH_APPROVAL_SOURCE_AUDIT']='PASS' if strict.ok else 'BLOCKED'
    try:
        projection=build_final_zh_projection(cfg,source)
        zh=list(projection.rows); es=build_es_rows(records)
        result['repair_audit']=projection.repair_audit
        save('replica_export_repair_report.json',projection.repair_report.as_dict())
        validate_zh_rows_against_source(zh,records)
        assert [r['编号'] for r in es]==[r['编号'] for r in zh]
        result['checks']['ES_ZH_PARITY']='PASS'
        result['checks']['EXPORT_REPAIR_GATE']='PASS' if projection.repair_audit['release_ready'] else 'BLOCKED'
        try:
            validate_production_release(cfg,source,zh)
            result['checks']['STRICT_RELEASE_GATE']='PASS'
        except ExportValidationError as exc:
            result['checks']['STRICT_RELEASE_GATE']='BLOCKED';result['blockers'].append(str(exc))
        candidates=[]
        for language,rows in (('es',es),('zh',zh)):
            profile=load_profile(cfg,language=language,no_images=True)
            headers=[column['header'] for column in profile.columns]
            target=REPORT/'candidate_only_NOT_FORMAL'/f'20261008_{language}_candidate_NOT_FORMAL.xlsx'
            write_catalog_xlsx(target,headers=headers,rows=rows,workbook_format=profile.workbook_format)
            actual=_read_catalog_rows(target,headers)
            expected_hash=canonical_export_rows_hash(rows);actual_hash=canonical_export_rows_hash(actual)
            assert expected_hash==actual_hash
            candidates.append({'language':language,'path':str(target),'audited_hash':expected_hash,
                'reread_hash':actual_hash,'hash_parity':True,'rows':len(actual),
                'workbook_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'formal_release':False})
        result['candidate_workbooks']=candidates
        result['checks']['TEMP_XLSX_REREAD_HASH']='PASS'
    except (ExportValidationError,AssertionError) as exc:
        result['blockers'].append(str(exc)); result['checks']['FINAL_PROJECTION']='BLOCKED'
    try:
        export_catalog(cfg,language='zh',export_date='2026-10-08',no_images=True,research_release=True)
        result['checks']['FORMAL_EXPORT_INTERFACE']='PASS'
    except ExportValidationError as exc:
        result['checks']['FORMAL_EXPORT_INTERFACE']='BLOCKED'; result['blockers'].append(str(exc))
    with ro(replica) as db:
        result['integrity']=db.execute('PRAGMA integrity_check').fetchone()[0]
        result['foreign_keys']=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
    result['blockers']=list(dict.fromkeys(result['blockers']))
    result['production_release']='BLOCKED' if result['blockers'] else 'PASS'
    result['data_replica_ready']=not result['blockers']
    # A BLOCKED real replica is the correct result when Owner decisions remain.
    # Isolated candidate spreadsheets are never reported as formal releases.
    save('09_phase3_replica_audit.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--stage',choices=['candidate','phase3'],required=True)
    args=parser.parse_args()
    if ROOT.is_relative_to(args.data_root.resolve()):
        raise ValueError('REPLICA_WORKTREE_MUST_BE_ISOLATED')
    if args.stage=='candidate':
        result=validate_candidate()
        print(json.dumps({k:result[k] for k in ('status','sku','verified_price','production_apply','idempotent','protected_2568365_unchanged')},ensure_ascii=False))
    else:
        result=phase3(args.data_root)
        print(json.dumps({'production_release':result['production_release'],'checks':result['checks'],'blocker_findings':len(result['blockers'])},ensure_ascii=False))
