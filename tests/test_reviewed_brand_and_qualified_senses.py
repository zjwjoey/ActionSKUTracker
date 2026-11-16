from dataclasses import replace
import pytest
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation
from action_tracker.services.hashing import localization_field_source_hash

def brand(source, span='GS27', **changes):
    value = SemanticFact('BRAND', span, span, source_field='name_es',
        evidence='CODEX:CURRENT_BRAND_DICTIONARY_HUMAN_REVIEWED:test',
        fact_source_hash=localization_field_source_hash(source.as_record(), 'name'),
        coverage_status='VERIFIED')
    return replace(value, **changes)

def check(source, target, facts=(), field='name'):
    return guard_translation(source, {field: target}, (field,), semantic_facts=facts)

@pytest.mark.parametrize('span', ['GS27', 'K2', '3M'])
def test_current_exact_reviewed_title_brand_is_not_a_model(span):
    source=SourceFacts.from_record({'name_es':'Limpiador '+span})
    assert check(source,'清洁剂',(brand(source,span),))['overall_ready']

@pytest.mark.parametrize('change', [dict(fact_source_hash='stale'),dict(source_field='desc_es'),
    dict(coverage_status='REVIEW_REQUIRED'),dict(evidence='')])
def test_missing_stale_or_wrong_field_proof_stays_blocked(change):
    source=SourceFacts.from_record({'name_es':'Limpiador GS27'})
    assert not check(source,'清洁剂',(brand(source,**change),))['overall_ready']

def test_no_brand_proof_is_not_permission_to_drop_identifier():
    source=SourceFacts.from_record({'name_es':'Limpiador GS27'})
    assert not check(source,'清洁剂')['overall_ready']

@pytest.mark.parametrize('code',['A4','USB-C','XL','LED'])
def test_technical_identifiers_cannot_be_declared_omittable_brands(code):
    source=SourceFacts.from_record({'name_es':'Producto '+code})
    assert not check(source,'商品',(brand(source,code),))['overall_ready']

@pytest.mark.parametrize('text',['Limpiador GS27-X5','Limpiador GS27 GS27','Limpiador GS27 500 ml'])
def test_compound_independent_occurrence_and_quantity_protection(text):
    source=SourceFacts.from_record({'name_es':text})
    assert not check(source,'清洁剂',(brand(source),))['overall_ready']

def test_explicit_model_role_wins_over_a_conflicting_brand_claim():
    source=SourceFacts.from_record({'name_es':'Limpiador GS27'})
    model=SemanticFact('MODEL','GS27','GS27',source_field='name_es',
        fact_source_hash=localization_field_source_hash(source.as_record(),'name'),evidence='review')
    assert not check(source,'清洁剂',(brand(source),model))['overall_ready']

def test_body_brand_still_must_be_preserved():
    source=SourceFacts.from_record({'name_es':'Limpiador GS27','desc_es':'Limpiador GS27'})
    assert not check(source,'清洁剂',(brand(source),),field='description')['overall_ready']

DETAILS='Apto para tipo de aparato: Lavadora; Apto para tipo de colada a color: Colada a color; Contenido: 3.120 ml'
def laundry(details=DETAILS, name='Detergente Asevi Color', **kw):
    return SourceFacts.from_record({'name_es':name,'details_es':details,**kw})
def detergent_fact():
    return SemanticFact('PRODUCT_TYPE','detergente','洗洁精',source_field='name_es')

def test_structured_laundry_context_disambiguates_the_bare_title_only():
    source=laundry()
    assert check(source,'彩色衣物洗衣液',(detergent_fact(),))['overall_ready']

@pytest.mark.parametrize('target',['洗洁精','洗衣粉','不含洗衣液','洗衣液500毫升'])
def test_wrong_form_negation_and_borrowed_quantity_stay_blocked(target):
    assert not check(laundry(),target,(detergent_fact(),))['overall_ready']

@pytest.mark.parametrize('details',[
    DETAILS.replace('Lavadora','Lavavajillas'),DETAILS.replace('3.120 ml','3.120 g'),
    DETAILS.replace('Colada a color','Desconocida'),DETAILS.split(';')[0]])
def test_incomplete_or_conflicting_context_does_not_define_liquid_laundry(details):
    assert not check(laundry(details),'洗衣液',(detergent_fact(),))['overall_ready']

@pytest.mark.parametrize('name',['Detergente en polvo','Detergente gel','Detergente cápsulas','Recipiente para detergente'])
def test_powder_gel_capsules_and_accessories_remain_distinct(name):
    assert not check(laundry(name=name),'洗衣液',(detergent_fact(),))['overall_ready']

def test_laundry_context_cannot_rewrite_a_description_noun():
    source=laundry(desc_es='Detergente')
    fact=replace(detergent_fact(),source_field='desc_es')
    assert not check(source,'洗衣液',(fact,),field='description')['overall_ready']

def light(text='Con iluminación blanca o de color'):
    return SourceFacts.from_record({'desc_es':text})
LIGHT_FACT=SemanticFact('PRODUCT_TYPE','iluminación','照明灯',source_field='desc_es')
def test_complete_lighting_colour_phrase_is_represented():
    assert check(light(),'配有白色或彩色照明',(LIGHT_FACT,),field='description')['overall_ready']

@pytest.mark.parametrize('target',['照明','没有白色或彩色照明','配白色照明'])
def test_colour_omission_and_negation_not_accepted_by_new_phrase_alias(target):
    assert not check(light(),target,(LIGHT_FACT,),field='description')['overall_ready']

def test_phrase_does_not_waive_a_second_ordinary_lamp_mention():
    assert not check(light('Con iluminación blanca o de color; iluminación'),
        '配有白色或彩色照明',(LIGHT_FACT,),field='description')['overall_ready']
