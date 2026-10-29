"""Source-bound QA regressions; these fixtures are not Owner-approved Gold."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.body_names import mask_source_bound_body_names
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.qa import _semantic_aliases, guard_translation
from action_tracker.exporting.repair_overrides import field_source_hash


CASES = json.loads((Path(__file__).parent / 'fixtures/apply_preview19_qa_20261028.json').read_text('utf8'))


def check(record, field, target):
    plan = LocalizationEngine().resolve(record)
    return guard_translation(SourceFacts.from_record(record), {field: target}, (field,),
                             semantic_facts=plan.semantic_facts)


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['sku'] + '-' + c['field'])
def test_real_blocked_fields_no_longer_trigger_confirmed_false_rules(case):
    zh_field = 'desc_zh' if case['field'] == 'description' else case['field'] + '_zh'
    assert field_source_hash(case['source'], zh_field) == case['field_source_hash']
    qa = check(case['source'], case['field'], case['target'])
    assert not set(case['blocked_rules']) & {f['rule_id'] for f in qa['findings']}, qa


@pytest.mark.parametrize('field,es,zh', [
    ('name', 'Calcetines térmicos para mujer', '女士保暖衣'),
    ('name', 'Calcetines para niña Pairz', '女童手套'),
    ('name', 'Calcetines para niño Pairz', '不是男童袜'),
    ('name', 'Calcetines de senderismo térmicos', '不是保暖袜'),
    ('name', 'Paño hidrofílico Hoodadoo', '不是吸水布'),
    ('name', 'Paño de limpieza', '吸水布'),
    ('details', 'Tipo de detergente de limpieza de WC: Producto para la limpieza / refrescante de WC', '洁厕剂类型：洗洁精'),
    ('details', 'Tipo de detergente de limpieza de WC: Producto para la limpieza / refrescante de WC', '类型：不是洁厕剂'),
    ('name', 'Detergente lavavajillas', '洁厕剂'),
    ('details', 'Iluminación de la pantalla: Sí', '照明灯：是'),
    ('details', 'Iluminación de la pantalla: Sí', '无屏幕照明'),
    ('name', 'Iluminación', '屏幕照明'),
    ('name', 'Ositos de goma para deporte Accelerate', '运动小熊橡胶'),
    ('name', 'Ositos de goma para deporte Accelerate', '不是软糖'),
    ('details', 'Método de administración: Oral; Sustancia: Goma; Uso previsto como: Suplemento nutricional', '使用方式：口服；形态：橡胶；预期用途：营养补充剂'),
    ('details', 'Material: Goma', '材质：软糖'),
    ('details', 'Método de administración: Oral; Uso previsto como: Suplemento nutricional; Material: Goma', '使用方式：口服；预期用途：营养补充剂；材质：软糖'),
])
def test_wrong_nouns_and_negations_remain_blocked(field, es, zh):
    source_field = 'desc_es' if field == 'description' else field + '_es'
    assert not check({'sku': 'negative', source_field: es}, field, zh)['overall_ready']


def test_hydrophilic_context_cannot_be_borrowed_from_another_field():
    assert not check({'name_es': 'Paño hidrofílico', 'desc_es': 'Paño de limpieza'},
                     'description', '吸水布')['overall_ready']


def test_explicit_negative_source_does_not_require_a_positive_rubber_claim():
    qa = check({'details_es': 'Material: Sin goma'}, 'details', '材质：不含橡胶')
    assert not any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in qa['findings'])


def test_fab_factory_is_omittable_only_in_names_and_only_as_complete_brand():
    good = check({'name_es': 'Bálsamo labial FAB Factory'}, 'name', '润唇膏')
    assert not any(f['rule_id'] == 'PROTECTED_TOKEN_MISSING' for f in good['findings'])
    for field, es in [('name', 'Bálsamo labial FAB'), ('name', 'Bálsamo labial FAB Factory modelo FAB'),
                      ('description', 'Bálsamo labial FAB Factory')]:
        key = 'desc_es' if field == 'description' else 'name_es'
        qa = check({key: es}, field, '润唇膏')
        assert any(f['rule_id'] == 'PROTECTED_TOKEN_MISSING' for f in qa['findings'])


def test_body_brand_requires_complete_own_source_and_does_not_mask_prose():
    brand = 'La Maison Rêve'
    assert not mask_source_bound_body_names(brand, brand).strip()
    assert mask_source_bound_body_names('algodón', brand) == brand
    assert mask_source_bound_body_names('La Maison', 'La Maison') == 'La Maison'
    qa = check({'desc_es': brand + '. Disponible en varios colores'},
               'description', brand + ' disponible en varios colores')
    assert any(f['rule_id'] == 'SPANISH_RESIDUAL' for f in qa['findings'])


@pytest.mark.parametrize('term,canonical,es,context_alias', [
    ('paño', '清洁布', 'Paño hidrofílico y paño de limpieza', '吸水布'),
    ('paños', '清洁布', 'Paños hidrofílicos y paños de limpieza', '吸水布'),
    ('goma', '橡胶', 'Ositos de goma; Material: Goma', '软糖'),
    ('goma', '橡胶', 'Método de administración: Oral; Sustancia: Goma; '
     'Uso previsto como: Suplemento nutricional; Material: Goma', '软糖'),
    ('goma', '橡胶', 'Método de administración: Oral; Sustancia: Goma; '
     'Uso previsto como: No es un suplemento nutricional', '软糖'),
    ('detergente', '洗洁精', 'Tipo de detergente de limpieza de WC: Producto para la limpieza; '
     'Detergente lavavajillas', '洁厕剂'),
    ('iluminación', '照明灯', 'Iluminación de la pantalla: Sí; Iluminación: Sí', '屏幕照明'),
    ('calcetines', '袜子', 'Calcetines y guantes térmicos', '保暖袜'),
    ('calcetines', '袜子', 'Calcetines y guantes para niña', '女童袜'),
])
def test_context_does_not_replace_another_occurrence_or_unrelated_qualifier(term, canonical, es, context_alias):
    assert context_alias not in _semantic_aliases(term, es, canonical)


@pytest.mark.parametrize('es,zh', [
    ('Ositos de goma; Material: Goma', '小熊软糖；材质：软糖'),
    ('Método de administración: Oral; Sustancia: Goma; '
     'Uso previsto como: No es un suplemento nutricional',
     '使用方式：口服；形态：软糖；预期用途：不是营养补充剂'),
])
def test_ambiguous_gummy_context_cannot_waive_rubber_fact(es, zh):
    qa = check({'details_es': es}, 'details', zh)
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in qa['findings']), qa


def test_hydrophilic_context_cannot_hide_separate_cleaning_cloth():
    qa = check({'name_es': 'Paño hidrofílico y paño de limpieza'}, 'name', '吸水布')
    assert any(f['rule_id'] == 'SEMANTIC_FACT_DROPPED' for f in qa['findings']), qa
