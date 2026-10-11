"""CI_SAFE: pure own-field semantics/QA and approval gate; no production I/O."""
import json
import runpy
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import correction_device_eraser_spans, parse_semantic_facts

ROOT = Path(__file__).parents[1]
ROW = json.loads((Path(__file__).parent / 'fixtures' /
    'historical_pending3196_correction_device_goma_20261011.json').read_text(encoding='utf-8'))
ENGINE = LocalizationEngine(knowledge=KnowledgeLoader(ROOT / 'data' / 'dictionary').load())


def check(context, target):
    record = {'sku': 'correction-goma-regression', **context}
    facts = ENGINE.resolve(record).semantic_facts
    return guard_translation(SourceFacts.from_record(record), {'details': target},
                             ('details',), semantic_facts=facts)


def test_real_eraser_type_fixture_is_source_bound_and_remains_high_owner_pending():
    assert ROW['source_evidence']['text'] == ROW['source']
    assert len(ROW['source_evidence']['file_hash']) == 64
    assert ROW['qa_before']['qa']['status'] == 'FAIL'
    assert check(ROW['context'], ROW['target_for_hard_qa'])['status'] == 'PASS'
    native = runpy.run_path(str(ROOT / 'scripts' / 'run_historical_localization_pilot.py'))
    assert native['reviewed_write_status']({
        'sku': ROW['sku'], 'field': 'details', 'after': ROW['target_for_hard_qa'],
        'semantic_status': 'PASS', 'risk_level': 'HIGH', 'review_model': 'CODEX',
        'review_note': 'Product identity correction retains HIGH owner gate.',
    }) == 'OWNER_REVIEW_REQUIRED'
    assert ROW['semantic_approval'] == 'HIGH_OWNER_PENDING'
    assert ROW['production_apply'] is False


@pytest.mark.parametrize('historical_quality', [True, False])
def test_structured_type_is_eraser_in_both_runtime_semantic_modes(historical_quality):
    source = SourceFacts.from_record({'details_es': 'Tipo de dispositivo de corrección: Goma'})
    facts = parse_semantic_facts(source, historical_quality=historical_quality)
    own = [f for f in facts if f.source_field == 'details_es' and f.source_text.casefold() == 'goma']
    assert [(f.semantic_type, f.canonical_value) for f in own] == [('PRODUCT_TYPE', '橡皮擦')]


@pytest.mark.parametrize('source', [
    'Tipo de dispositivo de corrección: Goma',
    'TIPO DE DISPOSITIVO DE CORRECCIÓN: GOMA',
    'Tipo de dispositivo de correccion：Goma；Color：Azul',
    'Color: Azul\nTipo de dispositivo de corrección: Goma\nCantidad: 12 piezas',
])
def test_complete_key_value_matches_only_the_goma_value_span(source):
    spans = correction_device_eraser_spans(source)
    assert len(spans) == 1
    assert source[slice(*spans[0])].casefold() == 'goma'


@pytest.mark.parametrize('source', [
    'Material: Goma', 'Tipo: Goma', 'Otro tipo: Goma',
    'Tipo de dispositivo de corrección: Gomaespuma',
    'Tipo de dispositivo de corrección: Goma negra',
    'Tipo de dispositivo de corrección: Gomas',
    'Tipo de dispositivo de corrección Goma',
    'Tipo de dispositivo de corrección: Goma; Tipo de dispositivo de corrección: Cinta',
    'Tipo de dispositivo de corrección: Goma; Tipo de dispositivo de corrección: Goma',
])
def test_unknown_qualified_or_duplicate_values_cannot_use_eraser_rule(source):
    assert correction_device_eraser_spans(source) == ()
    facts = parse_semantic_facts(SourceFacts.from_record({'details_es': source}))
    assert not any(f.evidence == 'source_bound_correction_device_type' for f in facts)


def test_separate_material_and_type_both_remain_required():
    context = {'details_es': 'Tipo de dispositivo de corrección: Goma; Material: Goma'}
    assert check(context, '纠错用品类型：橡皮擦；材质：橡胶')['status'] == 'PASS'
    result = check(context, '纠错用品类型：橡皮擦')
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' and
               f['evidence']['semantic_type'] == 'MATERIAL' for f in result['findings'])


@pytest.mark.parametrize('target', ['类型：橡胶', '类型：橡皮筋', '类型：胶棒'])
def test_eraser_identity_cannot_be_satisfied_by_material_or_other_products(target):
    result = check({'details_es': 'Tipo de dispositivo de corrección: Goma'}, target)
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' and
               f['evidence']['semantic_type'] == 'PRODUCT_TYPE' for f in result['findings'])


def test_other_fields_cannot_authorize_rubber_material_omission():
    result = check({'name_es': 'Tipo de dispositivo de corrección: Goma',
                    'details_es': 'Material: Goma'}, '材质：橡皮擦')
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in result['findings'])


@pytest.mark.parametrize('replacement', ['13 件', ''])
def test_real_numeric_damage_remains_blocked(replacement):
    result = check(ROW['context'], ROW['target_for_hard_qa'].replace('12 件', replacement))
    assert result['status'] == 'FAIL'


def test_type_rule_does_not_change_source_context_or_hash():
    context = dict(ROW['context'])
    before = SourceFacts.from_record(context).source_hash
    check(context, ROW['target_for_hard_qa'])
    assert context == ROW['context']
    assert SourceFacts.from_record(context).source_hash == before
