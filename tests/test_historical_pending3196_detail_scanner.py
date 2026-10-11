"""CI_SAFE: real historical QA reproductions, no provider or database writes."""
import json
from pathlib import Path
import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.knowledge import KnowledgeLoader

ROWS = {r['sku']: r for r in json.loads((Path(__file__).parent / 'fixtures' /
    'historical_pending3196_detail_scanner_20261011.json').read_text(encoding='utf-8'))}
ENGINE = LocalizationEngine(knowledge=KnowledgeLoader(Path(__file__).parent.parent / 'data' / 'dictionary').load())


def check(context, target, field='details'):
    record = {'sku':'qa-regression', **context}
    source = SourceFacts.from_record(record)
    facts = ENGINE.resolve(record).semantic_facts
    return guard_translation(source, {field:target}, (field,), semantic_facts=facts)


@pytest.mark.parametrize('sku', ['3003940','3217432','3200285'])
def test_real_sources_have_field_evidence_without_semantic_approval(sku):
    row = ROWS[sku]
    assert row['evidence']['text'] == row['source']
    assert len(row['evidence']['file_hash']) == 64
    assert row['semantic_approval'] == 'NOT_APPROVED'


def test_real_bamboo_material_atom_is_not_an_omitted_material():
    row = ROWS['3003940']; result = check(row['context'], row['candidate'])
    assert result['status'] == 'PASS', result['findings']


def test_real_plain_wood_atom_does_not_clear_untranslated_bad_detail_keys():
    row = ROWS['3217432']; result = check(row['context'], row['candidate'])
    assert not any(f['rule_id']=='SEMANTIC_FACT_DROPPED' and
        f['evidence'].get('source_term')=='madera' for f in result['findings'])
    assert any(f['rule_id']=='SPANISH_RESIDUAL' for f in result['findings'])
    assert result['status']=='FAIL'


def test_real_spanish_del_article_is_not_a_technical_identifier():
    row = ROWS['3200285']; result = check(row['context'], row['candidate'])
    assert result['status']=='PASS', result['findings']


@pytest.mark.parametrize('target', ['材质：竹纹','材质：竹纤维','图案：竹','材质：非竹','材质：铁'])
def test_bamboo_material_cannot_be_satisfied_by_appearance_fibre_or_another_key(target):
    result=check({'details_es':'Material: Bambú'},target)
    assert any(f['rule_id']=='SEMANTIC_FACT_DROPPED' for f in result['findings'])


@pytest.mark.parametrize('source,target', [
    ('Material: Fibra de bambú','材质：竹'),
    ('Material: Madera noble','材质：木'),
    ('Material: Bambú; Material: Hierro','材质：竹、铁'),
    ('Material: Bambú','材质：竹；材质：铁'),
    ('Material: Madera','材质：木纹'),
])
def test_qualified_or_duplicate_materials_do_not_receive_the_short_atom_exception(source,target):
    result=check({'details_es':source},target)
    assert any(f['rule_id']=='SEMANTIC_FACT_DROPPED' for f in result['findings'])


def test_other_field_cannot_authorize_a_missing_own_material():
    result=check({'details_es':'Material: Bambú','name_es':'Bambú'},'材质：铁')
    assert any(f['rule_id']=='SEMANTIC_FACT_DROPPED' for f in result['findings'])


@pytest.mark.parametrize('source', ['TECLA DEL','DEL','MODELO DEL','TECLA DEL; SOPORTE DEL TELÉFONO'])
def test_actual_del_identifier_still_cannot_be_dropped(source):
    result=check({'details_es':source},'手机支架')
    assert any(f['rule_id']=='PROTECTED_TOKEN_MISSING' and
        f['evidence']['value'].casefold()=='del' for f in result['findings'])


def test_mixed_article_and_keyboard_key_protect_only_the_actual_key():
    result=check({'details_es':'TECLA DEL; SOPORTE DEL TELÉFONO'},'DEL键；手机支架')
    assert not any(f['rule_id'] in {'PROTECTED_TOKEN_MISSING','PROTECTED_TOKEN_DUPLICATED'} and
        f['evidence']['value'].casefold()=='del' for f in result['findings'])


def test_real_detail_numeric_damage_remains_blocked():
    row=ROWS['3003940']; result=check(row['context'],row['candidate'].replace('32.8','33.8'))
    assert result['status']=='FAIL'
    assert any(f['rule_id']=='NUMERIC_DROPPED' for f in result['findings'])
