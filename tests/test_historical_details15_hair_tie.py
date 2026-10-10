"""CI_SAFE: hair ties do not establish an unprovided rubber composition."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

ROWS = json.loads((Path(__file__).parent / 'fixtures/historical_details15_hair_tie_20261010.json').read_text('utf8'))

def facts(source, description='', dictionaries=None):
    return parse_semantic_facts(SourceFacts(sku='1001', details_es=source, desc_es=description), dictionaries=dictionaries)

def check(source, target, description=''):
    source_facts = SourceFacts(sku='1001', details_es=source, desc_es=description)
    return guard_translation(source_facts, {'details':target}, ('details',), semantic_facts=parse_semantic_facts(source_facts))

@pytest.mark.parametrize('row', ROWS, ids=lambda r:r['sku'])
def test_real_own_source_hair_tie_without_guessed_rubber(row):
    assert row['source_evidence']['selected']['text'] == row['source']
    assert len(row['source_evidence']['selected']['file_hash']) == 64
    assert row['before_module_guard']['status'] == 'FAIL'
    assert row['formal_approval'] is False
    assert check(row['source'], row['target'])['status'] == 'PASS'

@pytest.mark.parametrize('term', ['Goma para cola de caballo', 'Gomas para cola de caballo', 'goma para la cola de caballo'])
def test_complete_phrase_is_an_accessory_not_composition(term):
    result = facts(term)
    assert any(f.value == '发圈' and f.source_field == 'details_es' for f in result)
    assert not any(f.value in {'橡胶', '橡皮筋'} for f in result)
    assert check(term, '发圈')['status'] == 'PASS'
    assert check(term, '橡胶')['status'] == 'FAIL'

def test_separate_explicit_rubber_material_survives():
    source = 'Goma para cola de caballo; Material: Goma'
    assert any(f.value == '橡胶' for f in facts(source))
    assert check(source, '橡胶发圈')['status'] == 'PASS'
    assert check(source, '发圈')['status'] == 'FAIL'

def test_separate_rubber_bands_survive():
    source = 'Gomas para cola de caballo y gomas para sujetar'
    assert any(f.value == '发圈' for f in facts(source))
    assert any(f.value == '橡皮筋' for f in facts(source))

def test_another_field_cannot_disambiguate_generic_goma():
    assert any(f.value == '橡胶' and f.source_field == 'details_es' for f in facts('Goma', 'Goma para cola de caballo'))
    assert check('Goma', '发圈', 'Goma para cola de caballo')['status'] == 'FAIL'

def test_dictionary_cannot_reintroduce_consumed_generic_material():
    result = facts('Goma para cola de caballo', dictionaries={'product_types':{'goma':'橡胶'}})
    assert any(f.value == '发圈' for f in result)
    assert not any(f.value == '橡胶' for f in result)

@pytest.mark.parametrize('term', ['Goma para cola', 'Goma espuma', 'Gomas para sujetar'])
def test_incomplete_or_other_phrase_does_not_become_hair_tie(term):
    assert not any(f.value == '发圈' for f in facts(term))
