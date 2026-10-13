"""CI_SAFE: own-field semantic roles and export model boundaries."""
import pytest
from action_tracker.localization.fidelity import (
    field_semantic_issues, repair_reviewed_detail_roles, strip_name_identity_aliases,
)
from action_tracker.exporting.repair_tokens import source_model_tokens
from action_tracker.exporting.repair_rules import repair_content_with_context
from action_tracker.exporting.repair_rules import repair_title_with_context


@pytest.mark.parametrize('source,bad', [
    ('Apto para tipo de dentadura: Dentadura de adultos', '适用假牙类型：成人假牙'),
    ('Sin colorantes artificiales: No', '是否含人工色素：否'),
    ('Sin conservantes: No', '是否含防腐剂：否'),
    ('Número de posiciones de sentado: 1', '座位数：1'),
    ('Tipo de dispositivo de corrección: Goma', '矫正器械类型：橡胶'),
    ('Hipoalergénico: Sí', '抗过敏：是'),
    ('Vegano: No', '素食：否'),
    ('Tipo de taza / vaso: Vaso (no desechable)', '杯/玻璃类型：玻璃杯（非一次性）'),
    ('Modo de transporte: En el oído sin diadema', '携带方式：入耳式无头戴'),
    ('Apto para tipo de fuente de calor: Eléctrico', '适用热源：电动'),
    ('Contenido: 1560 g', '容量：1560克'),
    ('Halal: Sí', '清真认证：是'),
])
def test_wrong_attribute_role_is_flagged_then_source_bound_repair_is_stable(source, bad):
    assert field_semantic_issues(source, bad, 'details')
    fixed = repair_reviewed_detail_roles(source, bad)
    assert not field_semantic_issues(source, fixed, 'details')
    assert repair_reviewed_detail_roles(source, fixed) == fixed


def test_negative_predicate_no_does_not_infer_positive_presence():
    assert repair_reviewed_detail_roles('Sin conservantes: No', '是否含防腐剂：否') == '不含防腐剂：否'
    assert not field_semantic_issues('Sin conservantes: No', '不含防腐剂：否', 'details')


def test_duplicate_source_attributes_keep_order_and_distinct_meanings():
    fixed = repair_reviewed_detail_roles('Antialérgico: Sí; Hipoalergénico: Sí', '抗过敏：是；抗过敏：是')
    assert fixed == '抗过敏：是；低致敏：是'
    with pytest.raises(ValueError, match='ALIGNMENT'):
        repair_reviewed_detail_roles('Vegano: Sí; Color: Verde', '纯素：是')


@pytest.mark.parametrize('source,target', [
    ('Bolsas para lavadora', '袜子'), ('Lienzos de pintura', '清洁布'),
    ('Caballete de corte', '画架'), ('Filetes Pollo y pato', '鸡鸭鱼片'),
])
def test_product_word_sense_does_not_pass_nonempty_guard(source, target):
    assert field_semantic_issues(source, target, 'name')


def test_gsm_and_composition_on_separate_lines_are_not_a_model():
    source = 'Con diseño de rombos\n500 GSM\n100 % algodón'
    target = '菱格纹设计；克重500克/平方米；100%棉'
    assert source_model_tokens(source) == ()
    assert source_model_tokens('500 GSM 100 % algodón') == ()
    assert source_model_tokens('PZ 2; A4; IP44') == ('PZ2', 'A4', 'IP44')
    assert repair_content_with_context(target, source) == target


def test_brand_spellings_and_chinese_aliases_are_source_local():
    assert strip_name_identity_aliases('Cámara Nor-Tec', '诺特克摄像头', {'Nortec'}) == '摄像头'
    assert strip_name_identity_aliases('Champú Head & Shoulders', '海飞丝洗发水', {'Head & Shoulders'}) == '洗发水'
    assert strip_name_identity_aliases('Vaso', '狮子造型杯', {'Lion'}) == '狮子造型杯'


def test_capacity_and_positive_boolean_roles_remain_valid():
    assert not field_semantic_issues('Contenido: 200 ml; Contiene conservantes: No', '容量：200毫升；含防腐剂：否', 'details')


def test_adding_correct_model_at_end_does_not_hide_wrong_model_in_sentence():
    assert field_semantic_issues('4 pilas AAA', '4节AA电池；AAA', 'description')
    assert not field_semantic_issues('4 pilas AAA', '4节AAA电池', 'description')
    assert field_semantic_issues('Hechos de algodón y poliéster', '棉和聚酰胺', 'description')


def test_function_count_is_not_a_brand_numeric_fragment():
    assert repair_title_with_context('3合1保鲜膜', 'Film transparente Dumil 3-in-1', excluded_tokens={'Dumil'}) == '3合1保鲜膜'
    assert repair_title_with_context('保鲜膜 Dumil 3-in-1', 'Film transparente Dumil 3-in-1', excluded_tokens={'Dumil'}) == '保鲜膜 3-in-1'


def test_new_model_conflict_counts_as_p0_without_changing_release_threshold():
    from action_tracker.exporting.repair_report import audit_repaired_rows
    record = dict(sku='1001', name_es='Pilas', cat1_es='', cat2_es='',
                  spec_es='', desc_es='4 pilas AAA', details_es='')
    row = {'编号': '1001', '标题': '电池', '分类1': '', '分类2': '',
           '规格': '', '描述': '4节AA电池；AAA', '产品详情': ''}
    result = audit_repaired_rows([record], [row])
    assert result['p0_findings'] >= 1
    assert result['affected_fields'] == 1
    assert result['release_ready'] is False
