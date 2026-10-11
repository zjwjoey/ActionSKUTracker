"""Real SKU energy units: hard QA equivalence never creates approval."""
import json
import runpy
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import _capacity_scan_view, guard_translation
from action_tracker.localization.protection.tokens import protect_text


CASE = json.loads((Path(__file__).parent / 'fixtures' /
                   'historical_pending3196_kwh_unit_20261011.json').read_text(encoding='utf-8'))


def check(source, target, field='spec'):
    key = 'details_es' if field == 'details' else 'spec_es'
    return guard_translation(SourceFacts.from_record({'sku': CASE['sku'], key: source}),
                             {field: target}, (field,))


def test_real_energy_unit_translation_passes_hard_qa_without_semantic_approval():
    assert CASE['source_evidence']['selected']['text'] == CASE['source']
    assert len(CASE['source_evidence']['selected']['file_hash']) == 64
    facts = SourceFacts.from_record({'sku': CASE['sku'], **CASE['context']})
    result = guard_translation(facts, {'details': CASE['target_for_hard_qa']}, ('details',))
    assert result['status'] == 'PASS', result['findings']
    assert CASE['semantic_approval'] == 'REVIEW_REQUIRED_HIGH'
    native = runpy.run_path(str(Path(__file__).parents[1] / 'scripts' /
                               'run_historical_localization_pilot.py'))
    assert native['reviewed_write_status']({
        'sku': CASE['sku'], 'field': 'details', 'after': CASE['target_for_hard_qa'],
        'semantic_status': 'PASS', 'risk_level': 'HIGH', 'review_model': 'CODEX',
        'review_note': 'Preserve the independent source-attribute ambiguity hold.',
    }) == 'OWNER_REVIEW_REQUIRED'


@pytest.mark.parametrize('source,target', [
    ('3 kWh', '3千瓦时'), ('3 Wh', '3瓦时'),
    ('500 mAh', '500毫安时'), ('2 Ah', '2安时'),
    ('3 kWh / 1000h', '3千瓦时/1000小时'),
    ('3 kWh | 5 Wh', '3千瓦时 | 5瓦时'),
])
def test_exact_unit_names_preserve_quantities_and_occurrences(source, target):
    result = check(source, target)
    assert result['status'] == 'PASS', result['findings']


@pytest.mark.parametrize('source,target', [
    ('3 kWh', '5千瓦时'), ('3 kWh', '13千瓦时'),
    ('3 kWh', '3瓦时'), ('3 kWh', '3千瓦'),
    ('500 mAh', '500安时'), ('3 kWh', '千瓦时'),
    ('3 kWh', '3千瓦时（3 kWh）'),
    ('3 kWh', '3千瓦时/1000小时'),
    ('AB12 | 3 kWh', 'AB13 | 3千瓦时'),
    ('3 kWh', '3千瓦时 AB12'),
])
def test_unit_aliases_cannot_hide_changed_added_or_dropped_facts(source, target):
    result = check(source, target)
    assert result['status'] == 'FAIL', (source, target, result)
    assert any(f['blocking'] for f in result['findings'])


def test_comparison_view_does_not_change_provider_placeholders_or_identifiers():
    value = 'AB12 | 3千瓦时'
    assert _capacity_scan_view('AB123千瓦时') == 'AB123千瓦时'
    assert _capacity_scan_view('3千瓦时s') == '3千瓦时s'
    assert protect_text(value).tokens == ('AB12', '3')
    assert '3 kWh' in protect_text('3 kWh').tokens
