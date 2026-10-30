"""CI_SAFE: source/candidate regressions recovered from d133dd6; pure QA only.

No archived review is an approval. These literals preserve the original
Spanish and rejected Chinese; faithful alternatives are guard controls only.
"""
import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import audit_translation


DETAIL_CASES = [
    ('Apto para tipo de dentadura: Dentadura de adultos', '适用假牙类型：成人假牙', '适用牙齿类型：成人牙齿', 'DENTITION_CHANGED_TO_DENTURE'),
    ('Sin colorantes artificiales: No', '是否含人工色素：否', '不含人工色素：否', 'NEGATIVE_ATTRIBUTE_ROLE_CHANGED'),
    ('Sin conservantes: No', '是否含防腐剂：否', '不含防腐剂：否', 'NEGATIVE_ATTRIBUTE_ROLE_CHANGED'),
    ('Número de posiciones de sentado: 1', '座位数：1', '座椅可调位置数：1', 'SEATING_POSITION_CHANGED_TO_CAPACITY'),
    ('Tipo de dispositivo de corrección: Goma', '矫正器械类型：橡胶', '涂改工具类型：橡皮擦', 'STATIONERY_CHANGED_TO_MEDICAL_DEVICE'),
    ('Hipoalergénico: Sí', '抗过敏：是', '低致敏：是', 'HYPOALLERGENIC_SCOPE_CHANGED'),
    ('Vegano: No', '素食：否', '纯素：否', 'VEGAN_SCOPE_CHANGED'),
    ('Tipo de taza / vaso: Vaso (no desechable)', '杯/玻璃类型：玻璃杯（非一次性）', '杯子类型：饮用杯（非一次性）', 'CUP_TYPE_CHANGED_TO_MATERIAL'),
    ('Modo de transporte: En el oído sin diadema', '携带方式：入耳式无头戴', '佩戴方式：入耳式无头带', 'WEARING_FORM_CHANGED_TO_TRANSPORT'),
    ('Apto para tipo de fuente de calor: Eléctrico', '适用热源：电动', '适用热源类型：电加热', 'ELECTRIC_HEAT_CHANGED_TO_MOTOR'),
    ('Contenido: 1560 g', '容量：1560克', '净含量：1560克', 'MASS_ATTRIBUTE_CHANGED_TO_CAPACITY'),
    ('Halal: Sí', '清真认证：是', '清真：是', 'CERTIFICATION_QUALIFIER_ADDED'),
]


def findings(raw, target, field='details', **context):
    key = {'details': 'details_es', 'description': 'desc_es', 'name': 'name_es'}[field]
    record = {'sku': 'FIXTURE-FIDELITY', 'name_es': 'Producto', **context, key: raw}
    return audit_translation(SourceFacts.from_record(record), {field: target}, (field,))


@pytest.mark.parametrize('raw,bad,good,code', DETAIL_CASES)
def test_recovered_details_roles_block_only_the_bad_candidate(raw, bad, good, code):
    rejected = [f for f in findings(raw, bad) if f.rule_id == code]
    assert rejected and all(f.blocking and f.source == raw and f.target == bad for f in rejected)
    assert not findings(raw, good)


@pytest.mark.parametrize('raw,bad,good,code', DETAIL_CASES)
def test_other_attribute_or_context_never_authorizes_bad_role(raw, bad, good, code):
    rejected = [f for f in findings(raw + '; Otro: Sí', bad + '；备注：' + good,
        desc_es=good, spec_es=raw) if f.rule_id == code]
    assert len(rejected) == 1 and rejected[0].evidence['pair_index'] == 0
    # The same bad words in an unrelated, corresponding free-text attribute
    # do not manufacture a role error for a correctly translated first pair.
    assert not [f for f in findings(raw + '; Otro: Sí', good + '；备注：' + bad) if f.rule_id == code]


def test_duplicate_attributes_compare_each_own_occurrence_without_cross_matching():
    raw = 'Apto para tipo de dentadura: Dentadura de adultos; Apto para tipo de dentadura: Dentadura postiza'
    good = '适用牙齿类型：成人牙齿；适用牙齿类型：假牙'
    assert not findings(raw, good)
    bad = '适用牙齿类型：假牙；适用牙齿类型：成人牙齿'
    detected = [f for f in findings(raw, bad) if f.rule_id == 'DENTITION_CHANGED_TO_DENTURE']
    assert len(detected) == 1 and detected[0].evidence['pair_index'] == 0


