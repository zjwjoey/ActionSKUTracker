"""CI_SAFE: real numeric wash phrases and malformed Chinese units."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS=json.loads((Path(__file__).parent/'fixtures/historical_spec24_usage_units_20261010.json').read_text('utf8'))

def check(source,target,field='spec',**other):
    key={'spec':'spec_es','description':'desc_es','name':'name_es'}[field]
    facts=SourceFacts(sku='1001',**{key:source,**other})
    return guard_translation(facts,{field:target},(field,),semantic_facts=parse_semantic_facts(facts))

@pytest.mark.parametrize('row',[r for r in ROWS if r['group_index'] in {170,184,197,201,206,232}],ids=lambda r:r['sku'])
def test_real_previously_passing_missing_use_or_plural_suffix(row):
    assert row['before_qa']['status']=='PASS'
    assert row['source_evidence']['selected']['text']==row['source']
    assert len(row['source_evidence']['selected']['file_hash'])==64
    assert check(row['source'],row['before'])['status']=='FAIL'
    target='800W' if row['group_index']==232 else row['source'].split()[0]+'次洗涤'
    assert check(row['source'],target)['status']=='PASS'

@pytest.mark.parametrize('target',['33次洗涤','可洗衣33次','33次清洗','可水洗33次'])
def test_wash_usage_aliases(target):
    assert check('33 lavados',target)['status']=='PASS'

def test_wash_alias_does_not_waive_quantity():
    assert check('33 lavados','洗涤')['status']=='FAIL'
    assert check('33 lavados','34次洗涤')['status']=='FAIL'

def test_wash_use_is_not_borrowed_from_another_field():
    assert check('33 unidades','33件',desc_es='33 lavados')['status']=='PASS'
    assert check('33 lavados','33次',name_es='Detergente')['status']=='FAIL'

def test_washed_adjective_or_partial_token_has_no_count_fact():
    for text in ('Vaqueros lavados','Modelo X33 lavados','prelavados'):
        facts=SourceFacts(sku='1001',spec_es=text)
        assert not any(f.evidence=='source_bound_wash_count' for f in parse_semantic_facts(facts))

@pytest.mark.parametrize('source,target',[('800 vatios','800瓦s'),('50 gramos','50克s'),('3 unidades','3件es'),('10 centímetros','10厘米s')])
def test_numeric_chinese_unit_with_latin_plural_is_blocked(source,target):
    result=check(source,target)
    assert result['status']=='FAIL'
    assert any(r['rule_id']=='MALFORMED_TRANSLATED_UNIT' for r in result['findings'])

def test_separate_model_label_is_not_plural_suffix():
    result=check('800 vatios, modo S','800瓦，S模式')
    assert not any(r['rule_id']=='MALFORMED_TRANSLATED_UNIT' for r in result['findings'])

def test_wash_usage_also_protected_in_description():
    assert check('Para 33 lavados','适用于33次洗涤','description')['status']=='PASS'
    assert check('Para 33 lavados','33次','description')['status']=='FAIL'

def test_uncertain_shade_fixtures_have_no_invented_gold_translation():
    uncertain=[r for r in ROWS if r['group_index'] in {186,238}]
    assert len(uncertain)==2
    assert all(r['formal_approval'] is False and 'expected_translation' not in r for r in uncertain)
