"""Real pool-pump flow: unit exponents are not independent quantities."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import _numbers, _units, guard_translation

CASE = json.loads((Path(__file__).parent / 'fixtures' /
                   'historical_pending3196_cubic_units_20261011.json').read_text(encoding='utf-8'))


def test_source_bound_pool_pump_flow_and_voltage_remain_equivalent():
    assert CASE['evidence']['text'] == CASE['source']
    assert len(CASE['evidence']['file_hash']) == 64
    assert CASE['independent_semantic_status'] == 'PASS'
    result = guard_translation(SourceFacts.from_record({'sku': CASE['sku'], 'spec_es': CASE['source']}),
                               {'spec': CASE['target']}, ('spec',))
    assert result['status'] == 'PASS', result['findings']
    assert _numbers(CASE['source']) == _numbers(CASE['target'])
    assert _units(CASE['source']) == {'m³', 'm³/h'}


@pytest.mark.parametrize('target', [
    '2.3 平方米/小时 | 12 伏特',
    '2.3 米/小时 | 12 伏特',
    '2.3 立方米 | 12 伏特',
    '2.3 立方米/分钟 | 12 伏特',
    '23 立方米/小时 | 12 伏特',
    '2.3 立方米/小时 | 24 伏特',
])
def test_wrong_dimensions_rates_and_values_still_fail(target):
    result = guard_translation(SourceFacts.from_record({'sku': CASE['sku'], 'spec_es': CASE['source']}),
                               {'spec': target}, ('spec',))
    assert result['status'] == 'FAIL'


def test_cubic_exponent_normalization_preserves_identifier_digits():
    assert _numbers('Model12m3') == {'12': 1, '3': 1}
    assert _numbers('2,3 m3/hora') == {'2.3': 1}
    assert _units('12m³') == {'m³'}
    assert _units('Model12m3') == set()


def test_cubic_rate_is_bound_to_its_own_field():
    facts = SourceFacts.from_record({'sku': 'cross-field', 'spec_es': '12 voltios', 'desc_es': '2,3 m3/hora'})
    result = guard_translation(facts, {'spec': CASE['target']}, ('spec',))
    assert result['status'] == 'FAIL'
