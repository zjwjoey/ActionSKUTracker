"""Real pending fields: dimension separators must not hide preserved units."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import _units, guard_translation

CASES = json.loads((Path(__file__).parent / 'fixtures' /
                    'historical_pending3196_compact_units_20261011.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['sku'])
def test_real_compact_dimensions_and_multipacks_keep_their_units(case):
    assert case['evidence']['text'] == case['source']
    assert len(case['evidence']['file_hash']) == 64
    assert case['independent_decision'] == 'KEEP'
    facts = SourceFacts.from_record({'sku': case['sku'], 'spec_es': case['source']})
    result = guard_translation(facts, {'spec': case['target']}, ('spec',))
    assert result['status'] == 'PASS', result['findings']


@pytest.mark.parametrize('source,target', [
    ('30 x 17cm', '30×17mm'),
    ('38 x 38 cm', '38×38'),
    ('8x 70 ml', '8×70l'),
    ('12x 50 ml', '12×50'),
])
def test_compact_rendering_cannot_drop_or_change_a_unit(source, target):
    facts = SourceFacts.from_record({'sku': 'unit-corruption', 'spec_es': source})
    result = guard_translation(facts, {'spec': target}, ('spec',))
    assert result['status'] == 'FAIL'
    assert any(f['rule_id'] == 'UNIT_DROPPED' for f in result['findings'])


def test_units_inside_latin_identifiers_are_not_measurement_evidence():
    assert not _units('abc38cm')
    assert not _units('USB-C38cm')
    assert _units('38×38cm') == {'cm'}
    assert _units('8×70ml') == {'ml'}
