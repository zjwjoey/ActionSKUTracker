"""CI_SAFE: frozen real sources plus negative controls, no business writes/API.

Approved proof objects below are synthetic test states, not claims of human Gold.
"""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from action_tracker.database.repository import _read_approved_name_reviews
from action_tracker.exporting.repair_report import audit_repaired_rows
from action_tracker.localization.contracts import SemanticFact, SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import (approved_name_review_facts,
    guard_translation, _semantic_aliases)
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.services.hashing import localization_field_source_hash
from scripts.run_current_localization_review import field_already_ready

CASES = json.loads((Path(__file__).parent/'fixtures/current102_qa_20261102.json').read_text(encoding='utf-8'))['cases']
NAMES = [x for x in CASES if x['review']['semantic_facts_additions']]


def approved_record(case):
    record = copy.deepcopy(case['record'])
    review = case['review']
    record['name_zh'] = review['target']
    actor = 'service:current-localization-review'
    record['zh_field_provenance']['name'] = dict(value=review['target'],source_hash=review['field_source_hash'],review_status='APPROVED',freshness_status='CURRENT',approved_by=actor)
    record['_approved_name_semantic_review'] = dict(target_text=review['target'],review_status='APPROVED',qa_status='PASS',canonical_qa_status='NOT_REQUIRED',approved_by=actor,review=copy.deepcopy(review))
    return record


@pytest.mark.parametrize('case', CASES, ids=lambda x:x['review']['sku']+'-'+x['review']['field'])
def test_real_source_bound_review_passes_full_guard(case):
    review=case['review'];source=SourceFacts.from_record(case['record'])
    facts=(*parse_semantic_facts(source),*(SemanticFact(**x) for x in review['semantic_facts_additions']))
    result=guard_translation(source,{review['field']:review['target']},(review['field'],),semantic_facts=facts)
    assert result['overall_ready'],result['findings']


@pytest.mark.parametrize('case', NAMES, ids=lambda x:x['review']['sku'])
def test_approved_brand_evidence_is_shared_with_planner_export_and_resume(case):
    record=approved_record(case);review=case['review'];facts=approved_name_review_facts(record)
    assert facts
    assert all(x in LocalizationEngine().resolve(record).semantic_facts for x in facts)
    audit=audit_repaired_rows([record],[{'编号':record['sku'],'标题':record['name_zh']}])
    assert not [x for x in audit['findings'] if x['field']=='name_zh'],audit['findings']
    assert field_already_ready(record,'name',review['target'],reviewed_facts=review['semantic_facts_additions'])
    record.pop('_approved_name_semantic_review')
    assert not approved_name_review_facts(record)
    assert not field_already_ready(record,'name',review['target'],reviewed_facts=review['semantic_facts_additions'])


@pytest.mark.parametrize('part,key,value',[
    ('record','sku','other'),('record','name_es','Different source TCX'),
    ('record','name_zh','不同中文'),('meta','value','不同中文'),
    ('meta','source_hash','changed'),('meta','freshness_status','STALE'),
    ('meta','review_status','PENDING'),('meta','approved_by','other'),
    ('proof','review_status','PENDING'),('proof','qa_status','FAIL'),
    ('proof','canonical_qa_status','FAIL'),('proof','approved_by',''),
    ('proof','target_text','different'),('review','field_source_hash','changed'),
    ('review','field','description'),('review','decision','REVIEW_REQUIRED'),
    ('review','review_model','QWEN'),('review','approval_evidence',''),
    ('review','approval_mode',''),('review','semantic_facts_additions',[{'not':'a fact'}]),
])
def test_changed_unapproved_or_malformed_evidence_never_exempts_tokens(part,key,value):
    record=approved_record(NAMES[0]);proof=record['_approved_name_semantic_review']
    target={'record':record,'meta':record['zh_field_provenance']['name'],'proof':proof,'review':proof['review']}[part]
    target[key]=value
    assert not approved_name_review_facts(record)


@pytest.mark.parametrize('key,value',[('semantic_type','QUANTITY'),('source_field','desc_es'),('fact_source_hash','changed'),('source_text','USB-C'),('evidence',''),('value','USB-C')])
def test_invalid_or_cross_field_brand_fact_is_not_reused(key,value):
    record=approved_record(NAMES[0]);record['_approved_name_semantic_review']['review']['semantic_facts_additions'][0][key]=value
    assert not approved_name_review_facts(record)


