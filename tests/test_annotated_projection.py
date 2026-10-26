"""CI_SAFE: pure source-bound annotated delivery projection and tamper checks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from action_tracker.exporting.annotated import (
    AnnotatedDeliveryError, HEADERS, QUARANTINE_TEXT, apply_annotation_plan,
    full_delivery_rows_hash, verify_annotation_delivery,
)
from action_tracker.exporting.service import ExportSource, canonical_source_hash
from action_tracker.localization.hashes import value_hash
from action_tracker.services.hashing import localization_field_source_hash, localization_source_hash


def fixture(action='RETAIN_SOURCE_NOTE', stale=False):
    rec = dict(sku='1001', name_es='Producto', cat1_es='Vivienda', cat2_es='Decoración',
               spec_es='1 unidad', desc_es='Producto blanco',
               details_es='Color: Blanco; Sustancia: Válido; Número del artículo: 1001',
               name_zh='商品', cat1_zh='家居布置', cat2_zh='装饰', spec_zh='1件',
               desc_zh='白色商品', details_zh='颜色：白色；来源异常：官网字段Sustancia=Válido；商品编号：1001',
               current_price=1.99, original_price=None, unit_price='1,99 €/ud.',
               product_url='https://www.action.com/es-es/p/1001/producto/', image_url=None)
    rec['zh_field_provenance'] = {'details': {
        'value': rec['details_zh'], 'source_hash': localization_field_source_hash(rec, 'details'),
        'review_status':'APPROVED', 'freshness_status':'STALE' if stale else 'CURRENT',
        'approved_by':'service:current-localization-review',
    }}
    source = ExportSource('2026-10-26','run','SQLITE_CURRENT',(rec,),None,source_commit_id='commit')
    row = {h:None for h in HEADERS}
    row.update(编号='1001',标题='商品',分类1='家居布置',分类2='装饰',规格='1件',折后价=1.99,
               单价='1,99 €/件',描述='白色商品',产品详情=rec['details_zh'],商品链接=rec['product_url'],备注='已有备注')
    entry = dict(sku='1001',field='details',source_hash=localization_source_hash(rec),
                 field_source_hash=localization_field_source_hash(rec,'details'),
                 current_target_hash=value_hash(rec['details_zh']),source_es=rec['details_es'],
                 current_zh=rec['details_zh'],action=action,reason='官网物态值异常，不能猜测实际物态。',
                 allowed_repair_codes=['SOURCE_DETAILS_TYPED_VALUE_INVALID'])
    plan = dict(contract='ANNOTATED_DELIVERY_V1',base_commit_id='commit',source_hash=canonical_source_hash(source.records),
                export_date='2026-10-26',authorization_ref='user:annotation',authorization_text='保留来源异常备注后交付中文表',
                approved_by='service:current-localization-review',approval_mode='OWNER_AUTHORIZED_PROXY',
                approved_at=datetime.now(timezone.utc).isoformat(),
                expires_at=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(),fields=[entry])
    repair = {'findings':[dict(sku='1001',field='details_zh',code='SOURCE_DETAILS_TYPED_VALUE_INVALID',blocking=True)]}
    strict = {'status':'FAIL' if stale else 'PASS','counts':{'STALE_ZH':int(stale)},
              'issues':['STALE_ZH:1001:details_zh:STALE'] if stale else []}
    return source,[row],plan,repair,strict


def apply_case(case):
    source,rows,plan,repair,strict = case
    return apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),
                                 repair_audit=repair,strict_audit=strict)


def test_retained_note_preserves_value_fact_status_and_inputs():
    case = fixture();before = deepcopy(case)
    rows,report = apply_case(case)
    assert case == before
    assert rows[0]['产品详情'] == case[1][0]['产品详情']
    assert rows[0]['备注'].startswith('已有备注\n【产品详情：来源异常/冲突待核】')
    assert 'Sustancia: Válido' in rows[0]['备注']
    assert report['research_verified'] is False and report['annotated_delivery_ready'] is True
    assert report['fields'][0]['source_fact_resolved'] is False
    verify_annotation_delivery(case[0],case[1],rows,report)


def test_stale_quarantine_retains_failed_strict_audit():
    case = fixture('QUARANTINE',True);rows,report = apply_case(case)
    assert rows[0]['产品详情'] == QUARANTINE_TEXT
    assert case[0].records[0]['zh_field_provenance']['details']['freshness_status'] == 'STALE'
    assert report['strict_source_audit'] == case[4]
    assert report['strict_source_audit']['status'] == 'FAIL'
    assert report['fields'][0]['after'] == QUARANTINE_TEXT


@pytest.mark.parametrize('key,value',[
    ('contract','UNKNOWN'),('base_commit_id','old'),('source_hash','bad'),
    ('export_date','2026-10-25'),('authorization_ref',''),('authorization_text',''),('approved_by',''),
])
def test_plan_authorization_and_snapshot_binding_fail_closed(key,value):
    case=fixture();case[2][key]=value
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


def test_missing_plan_fails():
    source,rows,_,repair,strict=fixture()
    with pytest.raises(AnnotatedDeliveryError):
        apply_annotation_plan(source,rows,None,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict)


@pytest.mark.parametrize('change', ['expired','naive','future','missing_mode','wrong_actor','missing_date','overlong'])
def test_explicit_proxy_approval_is_time_bounded(change):
    case=fixture();plan=case[2];now=datetime.now(timezone.utc)
    if change=='expired':plan['expires_at']=(now-timedelta(seconds=1)).isoformat()
    elif change=='naive':plan['approved_at']=now.replace(tzinfo=None).isoformat()
    elif change=='future':plan['approved_at']=(now+timedelta(days=1)).isoformat()
    elif change=='missing_mode':plan.pop('approval_mode')
    elif change=='wrong_actor':plan['approved_by']='service:untrusted'
    elif change=='missing_date':plan.pop('approved_at')
    elif change=='overlong':plan['expires_at']=(now+timedelta(days=8)).isoformat()
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


@pytest.mark.parametrize('key,value',[
    ('source_hash','bad'),('field_source_hash','bad'),('current_target_hash','bad'),
    ('field','unknown'),('sku','9999'),('action','IGNORE_ALL'),('reason',''),
    ('source_es','changed'),('current_zh','changed'),('allowed_repair_codes',[]),
])
def test_field_binding_actions_and_exact_declared_rules_fail_closed(key,value):
    case=fixture();case[2]['fields'][0][key]=value
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


def test_duplicate_entries_and_unbound_metadata_fail():
    case=fixture();case[2]['fields']*=2
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)
    case=fixture();case[0].records[0]['zh_field_provenance']['details']['source_hash']=localization_source_hash(case[0].records[0])
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


def test_new_repair_or_strict_failure_cannot_be_ignored():
    case=fixture();case[3]['findings'].append(dict(sku='1001',field='details_zh',code='NUMERIC_DROPPED',blocking=True))
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)
    case=fixture();case[4].update(status='FAIL',counts={'SOURCE_HASH_MISMATCH':1},issues=['SOURCE_HASH_MISMATCH:1001:details'])
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)
    case=fixture();case[3]['findings'][0]['sku']='9999'
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


def test_partial_or_forged_source_note_is_rejected():
    case=fixture();case[1][0]['产品详情']=case[1][0]['产品详情'].replace('=Válido','=Válido, Loción')
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)
    case=fixture();case[1][0]['产品详情']='没有来源说明'
    with pytest.raises(AnnotatedDeliveryError):apply_case(case)


@pytest.mark.parametrize('column,value',[('备注','删掉备注'),('折后价',9.99),('产品详情','改动正文'),('编号','9999')])
def test_written_rows_tampering_fails_readback(column,value):
    case=fixture();rows,report=apply_case(case);rows[0][column]=value
    with pytest.raises(AnnotatedDeliveryError):verify_annotation_delivery(case[0],case[1],rows,report)


def test_report_hash_recalculation_does_not_authorize_arbitrary_cells():
    case=fixture();rows,report=apply_case(case);rows[0]['折后价']=9.99
    report['final_rows_hash']=report['full_rows_hash']=full_delivery_rows_hash(rows,HEADERS)
    with pytest.raises(AnnotatedDeliveryError):verify_annotation_delivery(case[0],case[1],rows,report)


def test_hash_includes_order_remarks_and_all_columns():
    case=fixture();row=case[1][0];second=deepcopy(row);second['编号']='1002'
    assert full_delivery_rows_hash([row,second],HEADERS)!=full_delivery_rows_hash([second,row],HEADERS)
    other=deepcopy(row);other['备注']='不同备注'
    assert full_delivery_rows_hash([row],HEADERS)!=full_delivery_rows_hash([other],HEADERS)
    with pytest.raises(AnnotatedDeliveryError):full_delivery_rows_hash([row],HEADERS[:-1])
