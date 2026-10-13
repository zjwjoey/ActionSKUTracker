"""CI_SAFE: fixture and pure QA only; no production DB, provider or network."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader
from action_tracker.localization.qa import _technical_scan_source, guard_translation

ROOT = Path(__file__).parents[1]
ROW = json.loads((Path(__file__).parent / 'fixtures' /
    'historical_pending3196_connector_preposition_20261011.json').read_text(encoding='utf-8'))
ENGINE = LocalizationEngine(knowledge=KnowledgeLoader(ROOT / 'data' / 'dictionary').load())


def check(context, target):
    record = {'sku': 'connector-regression', **context}
    return guard_translation(SourceFacts.from_record(record), {'details': target},
                             ('details',), semantic_facts=ENGINE.resolve(record).semantic_facts)


def por_missing(result):
    return any(f['rule_id'] == 'PROTECTED_TOKEN_MISSING' and
               f['evidence']['value'] == 'POR' for f in result['findings'])


def test_real_fixture_preserves_source_evidence_and_has_no_apply_claim():
    assert ROW['source_evidence']['text'] == ROW['source']
    assert len(ROW['source_evidence']['file_hash']) == 64
    assert ROW['qa_before']['status'] == 'FAIL'
    assert por_missing(ROW['qa_before'])
    assert ROW['production_apply'] is False
    assert ROW['semantic_approval'] == 'NATIVE_APPROVAL_PENDING'


def test_real_all_caps_wired_connector_preposition_is_translatable():
    result = check(ROW['context'], ROW['target_for_hard_qa'])
    assert result['status'] == 'PASS', result['findings']


@pytest.mark.parametrize('source', [
    'POR', 'MODELO POR', 'TECLA POR', 'Tipo de conector: POR',
    'Tipo de conector: POR CABLE PLUS', 'Tipo de conector: POR CABLEADO',
    'Modelo de conector: POR CABLE', 'Tipo de conector POR CABLE',
    'Tipo de conector: POR CABLE; MODELO POR',
])
def test_other_por_occurrences_remain_protected(source):
    assert por_missing(check({'details_es': source}, '有线'))


def test_mixed_preposition_and_model_require_only_the_model_occurrence():
    source = 'Tipo de conector: POR CABLE; MODELO POR'
    assert _technical_scan_source(source) == 'Tipo de conector:  CABLE; MODELO POR'
    result = check({'details_es': source}, '有线；型号：POR')
    assert not any(f['rule_id'] in {'PROTECTED_TOKEN_MISSING', 'PROTECTED_TOKEN_DUPLICATED'}
                   and f['evidence']['value'] == 'POR' for f in result['findings'])


def test_another_field_cannot_authorize_por_omission():
    result = check({'name_es': 'Tipo de conector: POR CABLE',
                    'details_es': 'MODELO POR'}, '有线')
    assert por_missing(result)


@pytest.mark.parametrize('replacement', ['3218231', ''])
def test_real_sku_damage_still_blocks_after_preposition_fix(replacement):
    result = check(ROW['context'], ROW['target_for_hard_qa'].replace('3218230', replacement))
    assert result['status'] == 'FAIL'


@pytest.mark.parametrize('source', [
    'Tipo de conector: POR CABLE; USB-C',
    'Tipo de conector: POR CABLE; HDMI',
    'Tipo de conector: POR CABLE; Modelo AB123',
])
def test_real_interface_and_model_tokens_cannot_be_dropped(source):
    result = check({'details_es': source}, '有线')
    assert result['status'] == 'FAIL'
    assert any(f['rule_id'] in {'PROTECTED_TOKEN_MISSING', 'MODEL_DROPPED'}
               for f in result['findings'])


def test_qa_scan_view_does_not_modify_source_or_fact_hash():
    context = dict(ROW['context'])
    facts = SourceFacts.from_record({'sku': 'connector-regression', **context})
    before = facts.source_hash
    check(context, ROW['target_for_hard_qa'])
    assert context == ROW['context']
    assert SourceFacts.from_record({'sku': 'connector-regression', **context}).source_hash == before
