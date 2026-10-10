"""Generic sugar and lactose are separate source-bound boolean attributes."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

ROW = json.loads((Path(__file__).parent / 'fixtures/historical_details12_sugar_lactose_scope_20261010.json').read_text('utf8'))

def check(source, target):
    return guard_translation(SourceFacts(sku='1001', details_es=source), {'details': target}, ('details',))

def test_real_same_field_sugar_and_lactose_preserve_both_false_values():
    assert ROW['source_evidence']['selected']['text'] == ROW['source']
    assert len(ROW['source_evidence']['selected']['file_hash']) == 64
    assert ROW['formal_approval'] is False
    assert ROW['before_module_guard']['status'] == 'FAIL'
    assert check(ROW['source'], ROW['target'])['status'] == 'PASS'

@pytest.mark.parametrize('key', ['无糖', '无乳糖'])
def test_either_reversed_boolean_remains_blocked(key):
    result = check("{'Sin azúcar': 'No', 'Sin lactosa': 'No'}", str({'无糖': '否', '无乳糖': '否', key: '是'}))
    assert any(f['rule_id'] == 'DETAIL_BOOLEAN_POLARITY_CHANGED' for f in result['findings'])

@pytest.mark.parametrize('key', ['无糖', '无乳糖'])
def test_either_missing_attribute_remains_blocked(key):
    result = check("{'Sin azúcar': 'No', 'Sin lactosa': 'No'}", str({key: '否'}))
    assert any(f['rule_id'] == 'DETAIL_BOOLEAN_FIELD_MISSING' for f in result['findings'])

def test_lactose_alone_cannot_satisfy_generic_sugar_attribute():
    result = check("{'Sin azúcar': 'Sí'}", "{'无乳糖': '是'}")
    assert any(f['rule_id'] == 'DETAIL_BOOLEAN_FIELD_MISSING' for f in result['findings'])
