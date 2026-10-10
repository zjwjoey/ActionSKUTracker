"""CI_SAFE: preserve correct specific product nouns with own-source evidence."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS = json.loads((Path(__file__).parent / 'fixtures/historical_existing_description_aliases_20261010.json').read_text('utf8'))

def check(source, target, name=''):
    facts = SourceFacts(sku='1001', desc_es=source, name_es=name)
    return guard_translation(facts, {'description':target}, ('description',), semantic_facts=parse_semantic_facts(facts))

@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['sku'])
def test_real_existing_chinese_is_retained(row):
    assert row['source_evidence']['selected']['text'] == row['source']
    assert len(row['source_evidence']['selected']['file_hash']) == 64
    assert row['formal_approval'] is False and row['before_module_guard']['status'] == 'FAIL'
    assert check(row['source'], row['target'])['status'] == 'PASS'

@pytest.mark.parametrize('source,target', [('Iluminación de hilo de cobre','铜线灯串'), ('Paño para secar','擦干布'), ('Paño de pulir','抛光布')])
def test_specific_same_field_product_alias(source, target):
    assert check(source, target)['status'] == 'PASS'

@pytest.mark.parametrize('source,target', [('Iluminación','灯串'), ('Paño','抛光布'), ('Paño','擦干布')])
def test_generic_noun_does_not_gain_specific_sense(source, target):
    assert check(source, target)['status'] == 'FAIL'

@pytest.mark.parametrize('source,target,other', [('Iluminación','灯串','Iluminación de hilo de cobre'), ('Paño','抛光布','Paño de pulir'), ('Paño','擦干布','Paño para secar')])
def test_other_field_cannot_supply_specific_sense(source, target, other):
    assert check(source, target, other)['status'] == 'FAIL'

def test_alias_does_not_waive_quantity_or_led():
    source = 'Iluminación de hilo de cobre con 200 luces LED'
    assert check(source, '铜线灯串')['status'] == 'FAIL'
    assert check(source, '199个LED铜线灯串')['status'] == 'FAIL'
