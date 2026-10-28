"""CI_SAFE: source-bound regressions from candidate609, no production I/O."""
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import audit_translation, _detail_contextual_translation_findings
from action_tracker.localization.normalization.structured_details import parse_structured_details


def codes(es, zh, field='name'):
    key={'name':'name_es','details':'details_es','description':'desc_es','spec':'spec_es'}[field]
    return {f.rule_id for f in audit_translation(SourceFacts.from_record({key:es}),{field:zh},(field,))}


@pytest.mark.parametrize('es,good', [
    ('Bebida proteica XXL Nutrition Chocolate','巧克力味蛋白饮料'),
    ('Chips de proteína XXL Nutrition Thai Sweet Chili','泰式甜辣味蛋白脆片'),
    ('Barrita energética de turrón XXL Nutrition Salty Almond','咸杏仁牛轧糖能量棒'),
])
def test_actual_brand_is_not_restored_as_size(es,good):
    assert not codes(es,good)
    assert 'BRAND_SIZE_TOKEN_LEAK' in codes(es,good+'｜XXL')


def test_brand_role_does_not_erase_genuine_size_or_other_fields():
    assert 'PROTECTED_TOKEN_MISSING' in codes('Manta XXL Studio Home','毛毯')
    assert not codes('Manta XXL Studio Home','XXL毛毯')
    assert not codes('Camiseta XXL Nutrition talla XXL','XXL上衣')
    assert 'PROTECTED_TOKEN_MISSING' in codes('Camiseta XXL Nutrition talla XXL','上衣')
    assert 'PROTECTED_TOKEN_MISSING' in codes('Bebida XXL Nutrition','饮料','description')
    assert 'PROTECTED_TOKEN_MISSING' in codes('Bebida XXL Nutrition USB-C','饮料')


@pytest.mark.parametrize('field', ['name','description','details','spec'])
def test_percale_is_not_poplin_and_does_not_borrow_other_field(field):
    assert 'WEAVE_MISTRANSLATION' in codes('Algodón percal','棉质府绸',field)
    assert 'WEAVE_MISTRANSLATION' not in codes('Algodón percal','棉质密织平纹',field)
    assert 'WEAVE_MISTRANSLATION' not in codes('Algodón popelín','棉质府绸',field)
    assert 'WEAVE_MISTRANSLATION' not in codes('Comparar percal y popelín','比较平纹织物与府绸',field)


@pytest.mark.parametrize('es,bad,good', [
    ('Relleno de página: Con líneas','页面填充：条纹','页面填充：横线'),
    ('Relleno de página: Blanco','页面内容：白色','页面内容：空白'),
    ('Sustancia: Bloques','形态：积木','形态：块状'),
    ('Uso previsto: Taza del váter','用途：马桶座圈','用途：马桶便池'),
    ('Repelen el agua: Sí','防水：是','防泼水：是'),
    ('Repelen el agua: No','防水性能：否','防泼水性能：否'),
    ('Método de administración: Oral; Sustancia: Goma; Uso previsto como: Suplemento nutricional',
     '使用方式：口服；形态：橡胶；预期用途：营养补充剂','使用方式：口服；形态：软糖；预期用途：营养补充剂'),
])
def test_known_attribute_errors_are_flagged_not_rewritten(es,bad,good):
    assert _detail_contextual_translation_findings(es,bad)
    assert not _detail_contextual_translation_findings(es,good)


@pytest.mark.parametrize('es,zh', [
    ('Color: Blanco','颜色：白色'),('Diseño: Con líneas','图案：条纹'),
    ('Material: Goma','材质：橡胶'),('Sustancia: Goma','形态：橡胶'),
    ('Uso previsto: Asiento del inodoro','用途：马桶座圈'),
    ('Impermeable: Sí','防水：是'),('Tipo de juguete: Bloques','玩具类型：积木'),
])
def test_same_words_in_other_attributes_are_not_false_blocks(es,zh):
    assert not _detail_contextual_translation_findings(es,zh)


def test_real_3222178_missing_colons_recover_all_labels_in_order():
    es=('Cantidad: 15 unidades; Carbohidratos 9 g; Consejos sobre conservación Conservar en un lugar fresco y seco; '
        'Contenido: 135 g; De los cuales azúcares 5.9 g; Energía 36 kcal; Halal Sí; '
        'Sabor Fresa, Naranja, Limón; Sin azúcar No; Sin gluten Sí; Tipo de caramelo Chupachups; '
        'Vegano Sí; Número del artículo: 3222178')
    pairs=parse_structured_details(es)
    assert len(pairs)==13
    assert pairs[7].key=='Sabor' and pairs[7].value=='Fresa, Naranja, Limón'
    assert pairs[6].key=='Halal' and pairs[6].value=='Sí'
    assert _detail_contextual_translation_findings(es,'口味：草莓、橙色、柠檬')
    assert not _detail_contextual_translation_findings(es,'口味：草莓、橙子、柠檬')
    assert len(parse_structured_details('Sabor Naranja; Sabor Fresa'))==2
    assert not parse_structured_details('Etiqueta desconocida valor; Energía para jugar; Sin gluten quizás')


def test_repeated_attributes_are_compared_by_occurrence_not_cross_joined():
    es='Uso previsto: Taza del váter; Uso previsto: Asiento del inodoro'
    assert not _detail_contextual_translation_findings(es,'用途：马桶便池；用途：马桶座圈')
    assert _detail_contextual_translation_findings(es,'用途：马桶座圈；用途：马桶便池')


def test_unaligned_duplicate_counts_do_not_create_a_qa_exemption():
    es='Uso previsto: Taza del váter; Uso previsto: Asiento del inodoro'
    assert _detail_contextual_translation_findings(es,'用途：马桶座圈')
