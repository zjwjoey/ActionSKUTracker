"""CI_SAFE: pure source-note language checks; no files, network or Apply."""
import pytest

from action_tracker.localization.source_notes import mask_verified_detail_source_notes, repair_legacy_detail_source_notes
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.release_gate import audit_research_release
from action_tracker.exporting.dictionary_join import _repair_export_details


@pytest.mark.parametrize('value', ['Sí', 'No'])
@pytest.mark.parametrize('delimiter', ['；', ';', '\n', ' | '])
def test_complete_source_note_is_not_ordinary_spanish(value, delimiter):
    source = f'Color: Blanco; Incluye oído: {value}; Número del artículo: 1'
    note = f'来源异常：官网字段Incluye oído={value}'
    target = delimiter.join(['颜色：白色', note, '商品编号：1'])
    assert note not in mask_verified_detail_source_notes(source, target)
    qa = guard_translation(SourceFacts(sku='1', details_es=source), {'details': target}, ('details',))
    assert 'SPANISH_RESIDUAL' not in {f['rule_id'] for f in qa['findings']}
    result = audit_research_release([{'sku':'1', 'details_es':source, 'details_zh':target}])
    assert result.counts['SPANISH_RESIDUAL'] == 0
    # Language recognition never creates approval or fixes source hashes.
    assert result.counts['UNAPPROVED_ZH'] > 0
    assert result.counts['SOURCE_HASH_MISMATCH'] > 0


@pytest.mark.parametrize('source,target', [
    ('Color: Blanco', '来源异常：官网字段Incluye oído=Sí'),
    ('Incluye oído: No', '来源异常：官网字段Incluye oído=Sí'),
    ('Incluye oído: Sí', '来源异常：官网字段Incluye oído'),
    ('Incluye oído: Sí', '来源异常：官网字段Incluye oído=Sí con colores'),
    ('Incluye oído: Sí', '伪造来源异常：官网字段Incluye oído=Sí'),
    ('Incluye oído: Sí', '来源异常：官网字段Incluye oído=Quizás'),
    ('Incluye oído: Sí', '来源异常：官网字段Incluye oídos=Sí'),
    ('Incluye oído: Sí', '来源异常：官网字段Incluye oído=Sí；来源异常：官网字段Incluye oído=Sí'),
    ('Incluye oído: Sí; Incluye oído: Sí', '来源异常：官网字段Incluye oído=Sí'),
])
def test_changed_or_unbound_evidence_is_not_masked(source, target):
    assert mask_verified_detail_source_notes(source, target) == target


def test_duplicate_pairs_and_flattened_source_keep_value_and_count():
    source = 'Especificaciones; Incluye oído Sí; Incluye oído No'
    text = '来源异常：官网字段Incluye oído=Sí；来源异常：官网字段Incluye oído=No'
    assert mask_verified_detail_source_notes(source, text) == '；'


def test_source_note_does_not_waive_other_facts_prose_or_physical_state():
    source = 'Cantidad: 2 unidades; Incluye oído: Sí; Sustancia: Válido'
    target = '数量：3件；来源异常：官网字段Incluye oído=Sí；来源异常：官网字段Sustancia=Válido；con colores'
    qa = guard_translation(SourceFacts(sku='1', details_es=source), {'details':target}, ('details',))
    rules = {f['rule_id'] for f in qa['findings']}
    assert {'NUMERIC_DROPPED','SPANISH_RESIDUAL','SOURCE_DETAILS_TYPED_VALUE_INVALID'} <= rules


def test_other_field_source_cannot_authorize_a_note():
    source = SourceFacts(sku='1', name_es='Incluye oído: Sí', desc_es='Producto', details_es='Color: Blanco')
    target = '来源异常：官网字段Incluye oído=Sí'
    for field in ['description','details']:
        qa = guard_translation(source, {field:target}, (field,))
        assert 'SPANISH_RESIDUAL' in {f['rule_id'] for f in qa['findings']}


def test_verified_note_does_not_clear_stale_status():
    result = audit_research_release([{
        'sku':'1','details_es':'Incluye oído: Sí',
        'details_zh':'来源异常：官网字段Incluye oído=Sí',
        'zh_freshness_status':'STALE','zh_review_status':'APPROVED',
    }])
    assert result.counts['SPANISH_RESIDUAL'] == 0
    assert result.counts['STALE_ZH'] > 0


@pytest.mark.parametrize('key', ['是否带把手','带把手','是否带耳'])
@pytest.mark.parametrize('es,zh', [('Sí','是'),('No','否')])
def test_legacy_projection_preserves_complete_original_value(key, es, zh):
    source = f'Color: Blanco; Incluye oído: {es}; Número del artículo: 1'
    target = f'颜色：白色；{key}：{zh}；商品编号：1'
    fixed = repair_legacy_detail_source_notes(source, target)
    assert fixed == f'颜色：白色；来源异常：官网字段Incluye oído={es}；商品编号：1'
    assert repair_legacy_detail_source_notes(source, fixed) == fixed
    assert '是否来源异常' not in fixed
    assert 'Incluye oído' not in mask_verified_detail_source_notes(source, fixed)
    assert _repair_export_details(target, source) == fixed


@pytest.mark.parametrize('source,target', [
    ('Color: Blanco','是否带把手：是'),
    ('Incluye oído: No','是否带把手：是'),
    ('Incluye oído: Sí; Incluye oído: Sí','是否带把手：是'),
    ('Incluye oído: Sí','是否带把手：是；是否带把手：是'),
    ('Incluye oído: Sí','是否带把手：是，适用于所有杯子'),
    ('Incluye oído: Quizás','是否带把手：是'),
])
def test_legacy_projection_does_not_guess_append_or_hide_mismatch(source, target):
    assert repair_legacy_detail_source_notes(source, target) == target
