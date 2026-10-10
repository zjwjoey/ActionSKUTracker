"""CI_SAFE: own-source neon lamp noun evidence."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROW=json.loads((Path(__file__).parent/'fixtures/historical_neon_description_20261010.json').read_text('utf8'))
def check(source,target,name=''):
    facts=SourceFacts(sku='1001',desc_es=source,name_es=name)
    return guard_translation(facts,{'description':target},('description',),semantic_facts=parse_semantic_facts(facts))
def test_real_neon_candidate_with_source_evidence():
    assert ROW['source_evidence']['selected']['text']==ROW['source']
    assert len(ROW['source_evidence']['selected']['file_hash'])==64
    assert ROW['before_module_guard']['status']=='FAIL' and ROW['formal_approval'] is False
    assert check(ROW['source'],ROW['target'])['status']=='PASS'

@pytest.mark.parametrize('source,target',[
    ('Lámpara de neón. Iluminación colorida.','霓虹灯发出彩色光。'),
    ('Lámpara de pared solar de neón. Iluminación colorida.','太阳能霓虹壁灯发出彩色光。'),
])
def test_own_complete_phrase(source,target):
    assert check(source,target)['status']=='PASS'

@pytest.mark.parametrize('source,target',[
    ('Iluminación colorida','霓虹壁灯'),
    ('Lámpara de pared. Iluminación colorida.','霓虹壁灯'),
    ('Lámpara de neón. Iluminación colorida.','霓虹壁灯'),
])
def test_generic_phrase_does_not_gain_specific_type(source,target):
    assert check(source,target)['status']=='FAIL'

@pytest.mark.parametrize('source,target,other',[
    ('Iluminación colorida','霓虹壁灯','Lámpara de pared solar de neón'),
])
def test_other_field_cannot_supply_qualifier(source,target,other):
    assert check(source,target,other)['status']=='FAIL'

def test_alias_cannot_drop_quantity_or_led():
    assert check('Lámpara de pared solar de neón con iluminación LED y 10 luces','霓虹壁灯')['status']=='FAIL'
