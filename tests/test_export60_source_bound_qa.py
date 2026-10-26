"""CI_SAFE: own-field source facts and deterministic export guards, no IO."""
import pytest

from action_tracker.localization.body_names import mask_source_bound_body_names
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.exporting.repair_rules import (
    repair_content_with_context, repair_details_with_context,
    repair_title_with_context,
)


def rules(source, target):
    qa = guard_translation(SourceFacts(sku="test", details_es=source),
                           {"details": target}, ("details",))
    return {finding["rule_id"] for finding in qa["findings"]}


@pytest.mark.parametrize("key", ["是否香薰", "是否香氛", "香薰", "香氛"])
@pytest.mark.parametrize("es,zh,wrong", [("Sí", "是", "否"), ("No", "否", "是")])
def test_scent_boolean_alias_preserves_polarity(key, es, zh, wrong):
    assert not rules(f"Perfumado: {es}", f"{key}：{zh}")
    assert "DETAIL_BOOLEAN_POLARITY_CHANGED" in rules(f"Perfumado: {es}", f"{key}：{wrong}")
    assert "DETAIL_BOOLEAN_FIELD_MISSING" in rules(f"Perfumado: {es}", "颜色：白色")


def test_scent_alias_does_not_absorb_other_features_or_perfume_free_field():
    assert "DETAIL_BOOLEAN_FIELD_MISSING" in rules("Perfumado: Sí", "香薰机功能：是")
    assert not rules("Perfumado: Sí; Sin perfume: No", "是否香氛：是；无香型：否")
    assert "DETAIL_BOOLEAN_FIELD_MISSING" in rules("Perfumado: Sí; Sin perfume: No", "是否香氛：是")


@pytest.mark.parametrize("source", [
    "Para elegir entre Mickey Mouse, Stitch, Ángel\nSilueta de tu personaje favorito de Disney\nCon práctica ventosa",
    "Diversión activa jugando con tu perro\nPuedes elegir entre Ángel, Sonic y Kuromi\nPara perros de 10 a 25 kg",
])
def test_explicit_character_lists_disambiguate_angel_in_own_field(source):
    assert not mask_source_bound_body_names(source, "Ángel").strip()
    for malformed in (
        source.replace("Para elegir entre", "ñPara elegir entre").replace("Puedes elegir entre", "ñPuedes elegir entre"),
        source.replace("Disney", "Disneyñ").replace("Kuromi", "Kuromiñ"),
    ):
        assert mask_source_bound_body_names(malformed, "Ángel") == "Ángel"
    qa = guard_translation(SourceFacts(sku="x", name_es=source, desc_es="Adorno"),
                           {"description": "Ángel"}, ("description",))
    assert "SPANISH_RESIDUAL" in {f["rule_id"] for f in qa["findings"]}


@pytest.mark.parametrize("source", ["Ángel de Navidad", "Disney y Ángel", "Stitch y un ángel", "Puedes elegir entre Ángel y estrella"])
def test_ordinary_angel_stays_on_review_path(source):
    assert mask_source_bound_body_names(source, "Ángel") == "Ángel"


@pytest.mark.parametrize("source,target,excluded", [
    ("Capri-Sun mango y maracuyá", "Capri-Sun芒果百香果饮料", {"Sun"}),
    ("Finall Oxi Active Power Gel Multi-Color elimina las manchas", "Finall Oxi Active Power Gel Multi-Color去污", {"Finall"}),
    ("Silueta de tu personaje favorito de Disney", "喜爱的Disney角色轮廓", {"Disney"}),
])
def test_title_brand_policy_does_not_strip_own_body_identities(source, target, excluded):
    assert repair_content_with_context(target, source, excluded_tokens=excluded) == target


def test_details_brand_identity_preserved_and_title_no_brand_policy_unchanged():
    source = "Marca: Nutini; Número del artículo: 1"
    target = "品牌：Nutini；商品编号：1"
    assert repair_details_with_context(target, source, excluded_tokens={"Nutini"}) == target
    assert repair_title_with_context("饼干", "Galletas Nutini", excluded_tokens={"Nutini"}) == "饼干"
    # Retaining source-faithful bodies does not import a missing brand.
    assert "Nutini" not in repair_content_with_context("饼干", "Galletas", excluded_tokens={"Nutini"})


@pytest.mark.parametrize("identity", [
    "Play-Doh", "Sau'cee", "Oral-B", "A-Force", "Nor-Tec", "TP-link",
    "Gen-Zen Pore Refining", "Pom-Bär", "Coca-Cola", "Fisher-Price",
    "L.O.L. Surprise!", "Fanta",
])
def test_reviewed_punctuated_body_identity_is_complete_and_own_field(identity):
    source = f"Producto de {identity}."
    assert mask_source_bound_body_names(source, identity).strip() == ""
    assert mask_source_bound_body_names("Producto", identity) == identity
    assert mask_source_bound_body_names(source, "ñ" + identity).strip() == "ñ" + identity
    assert mask_source_bound_body_names("ñ" + identity, identity) == identity
    # Identity masking cannot waive unrelated Spanish prose or quantities.
    assert "sin perfume" in mask_source_bound_body_names(source, identity + " sin perfume")
    qa = guard_translation(SourceFacts(sku="x", desc_es=source + " 2 unidades"),
                           {"description": identity + "产品3件"}, ("description",))
    assert "NUMERIC_DROPPED" in {f["rule_id"] for f in qa["findings"]}


@pytest.mark.parametrize("identity,fragment", [
    ("Play-Doh", "Doh"), ("Sau'cee", "cee"), ("Nor-Tec", "Tec"),
    ("Coca-Cola", "Cola"), ("L.O.L. Surprise!", "Surprise"),
])
def test_identity_components_are_not_individually_whitelisted(identity, fragment):
    assert mask_source_bound_body_names(identity, fragment) == fragment