def test_one_brand_span_does_not_remove_an_identical_model_or_extra_occurrence():
    source=SourceFacts(sku='negative',name_es='Batidora TMC Home modelo TMC')
    fact=SemanticFact('BRAND','TMC Home','TMC Home',source_field='name_es')
    for target,ready in [('TMC搅拌机',True),('搅拌机',False),('TMC TMC TMC搅拌机',False)]:
        assert guard_translation(source,{'name':target},('name',),semantic_facts=(fact,))['overall_ready']==ready


@pytest.mark.parametrize('token',['USB-C','AA','AAA','LED','A4','XL'])
def test_technical_standards_batteries_and_sizes_cannot_be_declared_omittable_brands(token):
    source=SourceFacts(sku='negative',name_es='Producto '+token)
    fact=SemanticFact('BRAND',token,token,source_field='name_es')
    assert not guard_translation(source,{'name':'商品'},('name',),semantic_facts=(fact,))['overall_ready']


def test_name_brand_evidence_never_waives_body_brand_or_numbers():
    fact=SemanticFact('BRAND','TCX','TCX',source_field='name_es')
    source=SourceFacts(sku='negative',name_es='Cinta TCX 10 metros',desc_es='Producto TCX')
    assert not guard_translation(source,{'name':'胶带'},('name',),semantic_facts=(fact,))['overall_ready']
    assert not guard_translation(source,{'description':'产品'},('description',),semantic_facts=(fact,))['overall_ready']


def test_reviewed_brand_does_not_waive_the_turbo_fire_model():
    case=next(x for x in NAMES if x['review']['sku']=='3225710')
    record=approved_record(case);facts=approved_name_review_facts(record)
    result=guard_translation(SourceFacts.from_record(record),{'name':'软弹发射器'},('name',),semantic_facts=facts)
    assert not result['overall_ready']
    assert any(x['rule_id']=='MODEL_DROPPED' for x in result['findings'])


@pytest.mark.parametrize('source',[
    'Tabla de bambú','Material: Bambú','No son calcetines de bambú',
    'Caja para calcetines de bambú','Calcetines de bambú y caja de bambú',
    'Calcetines de bambú; sin bambú',
])
def test_sock_textile_alias_never_relaxes_solid_mixed_negated_or_accessory_bamboo(source):
    assert '竹纤维' not in _semantic_aliases('bambú',source,'竹制')


def test_wood_putty_is_an_intended_surface_not_wood_composition():
    assert '木器' in _semantic_aliases('madera','Masilla para madera TCX','木质')
    assert '木器' not in _semantic_aliases('madera','Material: Madera','木质')
    assert '木器' not in _semantic_aliases('madera','Masilla para madera con mango de madera','木质')


@pytest.mark.parametrize('value',['Válido','Válido, Loción','Válido, Lavado'])
def test_unknown_physical_state_remains_blocked(value):
    result=guard_translation(SourceFacts(sku='negative',details_es='Sustancia: '+value),{'details':'来源异常：官网字段Sustancia='+value},('details',))
    assert any(x['rule_id']=='SOURCE_DETAILS_TYPED_VALUE_INVALID' for x in result['findings'])


@pytest.mark.parametrize('field_name',['name','name_es'])
def test_repository_reads_only_current_approved_registry_revision_and_old_schema_is_safe(field_name):
    db=sqlite3.connect(':memory:')
    assert _read_approved_name_reviews(db)=={}
    db.executescript('''CREATE TABLE translation_units(source_version_id TEXT,current_revision_id TEXT,field_name TEXT);
        CREATE TABLE translation_source_versions(source_version_id TEXT,official_sku TEXT);
        CREATE TABLE translation_revisions(revision_id TEXT,target_text TEXT,review_status TEXT,qa_status TEXT,canonical_qa_status TEXT,approved_by TEXT,provenance_json TEXT);''')
    review=NAMES[0]['review'];sku=review['sku']
    db.execute('INSERT INTO translation_source_versions VALUES(?,?)',('source',sku))
    for revision,status in [('old','APPROVED'),('current','PENDING')]:
        db.execute('INSERT INTO translation_revisions VALUES(?,?,?,?,?,?,?)',(revision,review['target'],status,'PASS','NOT_REQUIRED','service:current-localization-review',json.dumps({'review':review})))
    db.execute('INSERT INTO translation_units VALUES(?,?,?)',('source','current',field_name))
    assert not _read_approved_name_reviews(db)
    db.execute("UPDATE translation_revisions SET review_status='APPROVED' WHERE revision_id='current'")
    proof=_read_approved_name_reviews(db)[sku];assert len(proof)==1
    record=approved_record(NAMES[0]);record['_approved_name_semantic_review']=proof
    assert approved_name_review_facts(record)
    db.close()
