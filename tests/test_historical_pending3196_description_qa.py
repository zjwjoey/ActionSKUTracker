"""CI_SAFE: own-field QA regressions; no runtime DB, network or writes."""
import json
import runpy
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SemanticFact, SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.qa import _units, guard_translation


ROOT = Path(__file__).parents[1]
CASES = {r['sku']: r for r in json.loads((Path(__file__).parent / 'fixtures' /
    'historical_pending3196_description_qa_20261011.json').read_text(encoding='utf-8'))}
ENGINE = LocalizationEngine(knowledge=KnowledgeLoader(ROOT / 'data' / 'dictionary').load())


def check(context, target, facts=None):
    record = {'sku': 'description-qa', **context}
    if facts is None:
        facts = ENGINE.resolve(record).semantic_facts
    return guard_translation(SourceFacts.from_record(record), {'description': target},
                             ('description',), semantic_facts=facts)


@pytest.mark.parametrize('sku', ['3000357', '3007114'])
def test_real_source_bound_descriptions_pass_qa_but_high_still_requires_owner(sku):
    r = CASES[sku]
    assert r['source_evidence']['text'] == r['source']
    assert len(r['source_evidence']['file_hash']) == 64
    assert r['qa_before']['status'] == 'FAIL'
    result = check(r['context'], r['target_for_hard_qa'])
    assert result['status'] == 'PASS', result['findings']
    native = runpy.run_path(str(ROOT / 'scripts' / 'run_historical_localization_pilot.py'))
    assert native['reviewed_write_status']({
        'sku': sku, 'field': 'description', 'after': r['target_for_hard_qa'],
        'semantic_status': 'PASS', 'risk_level': 'HIGH', 'review_model': 'CODEX',
        'review_note': 'Preserve the previous HIGH Owner approval gate.',
    }) == 'OWNER_REVIEW_REQUIRED'
    assert r['semantic_approval'] == 'HIGH_OWNER_PENDING'
    assert not r['production_apply']


@pytest.mark.parametrize('source,target', [
    ('Personajes populares', 'Spider-Man角色图案'),
    ('Diseño de Spider-Man', 'Spider角色图案'),
    ('Diseño de Spider-Man', 'Man角色图案'),
    ('Diseño de Spider-Man', 'Spider-Man con personajes图案'),
    ('Diseño de Spider-Man', 'Spider-Man resistente图案'),
])
def test_full_source_bound_ip_exception_cannot_authorize_parts_or_prose(source, target):
    result = check({'desc_es': source}, target, facts=())
    assert any(f['rule_id'] == 'SPANISH_RESIDUAL' for f in result['findings'])


def illumination_fact(term='iluminación', target='照明灯'):
    return SemanticFact('PRODUCT_TYPE', term, target, canonical_value=target,
                        source_field='desc_es', placement='name')


@pytest.mark.parametrize('context', [
    {'desc_es': 'Iluminación alrededor de la casa'},
    {'name_es': 'Lámpara de pared', 'desc_es': 'Iluminación alrededor de la casa'},
    {'desc_es': 'Una iluminación cálida con una funda nórdica de satén'},
])
def test_other_field_lamp_or_bedding_cannot_authorize_function_alias(context):
    result = check(context, '温暖氛围，提供照明。', facts=(illumination_fact(),))
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in result['findings'])


def test_function_alias_does_not_discharge_a_separate_wall_lamp_identity_fact():
    source = 'Iluminación alrededor de la casa con una lámpara de pared'
    facts = (illumination_fact(), illumination_fact('lámpara de pared', '壁灯'))
    result = check({'desc_es': source}, '提供照明。', facts=facts)
    dropped = [f['evidence']['source_term'] for f in result['findings']
               if f['rule_id'] == 'SEMANTIC_FACT_DROPPED']
    assert dropped == ['lámpara de pared']


def test_real_bedding_expression_remains_held_without_an_inferred_lighting_waiver():
    r = CASES['2537232']
    result = check(r['context'], r['target_for_hard_qa'])
    assert result['status'] == 'FAIL'
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in result['findings'])


@pytest.mark.parametrize('replacement', ['不到6米', '不到5厘米'])
def test_real_lamp_range_value_and_unit_changes_still_fail(replacement):
    r = CASES['3007114']
    target = r['target_for_hard_qa'].replace('不到5米', replacement)
    result = check(r['context'], target)
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('source,target', [
    ('5 metros', '5米'), ('1 metro', '1米'), ('1,5 centímetros', '1.5厘米'),
    ('2 milímetros', '2毫米'), ('3 kilómetros', '3千米'),
])
def test_full_spanish_length_units_preserve_their_exact_dimension(source, target):
    assert check({'desc_es': source}, target, facts=())['status'] == 'PASS'


@pytest.mark.parametrize('source,target', [
    ('5 metros', '5厘米'), ('5 centímetros', '5米'),
    ('2 milímetros', '2千米'), ('3 kilómetros', '3米'),
    ('5 metros', '5'), ('5 metros', '500厘米'),
])
def test_wrong_or_dropped_full_length_unit_is_blocked(source, target):
    result = check({'desc_es': source}, target, facts=())
    assert result['status'] == 'FAIL'
    assert any(f['rule_id'] == 'UNIT_DROPPED' for f in result['findings'])


@pytest.mark.parametrize('source', [
    'AB5metros', '5 metros cuadrados', '5 metros cúbicos',
    '5 centímetros cuadrados', '5 milímetros cúbicos', '5 metropolitano',
])
def test_full_length_unit_scanner_does_not_promote_identifiers_or_other_dimensions(source):
    assert not _units(source)
