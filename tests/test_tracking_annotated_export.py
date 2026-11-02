"""CI_SAFE: reviewed conflict tracking creates exact, non-mutating delivery annotations."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest
from action_tracker.exporting.annotated import AnnotatedDeliveryError, apply_annotation_plan, build_tracking_annotation_plan
from action_tracker.exporting.difference_whitelist import DifferenceWhitelist, field_source_hash, value_hash
from action_tracker.exporting.service import canonical_source_hash
from action_tracker.services.hashing import localization_field_source_hash
from test_annotated_projection import fixture


def registry_for(record, field):
    now = datetime.now(timezone.utc)
    return DifferenceWhitelist([{'id':'tracked:1001:'+field,'sku':'1001','field':field,'action':'ACK_DIFFERENCE',
        'status':'PENDING','source_hash':field_source_hash(record,field),'current_hash':value_hash(record.get(field)),
        'reviewed_hash':value_hash(record.get(field)),'reviewed_by':'reviewer','reviewed_at':now.isoformat(),
        'expires_at':(now+timedelta(days=3)).isoformat(),'reason':'来源待核','evidence':['test evidence'],
        'tracking_only':True,'automatic_apply':False,'does_not_waive_qa_or_approval':True}])


def make_plan(source, registry, repair):
    return build_tracking_annotation_plan(source,registry,repair,authorization_ref='user:request',authorization_text='带备注交付')


def test_invalid_source_note_is_retained_and_input_is_unchanged():
    source,rows,_,repair,strict=fixture(); registry=registry_for(source.records[0],'details_zh');before=deepcopy(rows)
    plan=make_plan(source,registry,repair)
    out,report=apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,conflict_registry=registry)
    assert rows==before and out[0]['产品详情']==before[0]['产品详情'] and 'Sustancia: Válido' in out[0]['备注']
    assert report['research_verified'] is False and not report['source_fact_resolved']


@pytest.mark.parametrize('field', ['source','chinese'])
def test_changed_binding_requires_recheck(field):
    source,_,_,repair,_=fixture();registry=registry_for(source.records[0],'details_zh')
    source.records[0]['details_es' if field=='source' else 'details_zh'] += 'changed'
    with pytest.raises(AnnotatedDeliveryError,match='RECHECK_REQUIRED'):make_plan(source,registry,repair)


def test_unapproved_blank_is_retained_only_for_matching_tracking_entry():
    source,rows,_,_,_=fixture();record=source.records[0];record['cat2_zh']='';rows[0]['分类2']=''
    record['zh_field_provenance']['cat2']={'value':'','source_hash':localization_field_source_hash(record,'cat2'),
        'review_status':'PENDING','freshness_status':'CURRENT','approved_by':None}
    registry=registry_for(record,'cat2_zh');repair={'findings':[]};strict={'status':'FAIL','counts':{'UNAPPROVED_ZH':2,'MISSING_REQUIRED_ZH':1},
        'issues':['UNAPPROVED_ZH:1001:cat2_zh','UNAPPROVED_ZH:1001:cat2_zh:status=PENDING']}
    plan=make_plan(source,registry,repair);assert plan['fields'][0]['action']=='RETAIN_PENDING_BLANK'
    with pytest.raises(AnnotatedDeliveryError,match='TRACKING_REQUIRED'):
        apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict)
    out,report=apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,conflict_registry=registry)
    assert not out[0]['分类2'] and '中文缺失，待复核' in out[0]['备注'] and not report['research_verified']


def test_untracked_new_blocker_cannot_be_waived():
    source,rows,_,repair,strict=fixture();registry=registry_for(source.records[0],'details_zh');plan=make_plan(source,registry,repair)
    strict={'status':'FAIL','counts':{'SOURCE_HASH_MISMATCH':1},'issues':['SOURCE_HASH_MISMATCH:1001:name']}
    with pytest.raises(AnnotatedDeliveryError,match='UNHANDLED_STRICT_RULE'):
        apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,conflict_registry=registry)


def test_expired_or_disabled_tracking_never_approves_delivery():
    source,_,_,repair,_=fixture(); registry=registry_for(source.records[0],'details_zh')
    item=next(iter(registry.entries.values())); item['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with pytest.raises(AnnotatedDeliveryError,match='RECHECK_REQUIRED'):make_plan(source,registry,repair)
    with pytest.raises(AnnotatedDeliveryError,match='TRACKING_NOT_ENABLED'):make_plan(source,None,repair)


def test_master_quality_failure_is_not_waived_by_whitelist():
    source,rows,_,repair,_=fixture();registry=registry_for(source.records[0],'details_zh');plan=make_plan(source,registry,repair)
    strict={'status':'FAIL','counts':{'MASTER_QUALITY_BLOCKED':1},'issues':['MASTER_QUALITY_BLOCKED']}
    with pytest.raises(AnnotatedDeliveryError,match='UNHANDLED_STRICT_RULE'):
        apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,conflict_registry=registry)


def test_unexplained_strict_failure_still_blocks():
    source,rows,_,repair,_=fixture();registry=registry_for(source.records[0],'details_zh');plan=make_plan(source,registry,repair)
    with pytest.raises(AnnotatedDeliveryError,match='UNEXPLAINED_STRICT_FAILURE'):
        apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,
                              strict_audit={'status':'FAIL','counts':{},'issues':[]},conflict_registry=registry)
