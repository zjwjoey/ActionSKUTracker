"""CI_SAFE: real archival evidence plus selected charging-speed counterexamples."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

ROW = json.loads((Path(__file__).parent / 'fixtures/historical_details14_charging_speed_20261010.json').read_text('utf8'))

def check(source, target, description=''):
    return guard_translation(SourceFacts(sku='1001', details_es=source, desc_es=description), {'details': target}, ('details',))

def test_real_candidate_both_options_is_blocked_but_selected_fast_passes():
    assert ROW['source_evidence']['selected']['text'] == ROW['source']
    assert len(ROW['source_evidence']['selected']['file_hash']) == 64
    assert ROW['formal_approval'] is False
    assert any(f['rule_id'] == 'DETAIL_CHARGING_SPEED_CHANGED' for f in check(ROW['source'], ROW['candidate'])['findings'])
    assert check(ROW['source'], ROW['target'])['status'] == 'PASS'

@pytest.mark.parametrize('value, good, bad', [('Rápido', '快速', '慢速'), ('Rapido', '快充', '慢充'), ('Lento', '慢速', '快速')])
def test_known_own_source_selection(value, good, bad):
    source = str({'Cargador rápido / lento': value})
    assert check(source, str({'充电速度': good}))['status'] == 'PASS'
    assert check(source, str({'充电速度': bad}))['status'] == 'FAIL'
    assert check(source, str({'充电速度': '快充/慢充'}))['status'] == 'FAIL'

def test_alternative_labels_are_not_selected_values():
    assert check("{'Cargador rápido / lento':'Rápido'}", "{'快充/慢充':'快速'}")['status'] == 'PASS'

@pytest.mark.parametrize('value', ['不支持快充', '非快速', '快速或慢速', '快速充电，支持PD'])
def test_negated_ambiguous_or_extra_claim_is_not_a_fast_selection(value):
    assert check("{'Cargador rápido / lento':'Rápido'}", str({'充电速度':value}))['status'] == 'FAIL'

@pytest.mark.parametrize('target', ["{'颜色':'黑色'}", "{'充电速度':'是'}", "{'充电速度':'快速','充电模式':'慢速'}"])
def test_missing_uninterpretable_or_duplicate_selection_is_blocked(target):
    assert check("{'Cargador rápido / lento':'Rápido'}", target)['status'] == 'FAIL'

def test_other_field_cannot_supply_the_selection():
    assert check("{'Cargador rápido / lento':'Lento'}", "{'充电速度':'快速'}", 'Cargador rápido')['status'] == 'FAIL'

def test_rule_does_not_match_another_source_attribute():
    result = check("{'Cargador incluido':'Sí'}", "{'是否含充电器':'是'}")
    assert not any(f['rule_id'].startswith('DETAIL_CHARGING_SPEED') for f in result['findings'])

def test_only_requested_details_are_checked():
    facts = SourceFacts(sku='1001', details_es="{'Cargador rápido / lento':'Rápido'}", desc_es='Producto')
    assert guard_translation(facts, {'description':'商品'}, ('description',))['status'] == 'PASS'
