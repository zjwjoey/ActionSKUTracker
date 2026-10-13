"""CI_SAFE: source-bound body-cloth form, QA and HIGH gate; no production I/O."""
import json
import runpy
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import body_cloth_form_spans, parse_semantic_facts

ROOT = Path(__file__).parents[1]
ROW = json.loads((Path(__file__).parent / 'fixtures' /
    'historical_pending3196_body_cloth_20261011.json').read_text(encoding='utf-8'))
ENGINE = LocalizationEngine(knowledge=KnowledgeLoader(ROOT / 'data' / 'dictionary').load())


def check(context, target):
    record = {'sku': 'body-cloth-regression', **context}
    return guard_translation(SourceFacts.from_record(record), {'details': target},
                             ('details',), semantic_facts=ENGINE.resolve(record).semantic_facts)


def test_real_candidate_passes_hard_qa_without_approving_high_identity_change():
    assert ROW['source_evidence']['text'] == ROW['source']
    assert len(ROW['source_evidence']['file_hash']) == 64
    assert ROW['qa_before']['status'] == 'FAIL'
    assert check(ROW['context'], ROW['candidate'])['status'] == 'PASS'
    native = runpy.run_path(str(ROOT / 'scripts' / 'run_historical_localization_pilot.py'))
    assert native['reviewed_write_status']({
        'sku': ROW['sku'], 'field': 'details', 'after': ROW['candidate'],
        'semantic_status': 'PASS', 'risk_level': 'HIGH', 'review_model': 'CODEX',
        'review_note': 'Source-bound body-cloth identity proposal retains HIGH Owner gate.',
    }) == 'OWNER_REVIEW_REQUIRED'
    assert ROW['owner_gate'] == 'PRESERVED'
    assert ROW['production_apply'] is False


@pytest.mark.parametrize('historical_quality', [True, False])
def test_both_runtime_modes_preserve_typed_form_in_own_field(historical_quality):
    source = SourceFacts.from_record({'details_es': ROW['source']})
    facts = parse_semantic_facts(source, historical_quality=historical_quality)
    own = [f for f in facts if f.source_field == 'details_es' and f.source_text.casefold() == 'paño']
    assert [(f.semantic_type, f.canonical_value) for f in own] == [('VARIANT', '巾片')]
    assert own[0].evidence == 'source_bound_body_cloth_form'


@pytest.mark.parametrize('source', [
    'Apto para tipo de parte del cuerpo: Todo el cuerpo; Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Manos; Sustancia: Paño',
    'SUSTANCIA：PAÑO；APTO PARA TIPO DE PARTE DEL CUERPO：ROSTRO',
    'Sustancia: Paño\nApto para tipo de parte del cuerpo: Manos\nColor: Azul',
])
def test_unique_complete_keys_match_only_the_own_form_value(source):
    spans = body_cloth_form_spans(source)
    assert len(spans) == 1
    assert source[slice(*spans[0])].casefold() == 'paño'


@pytest.mark.parametrize('source', [
    'Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Rostro; Otra sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Piel; Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Manos; Sustancia: Paños',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño húmedo',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño de limpieza',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia Paño',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño; Sustancia: Líquido',
    'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño; Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Rostro; Apto para tipo de parte del cuerpo: Manos; Sustancia: Paño',
    'Apto para tipo de parte del cuerpo: Rostro; Tipo de dispensador: Paño',
])
def test_unknown_qualified_or_duplicate_values_cannot_activate_rule(source):
    assert body_cloth_form_spans(source) == ()
    facts = parse_semantic_facts(SourceFacts.from_record({'details_es': source}))
    assert not any(f.evidence == 'source_bound_body_cloth_form' for f in facts)


@pytest.mark.parametrize('target', ['巾片', '布片', '布巾'])
def test_generic_cloth_form_aliases_do_not_require_cleaning_or_wetness(target):
    context = {'details_es': 'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño'}
    assert check(context, '适用身体部位：面部；形态：' + target)['status'] == 'PASS'


@pytest.mark.parametrize('target', ['液体', '喷雾', '清洁布', '抹布', '无巾片', '非巾片', '不是布片'])
def test_form_cannot_be_dropped_negated_or_replaced_with_cleaning_product(target):
    context = {'details_es': 'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño'}
    result = check(context, '适用身体部位：面部；形态：' + target)
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' and
               f['evidence']['semantic_type'] == 'VARIANT' for f in result['findings'])


def test_separate_positive_cleaning_cloth_product_remains_required():
    context = {'details_es': 'Apto para tipo de parte del cuerpo: Rostro; Sustancia: Paño; Producto: Paño de limpieza'}
    result = check(context, '适用身体部位：面部；形态：巾片；用品：棉片')
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' and
               f['evidence']['semantic_type'] == 'PRODUCT_TYPE' for f in result['findings'])
    assert check(context, '适用身体部位：面部；形态：巾片；用品：清洁布')['status'] == 'PASS'


def test_cleaning_type_key_does_not_become_body_cloth_form():
    context = {'details_es': 'Tipo de medio de limpieza / cuidado: Paño'}
    assert check(context, '清洁/护理介质类型：抹布')['status'] == 'PASS'
    assert check(context, '清洁/护理介质类型：巾片')['status'] == 'FAIL'


@pytest.mark.parametrize('other_field', ['name_es', 'desc_es', 'spec_es'])
def test_other_fields_cannot_supply_body_key_for_own_details_exception(other_field):
    context = {other_field: 'Apto para tipo de parte del cuerpo: Rostro', 'details_es': 'Sustancia: Paño'}
    assert check(context, '形态：巾片')['status'] == 'FAIL'


@pytest.mark.parametrize('replacement', ['6 件', ''])
def test_real_candidate_quantity_damage_remains_blocked(replacement):
    assert check(ROW['context'], ROW['candidate'].replace('5 件', replacement))['status'] == 'FAIL'


def test_rule_preserves_source_context_and_hash():
    context = dict(ROW['context'])
    before = SourceFacts.from_record(context).source_hash
    check(context, ROW['candidate'])
    assert context == ROW['context']
    assert SourceFacts.from_record(context).source_hash == before
