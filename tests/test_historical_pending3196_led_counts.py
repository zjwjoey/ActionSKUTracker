"""CI_SAFE: technical multiplicity is not a substring count of Spanish words."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import _casefold_token_count, guard_translation

ROW=json.loads((Path(__file__).parent/'fixtures'/'historical_pending3196_led_counts_20261011.json').read_text(encoding='utf-8'))

def check(target):
    record={'sku':ROW['sku'],**ROW['context']}
    return guard_translation(SourceFacts.from_record(record),{'description':target},('description',),semantic_facts=LocalizationEngine().resolve(record).semantic_facts)

def test_real_remote_control_word_is_not_a_third_led():
    assert ROW['evidence']['text']==ROW['source']
    assert len(ROW['evidence']['file_hash'])==64
    assert _casefold_token_count(ROW['source'],'LED')==2
    result=check(ROW['candidate'])
    assert result['status']=='PASS',result['findings']

def test_missing_actual_led_occurrence_is_still_blocked():
    result=check(ROW['candidate'].replace('LED','',1))
    assert any(f['rule_id']=='PROTECTED_TOKEN_MISSING' for f in result['findings'])

def test_added_actual_led_occurrence_is_still_blocked():
    result=check(ROW['candidate']+' LED灯')
    assert any(f['rule_id']=='PROTECTED_TOKEN_DUPLICATED' for f in result['findings'])

@pytest.mark.parametrize('word', ['teledirigido','OLED','LEDdriver','ÁLED','LEDÓ'])
def test_latin_word_or_different_technology_is_not_standalone_led(word):
    assert _casefold_token_count(word,'LED')==0

def test_chinese_adjoining_labels_and_case_are_supported():
    assert _casefold_token_count('LED灯、led灯、LED灯','LED')==3
    assert _casefold_token_count('USB-C接口','USB-C')==1
