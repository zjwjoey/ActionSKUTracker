"""Own-field boolean facts from archived SKUs; fixtures are not approvals."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

CASES = json.loads((Path(__file__).parent / 'fixtures/historical_details11_boolean_scope_20261010.json').read_text('utf8'))

def check(source, target, description=''):
    return guard_translation(SourceFacts(sku='1001', details_es=source, desc_es=description), {'details': target}, ('details',))

def rules(result):
    return {f['rule_id'] for f in result['findings']}

def test_real_fragrance_alias_preserves_original_false_value():
    row = CASES[0]
    assert row['source_evidence']['selected']['text'] == row['source']
    assert len(row['source_evidence']['selected']['file_hash']) == 64
    assert row['formal_approval'] is False
    assert check(row['source'], row['target'])['status'] == 'PASS'

@pytest.mark.parametrize('row', CASES[1:], ids=lambda r: r['sku'])
def test_real_soap_negation_is_not_reversed(row):
    assert row['source_evidence']['selected']['text'] == row['source']
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in rules(check(row['source'], row['candidate']))
    # Invalid Sustancia is intentionally unresolved: no guessed gold translation.
    assert row['expected_behavior'] == 'SOURCE_REVIEW_REQUIRED'
    assert 'target' not in row

@pytest.mark.parametrize('key', ['无香型', '是否无香', '是否含香精', '是否含香料', '是否有香味'])
def test_fragrance_aliases_check_presence_not_label_boolean(key):
    negative = '无' in key
    good_value, bad_value = ('否', '是') if negative else ('是', '否')
    source = "{'Sin perfume': 'No'}"
    assert check(source, str({key: good_value}))['status'] == 'PASS'
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in rules(check(source, str({key: bad_value})))

@pytest.mark.parametrize('source_key', ['Sin jabón', 'Sin jabon', 'Libre de jabón', 'No contiene jabón'])
def test_soap_source_negative_labels_retain_polarity(source_key):
    source = str({source_key: 'No'})
    assert check(source, "{'是否含皂基': '是'}")['status'] == 'PASS'
    assert check(source, "{'是否无皂': '否'}")['status'] == 'PASS'
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in rules(check(source, "{'是否含皂': '否'}"))

def test_fragrance_attribute_must_not_disappear():
    assert 'DETAIL_BOOLEAN_FIELD_MISSING' in rules(check("{'Sin perfume': 'No'}", "{'商品编号': '1001'}"))

def test_duplicate_fragrance_booleans_remain_distinct():
    source = "{'Sin perfume': 'No', 'Contiene perfume': 'Sí'}"
    assert check(source, "{'无香型': '否', '是否含香精': '是'}")['status'] == 'PASS'
    assert 'DETAIL_BOOLEAN_FIELD_MISSING' in rules(check(source, "{'无香型': '否'}"))

def test_soap_in_another_field_cannot_explain_wrong_details_truth():
    result = check("{'Sin jabón': 'No'}", "{'是否含皂': '否'}", 'Sin jabón')
    assert 'DETAIL_BOOLEAN_POLARITY_CHANGED' in rules(result)

def test_existing_no_iron_rule_survives_fragrance_extension():
    source = "{'Instrucciones de planchado': 'Sin planchado'}"
    assert 'CARE_INSTRUCTION_CHANGED' in rules(check(source, "{'熨烫说明': '无需熨烫'}"))
    assert check(source, "{'熨烫说明': '不可熨烫'}")['status'] == 'PASS'
