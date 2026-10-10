"""CI_SAFE: source-proven four sockets; keep the existing correct Chinese."""
import json
from pathlib import Path
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation

ROW = json.loads((Path(__file__).parent / 'fixtures/historical_quadruple_outlet_20261010.json').read_text('utf8'))

def check(source, target, description=''):
    return guard_translation(SourceFacts(sku='1001', name_es=source, desc_es=description), {'name':target}, ('name',))

def test_real_current_correct_name_remains_reusable():
    assert ROW['source_evidence']['selected']['text'] == ROW['source']
    assert len(ROW['source_evidence']['selected']['file_hash']) == 64
    assert ROW['formal_approval'] is False
    assert ROW['qa']['status'] == 'FAIL'
    assert check(ROW['source'], ROW['target'])['status'] == 'PASS'

@pytest.mark.parametrize('target', ['3插孔插线板', '5插孔插线板', '插线板'])
def test_wrong_or_missing_quantity_stays_blocked(target):
    assert check(ROW['source'], target)['status'] == 'FAIL'

@pytest.mark.parametrize('source', ['Regleta de enchufes', 'Cuádruple', 'Regleta de enchufes cuádruplex', 'Regleta de enchufes cuádrupleMax'])
def test_partial_or_model_fragment_does_not_supply_four_sockets(source):
    assert check(source, '4插孔插线板')['status'] == 'FAIL'

def test_another_source_field_cannot_supply_socket_count():
    assert check('Regleta de enchufes', '4插孔插线板', ROW['source'])['status'] == 'FAIL'

def test_repeated_source_quantities_cannot_be_collapsed():
    assert check(ROW['source'] + '; ' + ROW['source'], ROW['target'])['status'] == 'FAIL'

def test_unrelated_quantity_remains_protected():
    assert check(ROW['source'] + ' 2 m', ROW['target'])['status'] == 'FAIL'
