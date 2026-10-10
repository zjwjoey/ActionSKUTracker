"""CI_SAFE: source-bound unit, range, interface and illumination regression."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation, _numbers, _units
from action_tracker.localization.semantic import parse_semantic_facts

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_trusted_source_qa_20261010.json').read_text('utf8'))

def check(source,target,field='description',**context):
    key={'description':'desc_es','details':'details_es','spec':'spec_es'}[field]
    facts=SourceFacts(sku='1001',**{key:source,**context})
    semantic=parse_semantic_facts(facts,dictionaries={'terms':[{'term_es':'iluminación','term_zh':'照明灯','term_type':'PRODUCT_TYPE'}]})
    return guard_translation(facts,{field:target},(field,),semantic_facts=semantic)

@pytest.mark.parametrize('row',ROWS[:5],ids=lambda r:r['sku'])
def test_real_source_equivalent_rendering(row):
    assert row['source_evidence']['selected']['text']==row['source']
    assert len(row['source_evidence']['selected']['file_hash'])==64
    assert check(row['source'],row['reviewed_value'],row['field'])['status']=='PASS'

def test_area_exponent_does_not_drop_area_guard():
    assert _numbers('2,5 m2')==_numbers('2.5平方米')
    assert _units('2,5 m2')==_units('2.5平方米')
    assert check('Área 2,5 m2','面积2.5米')['status']=='FAIL'
    assert check('Área 2,5 m2','面积2.6平方米')['status']=='FAIL'
    assert _numbers('Modelo M2 2 unidades')=={'2':2}

def test_integer_range_list_keeps_decimals_and_missing_sizes():
    assert _numbers('28-29,30-31')==_numbers('28-29, 30-31')
    assert _numbers('28-29,5')=={'28':1,'29.5':1}
    assert check('Talla: 28-29,30-31','尺码:28-29','details')['status']=='FAIL'

def test_iphone_suffix_cannot_allow_unrelated_prose():
    assert check('Apto para iPhone X/XS/11 pro','适用于iPhone X/XS/11 pro','spec')['status']=='PASS'
    assert check('Apto para iPhone X/XS/11 pro','适用于iPhone X/XS/11 pro para','spec')['status']=='FAIL'
    assert check('Apto para uso pro','适用于使用pro','spec')['status']=='FAIL'

def test_light_usage_qualifier_cannot_be_borrowed_or_omitted():
    assert check('Iluminación','氛围照明',spec_es='Iluminación de ambiente')['status']=='FAIL'
    assert check('Iluminación multicolor RGB','多色RGB')['status']=='FAIL'

def test_source_brand_phrase_does_not_allow_ordinary_words():
    assert check('Max & More ofrece pinceles','Max & More提供刷子')['status']=='PASS'
    assert check('Ofrece pinceles','Max & More提供刷子')['status']=='FAIL'
    assert check('Max & More ofrece pinceles','Max & More ofrece刷子')['status']=='FAIL'
