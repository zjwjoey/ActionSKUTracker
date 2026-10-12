import pytest

from action_tracker.localization.body_names import mask_source_bound_body_names
from action_tracker.localization.contracts import SourceFacts, SemanticFact
from action_tracker.localization.qa import guard_translation, _numbers
from action_tracker.localization.release_gate import _has_release_spanish_residual


@pytest.mark.parametrize("name", ["Finall Oxi Active Power Gel Multi-Color", "Accelerate Energy Gel", "El Grinch"])
def test_complete_own_source_body_identity_is_not_spanish_residual(name):
    source = SourceFacts(sku="1001", desc_es=f"Producto {name}")
    assert "SPANISH_RESIDUAL" not in {f["rule_id"] for f in guard_translation(source, {"description": f"产品{name}"}, ("description",))["findings"]}
    assert not _has_release_spanish_residual(mask_source_bound_body_names(source.desc_es, f"产品{name}"), allowed_tokens=set())


def test_body_name_does_not_whitelist_individual_words_or_other_sources():
    assert mask_source_bound_body_names("El Grinch", "El Grinch con material") == "  con material"
    assert _has_release_spanish_residual(mask_source_bound_body_names("El Grinch", "El Grinch con material"), allowed_tokens=set())
    assert mask_source_bound_body_names("Producto", "El Grinch") == "El Grinch"
    assert mask_source_bound_body_names("Finall Oxi Active Power Gel Multi-Color", "Color") == "Color"
    assert "SPANISH_RESIDUAL" in {f["rule_id"] for f in guard_translation(SourceFacts(sku="1001", name_es="El Grinch", desc_es="Producto"), {"description": "El Grinch"}, ("description",))["findings"]}


def test_spanish_decimal_comma_measurement_is_not_a_thousand():
    assert _numbers("2 unidades de 1,500 litros y 1 de 5,1 litros") == _numbers("2个1.5升和1个5.1升")
    assert _numbers("1.500 litros") != _numbers("1.5升")
    assert _numbers("1,500 piezas") != _numbers("1.5件")
    assert _numbers("1,500 litros") != _numbers("1500升")
    source = SourceFacts(sku="1001", desc_es="2 unidades de 1,500 litros y 1 de 5,1 litros")
    good = guard_translation(source, {"description": "2个1.5升和1个5.1升"}, ("description",))
    assert good["status"] == "PASS"
    bad = guard_translation(source, {"description": "2个1500升和1个5.1升"}, ("description",))
    assert bad["status"] == "FAIL"


def test_model_identity_is_not_brand_or_arbitrary_prose():
    source = SourceFacts(sku="1001", name_es="Turbo Fire Dartblaster XSHOT")
    facts = (SemanticFact("BRAND", "XSHOT", "XSHOT", source_field="name_es"),
             SemanticFact("MODEL", "Turbo Fire", "Turbo Fire", source_field="name_es"))
    assert guard_translation(source, {"name": "Turbo Fire软弹枪"}, ("name",), semantic_facts=facts)["status"] == "PASS"
    assert guard_translation(source, {"name": "Turbo Fire软弹枪 con"}, ("name",), semantic_facts=facts)["status"] == "FAIL"
    assert guard_translation(source, {"name": "Turbo Fire软弹枪"}, ("name",))["status"] == "FAIL"


def test_invalid_physical_state_in_source_list_is_blocked():
    source = SourceFacts(sku="1001", details_es="Sustancia: Válido, Loción")
    result = guard_translation(source, {"details": "产品形态：凝胶、乳液"}, ("details",))
    assert "SOURCE_DETAILS_TYPED_VALUE_INVALID" in {r["rule_id"] for r in result["findings"]}
