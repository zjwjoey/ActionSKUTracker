"""CI_SAFE: disclosed source-price conflicts never change facts or strict QA."""
from copy import deepcopy
import pytest
from action_tracker.exporting.annotated import AnnotatedDeliveryError, original_price_notices, apply_annotation_plan, verify_annotation_delivery
from action_tracker.exporting.service import canonical_source_hash
from test_annotated_projection import fixture


def price_case(original=1.99):
    source,rows,plan,repair,_=fixture();rec=source.records[0];rec['original_price']=original
    plan['source_hash']=canonical_source_hash(source.records)
    quality={'status':'FAIL','release_ready':False,'commit_id':source.source_commit_id,'records_checked':1,
             'counts':{'INVALID_ORIGINAL_PRICE':1},'issues':['INVALID_ORIGINAL_PRICE:1001']}
    plan['original_price_notices']=original_price_notices(source,rows,quality)
    strict={'status':'FAIL','counts':{'MASTER_QUALITY_BLOCKED':1},'issues':['MASTER_QUALITY_BLOCKED']}
    return source,rows,plan,repair,strict,quality


@pytest.mark.parametrize('original',[1.99,1.50])
def test_equal_and_lower_original_prices_are_disclosed_without_changing_source(original):
    source,rows,plan,repair,strict,quality=price_case(original);before=deepcopy((source,rows))
    final,report=apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,master_quality=quality)
    assert (source,rows)==before and final[0]['原价'] is None and final[0]['折后价']==1.99
    assert '原价待核' in final[0]['备注'] and f'{original:g}欧元' in final[0]['备注']
    assert report['strict_source_audit']==strict and report['master_quality_audit']==quality
    assert report['research_verified'] is False
    verify_annotation_delivery(source,rows,final,report)


@pytest.mark.parametrize('change', ['filled_display','changed_current','invalid_current','negative_original','missing_original','valid_higher_original','other_issue','wrong_head','wrong_count'])
def test_price_disclosure_rejects_unsafe_or_unbound_cases(change):
    source,rows,_,_,_,quality=price_case();rec=source.records[0]
    if change=='filled_display':rows[0]['原价']=1.99
    elif change=='changed_current':rows[0]['折后价']=9
    elif change=='invalid_current':rec['current_price']=float('nan')
    elif change=='negative_original':rec['original_price']=-1
    elif change=='missing_original':rec['original_price']=None
    elif change=='valid_higher_original':rec['original_price']=9
    elif change=='other_issue':quality['issues'].append('FIELD_PROVENANCE_MISSING:1001:name')
    elif change=='wrong_head':quality['commit_id']='old'
    elif change=='wrong_count':quality['counts']['INVALID_ORIGINAL_PRICE']=2
    with pytest.raises(AnnotatedDeliveryError):original_price_notices(source,rows,quality)


def test_default_plan_cannot_waive_master_quality_and_readback_rejects_tampering():
    source,rows,plan,repair,strict,quality=price_case();without=deepcopy(plan);without.pop('original_price_notices')
    with pytest.raises(AnnotatedDeliveryError,match='UNHANDLED_STRICT_RULE'):
        apply_annotation_plan(source,rows,without,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,master_quality=quality)
    final,report=apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,master_quality=quality)
    altered=deepcopy(final);altered[0]['折后价']=5
    with pytest.raises(AnnotatedDeliveryError):verify_annotation_delivery(source,rows,altered,report)
    forged=deepcopy(report);forged['master_quality_audit']['issues'].append('INVALID_PRICE:1001')
    with pytest.raises(AnnotatedDeliveryError):verify_annotation_delivery(source,rows,final,forged)


def test_masked_current_original_uses_retained_value_only_as_disclosed_evidence():
    source,rows,_,_,_,quality=price_case();rec=source.records[0]
    rec['last_valid_original_price']=rec['original_price'];rec['original_price']=None
    notes=original_price_notices(source,rows,quality)
    assert notes[0]['source_original_price']==1.99 and notes[0]['effective_original_price'] is None
    assert rec['original_price'] is None and '不代表当前有效原价' in notes[0]['note']
    rec['current_price']=None
    with pytest.raises(AnnotatedDeliveryError):original_price_notices(source,rows,quality)


def test_retained_price_changed_after_plan_is_rejected_even_if_masked_value_is_unchanged():
    source,rows,plan,repair,strict,quality=price_case();rec=source.records[0]
    rec['last_valid_original_price']=rec['original_price'];rec['original_price']=None
    plan['source_hash']=canonical_source_hash(source.records)
    plan['original_price_notices']=original_price_notices(source,rows,quality)
    rec['last_valid_original_price']=1.5
    with pytest.raises(AnnotatedDeliveryError,match='PRICE_PLAN_CHANGED'):
        apply_annotation_plan(source,rows,plan,source_hash=canonical_source_hash(source.records),repair_audit=repair,strict_audit=strict,master_quality=quality)