@pytest.mark.parametrize('label', ['低过敏', '低敏', '不易致敏'])
def test_hypoallergenic_faithful_variants_are_not_forced_to_one_word(label):
    assert not findings('Hipoalergénico: Sí', label + '：是')


@pytest.mark.parametrize('field,raw,bad,good,code', [
    ('description', 'Fabricado en poliéster.', '采用聚酰胺制成。', '采用聚酯纤维制成。', 'POLYESTER_CHANGED_TO_POLYAMIDE'),
    ('name', 'Bolsas para lavadora', '袜子', '洗衣袋', 'PRODUCT_WORD_SENSE_CHANGED'),
    ('name', 'Paneles de pintura', '清洁布', '绘画板', 'PRODUCT_WORD_SENSE_CHANGED'),
    ('name', 'Caballete de corte', '画架', '切割支架', 'PRODUCT_WORD_SENSE_CHANGED'),
    ('name', 'Filetes de pollo', '鱼片', '鸡肉片', 'PRODUCT_WORD_SENSE_CHANGED'),
])
def test_uncovered_own_field_word_senses_and_faithful_controls(field, raw, bad, good, code):
    assert any(f.rule_id == code and f.blocking for f in findings(raw, bad, field))
    assert not findings(raw, good, field)
    # Another source field cannot authorize the wrong term/model.
    assert any(f.rule_id == code for f in findings(raw, bad, field, spec_es='AA poliamida pescado calcetines'))


@pytest.mark.parametrize('raw,target', [
    ('Materiales: poliéster y poliamida.', '材质为聚酯纤维和聚酰胺。'),
    ('Funciona con pilas AAA y AA.', '使用AAA和AA电池。'),
])
def test_same_field_explicit_alternatives_do_not_create_fabricated_contradictions(raw, target):
    assert not findings(raw, target, 'description')


@pytest.mark.parametrize('raw,target', [
    ('Halal: Certificado halal', '清真认证：已认证'),
    ('Halal: Certificación halal', '清真认证：已认证'),
    ('Tipo de taza / vaso: Vaso de vidrio', '杯具类型：玻璃杯'),
    ('Tipo de taza / vaso: Vaso de cristal', '杯具类型：玻璃杯'),
    ('Tipo de dispositivo de corrección: Aparato de ortodoncia', '矫正器械类型：正畸矫治器'),
    ('Vegano: Sí', '严格素食：是'),
])
def test_explicit_own_value_and_faithful_synonyms_do_not_create_false_blocks(raw, target):
    assert not findings(raw, target)


@pytest.mark.parametrize('raw,target,extra,code', [
    ('Halal: Sí', '清真认证：是', 'Certificación: Certificado halal', 'CERTIFICATION_QUALIFIER_ADDED'),
    ('Tipo de taza / vaso: Vaso (no desechable)', '杯具类型：玻璃杯（非一次性）', 'Otro: Vidrio', 'CUP_TYPE_CHANGED_TO_MATERIAL'),
    ('Tipo de dispositivo de corrección: Goma', '矫正器械类型：橡胶', 'Otro: Aparato de ortodoncia', 'STATIONERY_CHANGED_TO_MEDICAL_DEVICE'),
])
def test_other_attribute_value_cannot_authorize_material_medical_or_certification_claim(raw, target, extra, code):
    assert any(f.rule_id == code for f in findings(raw + '; ' + extra, target + '；其他：说明'))


def test_battery_model_change_reuses_existing_protected_token_guards():
    rejected = findings('Funciona con pilas AAA.', '使用AA电池。', 'description')
    assert any(f.rule_id == 'PROTECTED_TOKEN_CHANGED' and f.blocking for f in rejected)
    assert not findings('Funciona con pilas AAA.', '使用AAA电池。', 'description')


def test_negated_own_glass_value_cannot_authorize_glass_material_claim():
    assert any(f.rule_id == 'CUP_TYPE_CHANGED_TO_MATERIAL' for f in findings(
        'Tipo de taza / vaso: Vaso sin vidrio', '杯具类型：玻璃杯'))


def test_new_role_checks_do_not_bypass_existing_numeric_model_or_name_brand_guards():
    assert any(f.blocking for f in findings('Contenido: 1560 g', '净含量：1500克'))
    assert any(f.blocking for f in findings('USB-C 20 W', '充电器', 'name'))
    assert any(f.blocking for f in findings('Disney taza', 'Disney杯', 'name'))
    assert not findings('Marca: Disney', '品牌：Disney')
