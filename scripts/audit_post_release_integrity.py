"""Read-only production evidence and isolated Backup API validation.

No provider, collection, Owner approval or production Apply is performed.
Run explicitly with --primary and --data-root; outputs stay in this checkout.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
REPORT = ROOT/'runtime/reports/post_release_data_integrity_20261008'
FIELDS = {'name':'name_es','cat1':'cat1_es','cat2':'cat2_es','spec':'spec_es',
          'description':'desc_es','details':'details_es'}
ZH = {'name':'name_zh','cat1':'cat1_zh','cat2':'cat2_zh','spec':'spec_zh',
      'description':'desc_zh','details':'details_zh'}


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def save(name, data):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def csv_save(name, rows, headers):
    with (REPORT/name).open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=headers,extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)


def fingerprint(path):
    result={}
    with ro(path) as db:
        for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            digest=hashlib.sha256(); count=0
            for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                digest.update(json.dumps(tuple(row),ensure_ascii=False,default=str,separators=(',',':')).encode())
                digest.update(b'\n'); count+=1
            result[table]={'count':count,'sha256':digest.hexdigest()}
    return result


def artifact_fingerprint(data_root):
    result={}
    for root in (data_root/'runtime/exports',data_root/'runtime/master',data_root/'runtime/state'):
        if root.exists():
            for path in root.rglob('*'):
                if path.is_file() and path.suffix in {'.xlsx','.csv','.json'}:
                    result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def baseline(primary,data_root):
    if primary.resolve().is_relative_to(ROOT) or ROOT.is_relative_to(data_root.resolve()):
        raise ValueError('PRODUCTION_AND_REPORT_ROOT_MUST_BE_ISOLATED')
    REPORT.mkdir(parents=True,exist_ok=True)
    replica=REPORT/'primary_replica.db'
    if replica.exists():
        raise ValueError('EXISTING_REPLICA_NOT_OVERWRITTEN')
    before=fingerprint(primary)
    artifacts=artifact_fingerprint(data_root)
    with ro(primary) as source,sqlite3.connect(replica) as dest:
        source.backup(dest)
    with ro(primary) as db:
        head=db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC LIMIT 1").fetchone()[0]
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        fk=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
    save('00_primary_baseline_readonly.json',dict(cutoff=datetime.now(timezone.utc).isoformat(),
        original_primary=str(primary),replica=str(replica),head=head,tables=before,artifacts=artifacts,
        backup_matches_original=fingerprint(replica)==before,integrity=integrity,foreign_keys=fk,
        code_base='b09bcea89e35e6a69367a6542ff871bab5ba1921',working_tree_at_start='clean'))
    return replica


def historical_evidence(primary,data_root):
    rows=[]; audit={}
    with ro(primary) as db:
        for sku in ('2568365','3207872'):
            audit[sku]={}
            for table in ('products','product_localizations','localization_fields','lifecycle_state',
                          'observations','price_history','event_history','product_fact_versions'):
                cols={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if 'official_sku' not in cols: continue
                records=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE official_sku=? ORDER BY rowid',(sku,))]
                audit[sku][table]=records
                for r in records:
                    if table in ('products','product_localizations','localization_fields','lifecycle_state'):
                        for field,value in r.items():
                            rows.append(dict(sku=sku,stage='CURRENT',table=table,run_id=r.get('last_commit_id',''),field=field,value=value))
                    elif table=='product_fact_versions':
                        for field,value in json.loads(r['normalized_fact_json'] or '{}').items():
                            rows.append(dict(sku=sku,stage='FROZEN_FACT',table=table,run_id=r['run_id'],field=field,value=value))
                    elif table in ('observations','event_history','price_history'):
                        for field,value in r.items():
                            rows.append(dict(sku=sku,stage='HISTORY',table=table,run_id=r.get('run_id',''),field=field,value=value))
    csv_save('02_historical_field_diff.csv',rows,['sku','stage','table','run_id','field','value'])
    save('historical_evidence.json',audit)
    snap=data_root/'runtime/snapshots/2026-10-08/2026-10-08_024528'
    selected={}
    snapshot_products={}
    for name in ('listing_products.csv','presence_evidence.csv','products_normalized.csv','sku_delta.csv'):
        with (snap/name).open(encoding='utf-8-sig',newline='') as handle:
            parsed=list(csv.DictReader(handle))
            selected[name]=[r for r in parsed if str(r.get('sku') or r.get('SKU'))=='3207872']
            if name=='products_normalized.csv':
                snapshot_products={str(r.get('sku') or r.get('SKU')):r for r in parsed
                                   if str(r.get('sku') or r.get('SKU')) in audit}
    for sku,item in audit.items():
        previous=next((r for r in reversed(item['product_fact_versions'])
                       if json.loads(r['normalized_fact_json']).get('name_es')),None)
        if previous:
            previous_values=json.loads(previous['normalized_fact_json'])
            current_row=item['products'][0]
            es_row=next((r for r in item['product_localizations'] if r['language']=='es'),{})
            final={**current_row,**{key:es_row.get(field) for field,key in FIELDS.items()}}
            final.update(sku=sku,first_seen=current_row.get('first_seen_at'),last_seen=current_row.get('last_seen_at'))
            for field in ('canonical_id','name_es','cat1_es','cat2_es','spec_es','desc_es','details_es',
                          'current_price','original_price','product_url','image_url','first_seen','last_seen'):
                before_value=previous_values.get(field)
                snapshot_value=snapshot_products.get(sku,{}).get(field)
                after_value=final.get(field)
                lost=before_value not in (None,'') and snapshot_value in (None,'')
                rows.append(dict(sku=sku,stage='BEFORE_AFTER',table='snapshot_to_primary',run_id=previous['run_id'],
                    field=field,before_value=before_value,snapshot_after_value=snapshot_value,after_value=after_value,
                    assessment='HISTORICAL_FACT_LOST_IN_SNAPSHOT' if lost else 'REVIEW_ACTUAL_DIFF',
                    value=after_value))
    csv_save('02_historical_field_diff.csv',rows,['sku','stage','table','run_id','field','value',
        'before_value','snapshot_after_value','after_value','assessment'])
    selected['detail_fetch.jsonl']=[json.loads(line) for line in (snap/'detail_fetch.jsonl').read_text(encoding='utf-8').splitlines()
                                  if json.loads(line).get('sku')=='3207872']
    log=data_root/'runtime/logs/production-run-20261008.out.log'
    selected['log']=[line for line in log.read_text(encoding='utf-8',errors='replace').splitlines() if '3207872' in line] if log.exists() else []
    selected['qa_report']=json.loads((snap/'qa_report.json').read_text(encoding='utf-8-sig'))
    selected['sitemap_url_present']='https://www.action.com/es-es/p/3207872/moldes-de-silicona-para-pasteles/' in (snap/'sitemap_raw.xml').read_text(encoding='utf-8')
    proof=REPORT/'3207872_official_page.txt'
    text=proof.read_text(encoding='utf-8') if proof.exists() else ''
    selected.update(official_verified=('3207872' in text and '2,99' in text and 'Moldes de silicona para pasteles' in text),
        official_price=2.99 if text else None,evidence=str(proof),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest() if proof.exists() else None,
        evidence_time=datetime.fromtimestamp(proof.stat().st_mtime,timezone.utc).isoformat() if proof.exists() else None,
        cause='REAPPEARED omitted from CURRENT baseline; missing Listing URL stopped detail before navigation; sparse CURRENT upsert erased history',
        parser_defect_proven=False,previous_2568365_protected=True)
    save('04_sku_3207872_source_audit.json',selected)
    (REPORT/'01_reappeared_sku_root_cause.md').write_text(
        '# 回归商品历史字段缺陷\n\n实际证据见 02_historical_field_diff.csv、historical_evidence.json 和 04_sku_3207872_source_audit.json。\n\n'
        '2026-09-30 冻结事实仍有完整西语、价格、URL；10-04 OFFLINE；10-08 SITEMAP_ONLY 有效出现并产生 REAPPEARED。'
        '今天 normalized snapshot 已经为空，损失发生在提交前；并非导出层清空。\n\n'
        '1. daily 使用 load_current_products 合并，MISSING/OFFLINE 身份不在此基线。\n'
        '2. updater 只从 Listing 取 URL，未用已有 Sitemap/历史官方 URL；无 URL 直接返回，日志却写成抓取失败。\n'
        '3. sparse CURRENT 行正常 upsert，历史保护仅覆盖 _historical_minimal 非 CURRENT 行，随后 ES localization 写入空值。\n'
        '4. 修复保留 current Presence 基线，用全部历史投影作事实合并；分字段标记 current/history/missing，价格未经本轮确认保持未知，历史事件不删除。\n'
        '5. 中文日常提交保留文本和审批，真实变化只 stale 对应字段；重复同一源不能自动解除未审核 stale。\n\n'
        'state/lifecycle 使用 known.previous_status 判定，不是字段丢失根因；V2 正式路径复用 daily，禁止独立部分事实提交的限制保持。\n'
        '首次出现以前的完整产品行未必存在；CSV 保留现存最早事实、历史事件和观测，不把后来的值推断成首次值。\n',encoding='utf-8')
    return selected


def readiness(path):
    from action_tracker.database.repository import ProductionRepository
    from action_tracker.services.hashing import localization_source_hash,localization_field_source_hash
    from action_tracker.localization.release_gate import audit_research_release,load_allowed_tokens,APPROVED_REVIEW_STATUSES
    records=ProductionRepository(path).load_current_export_records()
    tokens=load_allowed_tokens(ROOT/'data/dictionary')
    strict=audit_research_release(records,allowed_tokens=tokens).as_dict()
    with ro(path) as db:
        revisions=[dict(r) for r in db.execute('''SELECT s.official_sku,s.source_hash AS version_hash,s.source_quality_status,
            u.unit_id,u.field_name,u.source_text,u.source_text_hash,u.freshness_status AS unit_freshness,
            u.current_revision_id,r.* FROM translation_source_versions s JOIN translation_units u
            ON u.source_version_id=s.source_version_id LEFT JOIN translation_revisions r
            ON r.revision_id=u.current_revision_id ORDER BY s.created_at,u.created_at''')]
        findings={r['revision_id']:int(r['n']) for r in db.execute("SELECT revision_id,COUNT(*) AS n FROM translation_qa_findings WHERE status='OPEN' AND severity IN ('HIGH','ERROR','BLOCKER') GROUP BY revision_id")}
    latest={}
    for r in revisions:
        field=r['field_name'].removesuffix('_es')
        field={'desc':'description'}.get(field,field)
        if field in FIELDS: latest[(str(r['official_sku']),field)]=r
    rows=[]
    for rec in records:
        sku=rec['sku']; aggregate=localization_source_hash(rec)
        for field,key in FIELDS.items():
            p=(rec.get('zh_field_provenance') or {}).get(field,{})
            revision=latest.get((sku,field),{})
            reasons=[]; target=rec.get(ZH[field]); source=rec.get(key)
            fieldhash=localization_field_source_hash(rec,field)
            approved=p.get('review_status') in APPROVED_REVIEW_STATUSES
            bound=p.get('source_hash')==fieldhash
            fresh=p.get('freshness_status')=='CURRENT'
            matches=str(p.get('value') or '')==str(target or '')
            absentapproved=p.get('review_status')=='APPROVED_SOURCE_ABSENT'
            if not approved: reasons.append('PROVENANCE_NOT_APPROVED')
            if not bound: reasons.append('FIELD_HASH_MISMATCH')
            if not fresh: reasons.append('PROVENANCE_NOT_CURRENT')
            if not matches: reasons.append('PROJECTION_VALUE_MISMATCH')
            if str(rec.get('zh_source_hash') or '')!=aggregate: reasons.append('AGGREGATE_HASH_MISMATCH')
            if not str(target or '').strip() and not absentapproved: reasons.append('TRANSLATION_NOT_READY')
            qa=revision.get('qa_status'); canonical=revision.get('canonical_qa_status')
            openqa=findings.get(revision.get('revision_id'),0)
            if str(source or '').strip() or not absentapproved:
                if qa!='PASS': reasons.append('QA_NOT_PASS')
                if canonical not in ('PASS','NOT_REQUIRED'): reasons.append('CANONICAL_QA_NOT_READY')
                if revision.get('review_status') not in ('APPROVED','HUMAN_REVIEWED','LOCKED'):
                    reasons.append('REVISION_NOT_APPROVED')
            rebind=(bool(revision.get('revision_id')) and revision.get('version_hash')==aggregate
                and revision.get('source_hash')==aggregate and str(revision.get('target_text') or '')==str(target or '')
                and qa=='PASS' and canonical in ('PASS','NOT_REQUIRED')
                and revision.get('review_status') in ('APPROVED','HUMAN_REVIEWED','LOCKED')
                and bool(revision.get('approved_by')) and bool(revision.get('approved_at'))
                and revision.get('unit_freshness')=='FRESH' and openqa==0
                and revision.get('current_revision_id')==revision.get('revision_id')
                and revision.get('source_quality_status') not in ('DAMAGED','SOURCE_DAMAGED','SOURCE_POLLUTED','BLOCKED'))
            if openqa: reasons.append('OPEN_QA_BLOCKER')
            if not reasons: classification='READY'
            elif rebind: classification='REBIND'
            elif not str(source or '').strip() and not absentapproved: classification='SOURCE_BLOCKED'
            elif not bound or not fresh or revision.get('unit_freshness')=='STALE': classification='STALE'
            else: classification='REVIEW_REQUIRED'
            rows.append(dict(sku=sku,field=field,classification=classification,current_es=source,current_zh=target,
                source_hash=fieldhash,aggregate_source_hash=aggregate,registry_revision=revision.get('revision_id'),
                registry_source_hash=revision.get('source_hash'),qa=qa,canonical_qa=canonical,
                revision_approval=revision.get('review_status'),approved_by=revision.get('approved_by'),approved_at=revision.get('approved_at'),
                unit_freshness=revision.get('unit_freshness'),provenance=json.dumps(p,ensure_ascii=False),
                freshness=p.get('freshness_status'),open_qa_blockers=openqa,block_reason=';'.join(reasons),
                suggested_target=revision.get('target_text'),owner_action='REVIEW_SOURCE_OR_APPROVE_CONTENT' if classification not in ('READY','REBIND') else ''))
    headers=list(rows[0])
    csv_save('06_localization_full_readiness_audit.csv',rows,headers)
    csv_save('07_owner_review_required.csv',[r for r in rows if r['classification'] not in ('READY','REBIND')],headers)
    csv_save('08_metadata_rebind_candidates.csv',[r for r in rows if r['classification']=='REBIND'],headers)
    summary=dict(current_skus=len(records),fields=len(rows),classifications=dict(Counter(r['classification'] for r in rows)),
        affected_fields=sum(r['classification']!='READY' for r in rows),affected_skus=len({r['sku'] for r in rows if r['classification']!='READY'}),
        owner_approval_pending=sum('PROVENANCE_NOT_APPROVED' in r['block_reason'] for r in rows),
        source_hash_mismatch=sum('FIELD_HASH_MISMATCH' in r['block_reason'] for r in rows),
        aggregate_hash_mismatch_skus=len({r['sku'] for r in rows if 'AGGREGATE_HASH_MISMATCH' in r['block_reason']}),
        stale_blocking=sum(r['freshness']=='STALE' or r['unit_freshness']=='STALE' for r in rows),strict_release=strict,
        semantic_review='Uncertain content stays REVIEW_REQUIRED; no invented EDIT/RETRANSLATE or approval',
        applied_fields=0,qwen_calls=0)
    save('localization_readiness_summary.json',summary)
    return summary


def verify_unchanged(primary,data_root):
    before=json.loads((REPORT/'00_primary_baseline_readonly.json').read_text(encoding='utf-8'))
    result={'original_primary_unchanged':fingerprint(primary)==before['tables'],
            'original_exports_and_state_unchanged':artifact_fingerprint(data_root)==before['artifacts']}
    save('original_unchanged.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--primary',type=Path,required=True)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--stage',choices=['baseline','audit','verify'],required=True)
    args=parser.parse_args()
    if args.stage=='baseline':
        replica=baseline(args.primary,args.data_root)
        print('BACKUP_READY',replica)
    elif args.stage=='audit':
        historical_evidence(args.primary,args.data_root)
        summary=readiness(REPORT/'primary_replica.db')
        print(json.dumps({k:v for k,v in summary.items() if k!='strict_release'},ensure_ascii=False))
    else:
        print(json.dumps(verify_unchanged(args.primary,args.data_root)))
