"""Real pending description and adversarial own-field/body-only controls."""
import pytest

from action_tracker.localization.body_names import mask_source_bound_body_names
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.release_gate import _has_release_spanish_residual

SOURCE = (
    "Haz tu propio peluche\nUna sorpresa en cada caja\nColecciona las 6 figuras\n"
    "Haz tú mismo un bonito mokoji con este juego completo de ganchillo. "
    "El juego incluye hilo, relleno, aguja de ganchillo, aguja, colgante de plástico e instrucciones."
)
TARGET = (
    "亲手制作你的毛绒玩具\n每个盒子里都有惊喜\n收集全部6款玩偶\n"
    "用这套完整的钩针编织套装，亲手制作一个可爱的mokoji。"
    "套装包含纱线、填充物、钩针、针、塑料挂件和说明书。"
)


def test_real_mokoji_description_keeps_identity_and_passes_both_residual_checks():
    source = SourceFacts(sku="3224847", name_es="Set de ganchillo Mokoji", desc_es=SOURCE)
    assert guard_translation(source, {"description": TARGET}, ("description",))["status"] == "PASS"
    assert not _has_release_spanish_residual(mask_source_bound_body_names(SOURCE, TARGET), allowed_tokens=set())


@pytest.mark.parametrize("field,attr", [("description", "desc_es"), ("details", "details_es")])
def test_identity_is_retained_only_in_its_own_body_source(field, attr):
    source = SourceFacts(sku="1001", **{attr: "mokoji"})
    assert guard_translation(source, {field: "mokoji"}, (field,))["status"] == "PASS"
    absent = SourceFacts(sku="1001", name_es="mokoji", **{attr: "Producto"})
    assert "SPANISH_RESIDUAL" in {r["rule_id"] for r in guard_translation(absent, {field: "mokoji"}, (field,))["findings"]}


@pytest.mark.parametrize("source,target", [
    ("mokojito", "mokoji"), ("mokoji", "mokojito"),
    ("mokoji", "mokoji con material"), ("mokoji", "mokoji incluye"),
])
def test_prefixes_and_untranslated_prose_are_not_exempt(source, target):
    masked = mask_source_bound_body_names(source, target)
    if source == "mokojito" or target == "mokojito":
        assert masked == target
    else:
        assert _has_release_spanish_residual(masked, allowed_tokens=set())
    result = guard_translation(SourceFacts(sku="1001", desc_es=source), {"description": target}, ("description",))
    assert "SPANISH_RESIDUAL" in {r["rule_id"] for r in result["findings"]}


@pytest.mark.parametrize("field,attr", [("name", "name_es"), ("spec", "spec_es")])
def test_body_identity_does_not_change_short_field_residual_rules(field, attr):
    source = SourceFacts(sku="1001", **{attr: "mokoji"})
    assert "SPANISH_RESIDUAL" in {r["rule_id"] for r in guard_translation(source, {field: "mokoji"}, (field,))["findings"]}


def test_numeric_and_unit_guards_still_reject_wrong_collectible_count():
    source = SourceFacts(sku="3224847", desc_es=SOURCE)
    result = guard_translation(source, {"description": TARGET.replace("6款", "8款")}, ("description",))
    assert result["status"] == "FAIL"
    assert any("NUMERIC" in r["rule_id"] for r in result["findings"])
