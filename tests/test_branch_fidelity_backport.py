"""CI_SAFE: finite cases from d133dd6; no model, files or business writes."""
import pytest
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.localization.qa import guard_translation


def qa(source, target, **other):
    facts = SourceFacts(sku='1001', details_es=source, **other)
    return guard_translation(facts, {'details': target}, ('details',), semantic_facts=parse_semantic_facts(facts))


@pytest.mark.parametrize('source,target', [
    ('Color: Azul, Púrpura, Rojo, Blanco, Amarillo', '颜色：蓝色、红色、白色'),
    ('Color: Gris topo', '颜色：驼色'),
    ('Material: Poliéster, Dolomita', '材质：聚酯纤维、方解石'),
    ('Material: Polirresina, Plástico', '材质：塑料'),
    ('Material: del asa Silicona', '把手材质：玻璃'),
    ('Material: de la cubierta Vidrio', '材质：玻璃'),
    ('Material: Silicona; Material: Vidrio', '材质：玻璃；材质：硅胶'),
    ('Color: Azul; Color: Rojo', '颜色：红色；颜色：蓝色'),
    ('Material: Plástico; Material: del asa Silicona', '材质：塑料、硅胶'),
    ('Material: Polietileno', '材质：PET'),
])
def test_lost_or_wrong_own_attribute_is_blocked(source, target):
    result = qa(source, target)
    assert result['status'] == 'FAIL'
    assert any(x['rule_id'] == 'DETAIL_CONTEXT_MISTRANSLATION' for x in result['findings'])


@pytest.mark.parametrize('source,target', [
    ('Color: Azul, Púrpura, Rojo, Blanco, Amarillo', '颜色：蓝色、紫色、红色、白色、黄色'),
    ('Color: Gris topo', '颜色：灰褐色'),
    ('Color: Gris topo', '颜色：驼灰色'),
    ('Material: Poliéster, Dolomita', '材质：聚酯纤维、白云石'),
    ('Material: Polirresina, Plástico', '材质：树脂、塑料'),
    ('Material: del asa Silicona', '把手材质：硅胶'),
    ('Material: de la cubierta Vidrio', '盖子材质：玻璃'),
    ('Material: Silicona; Material: Vidrio', '材质：硅胶；材质：玻璃'),
    ('Color: Azul; Color: Rojo', '颜色：蓝色；颜色：红色'),
    ('Material: Madera MDF, Tereftalato de polietileno', '材质：MDF中密度纤维板、聚对苯二甲酸乙二醇酯'),
    ('Material: base Plástico; Material: Poliéster', '基材：塑料；材质：涤纶'),
    ('Material: Plástico', '塑料'),
    ('Material: Desconocido', '材质：未知'),
    ('Material: Polietileno', '材质：聚乙烯'),
    ('Material: del asa Silicona; Material: del mango Vidrio', '把手材质：硅胶；手柄材质：玻璃'),
    ('Material: de la lente Plástico; Material: estructura Plástico', '材质：镜片：塑料；材质：镜架：塑料'),
])
def test_equivalent_values_compounds_roles_and_duplicates_are_preserved(source, target):
    assert qa(source, target)['status'] == 'PASS'


def test_title_context_cannot_supply_material_or_color_to_details():
    assert qa('Tipo: Vaso', '类型：杯子', name_es='Vaso de vidrio azul')['status'] == 'PASS'
    assert qa('Color: Azul', '颜色：红色', name_es='Vaso rojo')['status'] == 'FAIL'


def test_detail_terms_are_not_imposed_on_other_fields():
    source = SourceFacts(sku='1001', desc_es='Aroma a madera de roble', details_es='Material: Vidrio')
    result = guard_translation(source, {'description': '橡木香气'}, ('description',), semantic_facts=())
    assert not any(x['rule_id'] == 'DETAIL_CONTEXT_MISTRANSLATION' for x in result['findings'])


def test_formal_export_audit_consumes_the_same_guard():
    from action_tracker.exporting.repair_report import audit_repaired_rows
    result = audit_repaired_rows([{'sku': '1001', 'details_es': 'Material: del asa Silicona'}],
        [{'编号': '1001', '产品详情': '把手材质：玻璃'}])
    assert any(x['code'] == 'DETAIL_CONTEXT_MISTRANSLATION' for x in result['findings'])
    assert result['release_ready'] is False and result['p0_findings'] > 0
