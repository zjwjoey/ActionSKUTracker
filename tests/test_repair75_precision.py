"""CI_SAFE: scoped measured-token, source-brand and attribute-role regression."""
import pytest
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation

def qa(es,zh,field='description',term=None,canonical='',**ctx):
    key={'description':'desc_es','details':'details_es','spec':'spec_es'}[field]
    facts=(SemanticFact('PRODUCT_TYPE',term,canonical,source_field=key),) if term else ()
    return guard_translation(SourceFacts.from_record({key:es,**ctx}),{field:zh},(field,),semantic_facts=facts)

@pytest.mark.parametrize('es,zh', [('20.000 mAh','20000 mAh'),('1,5 V','1.5 V')])
def test_measured_notation_equivalent(es,zh):
    assert qa(es,zh,'spec')['overall_ready']

@pytest.mark.parametrize('es,zh',[('20.000 mAh','20 mAh'),('20.000 mAh','20000 Ah'),('20.000 mAh','20001 mAh'),('1,5 V','15 V'),('1,5 V','1.5 W'),('1,5 V','1.6 V')])
def test_wrong_measured_value_or_unit_blocked(es,zh):
    assert not qa(es,zh,'spec')['overall_ready']

def test_model_not_numeric_measurement():
    assert not qa('Modelo X20.000','型号X20000','spec')['overall_ready']

def test_same_field_complete_brand_only():
    assert qa('Caramelo en spray Dr. Candy','Dr. Candy喷雾糖')['overall_ready']
    assert not qa('Caramelo en spray','Dr. Candy喷雾糖')['overall_ready']
    assert not qa('Caramelo en spray Dr. Candy','Dr. Candy喷雾糖 incluye sabores')['overall_ready']

def test_lighting_function_and_installation_key():
    es='Función: Iluminación ambiente; Tipo de instalación de iluminación: Colgado'
    assert qa(es,'功能：环境照明；照明安装类型：悬挂','details','iluminación','照明灯')['overall_ready']
    assert not qa(es,'功能：不含环境照明；照明安装类型：悬挂','details','iluminación','照明灯')['overall_ready']
    assert not qa(es+'; Otra iluminación: Lámpara','功能：环境照明；照明安装类型：悬挂；其他：灯','details','iluminación','照明灯')['overall_ready']

def test_ear_protection_noun_disambiguation():
    es='Estos auriculares con cancelación de ruido están fabricados con silicona.'
    args=dict(term='auriculares',canonical='耳机',name_es='Tapones con reducción de ruido')
    assert qa(es,'这些降噪耳塞由硅胶制成。',**args)['overall_ready']
    assert not qa(es,'这些降噪耳塞由硅胶制成。',term='auriculares',canonical='耳机',name_es='Auriculares Bluetooth')['overall_ready']
    assert not qa(es+' Incluyen micrófono.','这些降噪耳塞由硅胶制成。',**args)['overall_ready']
    assert not qa(es,'这些不是降噪耳塞，由硅胶制成。',**args)['overall_ready']
