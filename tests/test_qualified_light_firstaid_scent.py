"""CI_SAFE: own-field polysemy; no database, network or production changes."""
import pytest
from dataclasses import replace
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation
from action_tracker.services.hashing import localization_field_source_hash

def commercial(source, **changes):
    return replace(SemanticFact('BRAND','BIG Selection','BIG Selection',
        source_field='name_es',evidence='CODEX:OWNER_APPROVED_COMMERCIAL_PRODUCT_LINE:manufacturer',
        fact_source_hash=localization_field_source_hash(source.as_record(),'name'),
        coverage_status='VERIFIED',coverage_reason='COMMERCIAL_PRODUCT_LINE: manufacturer name'),**changes)

def test_current_complete_commercial_line_is_not_an_invented_technical_model():
    source=SourceFacts.from_record({'name_es':'Caramelos multipack MAOAM BIG Selection'})
    assert guard_translation(source,{'name':'多包装综合糖果'},('name',),semantic_facts=(commercial(source),))['overall_ready']

@pytest.mark.parametrize('changes',[{'fact_source_hash':'stale'},{'source_field':'desc_es'},
    {'coverage_status':'REVIEW_REQUIRED'},{'evidence':''}])
def test_commercial_line_requires_current_own_field_review(changes):
    source=SourceFacts.from_record({'name_es':'Caramelos multipack MAOAM BIG Selection'})
    assert not guard_translation(source,{'name':'多包装综合糖果'},('name',),semantic_facts=(commercial(source,**changes),))['overall_ready']

@pytest.mark.parametrize('text',['Caramelos BIG Selection BIG','Caramelos BIG Selection-X5'])
def test_commercial_line_does_not_waive_separate_identifier_or_compound_model(text):
    source=SourceFacts.from_record({'name_es':text})
    assert not guard_translation(source,{'name':'糖果'},('name',),semantic_facts=(commercial(source),))['overall_ready']

def test_explicit_big_model_overrides_commercial_proof():
    source=SourceFacts.from_record({'name_es':'Caramelos BIG Selection'})
    model=SemanticFact('MODEL','BIG','BIG',source_field='name_es',evidence='explicit model',fact_source_hash=localization_field_source_hash(source.as_record(),'name'))
    assert not guard_translation(source,{'name':'糖果'},('name',),semantic_facts=(commercial(source),model))['overall_ready']

def check(text,target,term,canonical,field='description',**context):
    key={'description':'desc_es','details':'details_es','name':'name_es'}[field]
    source=SourceFacts.from_record({**context,key:text})
    fact=SemanticFact('MATERIAL' if term=='madera' else 'PRODUCT_TYPE',term,canonical,source_field=key)
    return guard_translation(source,{field:target},(field,),semantic_facts=(fact,))['overall_ready']

@pytest.mark.parametrize('target',['LED灯效支持6种模式','LED灯光具有6个档位','LED照明有6档'])
def test_qualified_led_light_is_preserved_without_inventing_a_lamp_object(target):
    assert check('Iluminación led con 6 niveles de luz diferentes',target,'iluminación','照明灯')

@pytest.mark.parametrize('target',['6种模式','LED灯效有5档','不支持LED灯效，有6档','LED灯效有6档USB-C'])
def test_lighting_omission_number_negation_and_added_tech_remain_blocked(target):
    assert not check('Iluminación led con 6 niveles de luz diferentes',target,'iluminación','照明灯')

def test_plain_or_separate_unqualified_light_keeps_existing_guard():
    assert not check('Iluminación para jardín','花园灯饰','iluminación','照明灯')
    assert not check('Iluminación LED e iluminación para jardín','LED灯效','iluminación','照明灯')
    assert not check('Iluminación','带灯','iluminación','照明灯',name_es='Figura con iluminación')

def test_own_title_with_light_names_a_lit_figure():
    assert check('Figura de pan de jengibre con iluminación','带灯姜饼人摆件','iluminación','照明灯',field='name')
    assert not check('Figura de pan de jengibre con iluminación','无灯姜饼人摆件','iluminación','照明灯',field='name')

@pytest.mark.parametrize('value,zh',[('No','否'),('Sí','是')])
def test_firstaid_complete_attribute_keeps_actual_boolean(value,zh):
    source='Incluye paño de respiración: '+value
    assert check(source,'含人工呼吸面巾：'+zh,'paño','清洁布',field='details')
    assert not check(source,'含人工呼吸面巾：'+('是' if zh=='否' else '否'),'paño','清洁布',field='details')

@pytest.mark.parametrize('text',[
    'Paño de respiración: No', 'Incluye paño: No',
    'Incluye paño de respiración: No; Material: Paño',
    'Incluye paño de respiración: No; Uso: paño de limpieza',
])
def test_incomplete_or_separate_cloth_does_not_gain_firstaid_alias(text):
    assert not check(text,'含人工呼吸面巾：否','paño','清洁布',field='details')

def test_firstaid_word_cannot_be_substituted_for_cleaning_cloth():
    assert not check('Paño de limpieza','人工呼吸面巾','paño','清洁布')
    assert not check('Incluye paño de respiración: No','含清洁布：否','paño','清洁布',field='details')

@pytest.mark.parametrize('text',['Olor: Canela, Madera','Olor Canela, Madera'])
def test_complete_scent_key_does_not_assert_wood_composition(text):
    assert check(text,'气味：肉桂、木香','madera','木质',field='details')

@pytest.mark.parametrize('text',[
    'Material: Madera', 'Madera', 'Olor: Sin madera',
    'Material: Madera; Olor: Canela, Madera',
])
def test_wood_material_negated_and_separate_occurrences_keep_guard(text):
    assert not check(text,'气味：肉桂、木香','madera','木质',field='details')
