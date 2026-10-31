"""CI_SAFE: literal source-bound controls; no production, network or provider."""
import pytest

from action_tracker.localization.contracts import SemanticFact, SourceFacts
from action_tracker.localization.qa import audit_translation


def findings(source, target, field, fact, **context):
    attr = {"details": "details_es", "description": "desc_es", "name": "name_es"}[field]
    record = {"sku": "SOURCE-SENSE-CONTROL", "name_es": "Producto", **context, attr: source}
    return audit_translation(SourceFacts.from_record(record), {field: target}, (field,), semantic_facts=(fact,))


@pytest.mark.parametrize("target", ["连指手套", "并指手套"])
def test_glove_attribute_accepts_mitten_sense_not_bath(target):
    source = "Material: Poliéster; Tipo de guantes: Manoplas"
    fact = SemanticFact("PRODUCT_TYPE", "manoplas", "沐浴手套", source_field="details_es")
    assert not findings(source, f"材质：聚酯纤维；手套类型：{target}", "details", fact)
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        source, "材质：聚酯纤维；手套类型：沐浴手套", "details", fact))


def test_bath_mitt_does_not_inherit_glove_alias():
    source = "Tipo de guantes: Manoplas de baño"
    fact = SemanticFact("PRODUCT_TYPE", "manoplas", "沐浴手套", source_field="details_es")
    assert not findings(source, "手套类型：沐浴手套", "details", fact)
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        source, "手套类型：连指手套", "details", fact))


def test_winter_title_cannot_authorize_an_unqualified_other_field():
    fact = SemanticFact("PRODUCT_TYPE", "manoplas", "沐浴手套", source_field="details_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Manoplas", "连指手套", "details", fact, name_es="Guantes de invierno"))


def test_separate_manoplas_occurrence_is_not_consumed_by_attribute():
    fact = SemanticFact("PRODUCT_TYPE", "manoplas", "沐浴手套", source_field="details_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Tipo de guantes: Manoplas; Otro: Manoplas", "手套类型：连指手套；其他：连指手套", "details", fact))


@pytest.mark.parametrize("target", ["怡人的橡木香气", "怡人的橡木香调", "怡人的木质香调（橡木）"])
def test_oak_scent_does_not_require_a_wood_composition_claim(target):
    source = "Con un agradable aroma a madera de roble"
    fact = SemanticFact("MATERIAL", "madera", "木质", source_field="desc_es")
    assert not findings(source, target, "description", fact)


@pytest.mark.parametrize("target", ["怡人的松木香气", "怡人的香气", "木质产品"])
def test_oak_scent_still_requires_oak_scent_not_another_material(target):
    fact = SemanticFact("MATERIAL", "madera", "木质", source_field="desc_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Con un agradable aroma a madera de roble", target, "description", fact))


def test_oak_scent_alias_cannot_waive_separate_real_wood():
    fact = SemanticFact("MATERIAL", "madera", "木质", source_field="desc_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Material de madera y aroma a madera de roble", "橡木香气", "description", fact))


def test_existing_source_bound_brand_evidence_resolves_ob_without_global_whitelist():
    brand = SemanticFact("BRAND", "OB", "OB", source_field="name_es", evidence="reviewed official pack")
    assert not findings("Tampones OB Normal", "普通型卫生棉条", "name", brand)
    assert any(f.rule_id == "PROTECTED_TOKEN_MISSING" for f in findings(
        "Tampones OB Normal", "普通型卫生棉条", "name",
        SemanticFact("BRAND", "OB", "OB", source_field="desc_es")))


@pytest.mark.parametrize("target", ["米色厨房用布", "米色厨房巾", "米色厨房抹布", "米色厨房毛巾", "米色厨房清洁布"])
def test_kitchen_cloth_phrase_accepts_source_supported_cloth_terms(target):
    fact = SemanticFact("PRODUCT_TYPE", "paño", "清洁布", source_field="name_es")
    assert not findings("Paño de cocina Beige", target, "name", fact)


def test_kitchen_title_cannot_supply_missing_context_to_other_field():
    fact = SemanticFact("PRODUCT_TYPE", "paño", "清洁布", source_field="details_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Tipo: Paño", "类型：厨房巾", "details", fact, name_es="Paño de cocina"))


def test_kitchen_alias_retains_existing_canonical_guard_control():
    fact = SemanticFact("PRODUCT_TYPE", "paño", "清洁布", source_field="name_es")
    assert not findings("Paño de cocina", "清洁布", "name", fact)


def test_kitchen_phrase_does_not_waive_separate_generic_cloth():
    fact = SemanticFact("PRODUCT_TYPE", "paño", "清洁布", source_field="name_es")
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Paño de cocina y paño", "厨房巾", "name", fact))


def test_openwork_sock_compound_is_not_dropped_product_identity():
    fact = SemanticFact("PRODUCT_TYPE", "calcetines", "袜子", source_field="name_es")
    assert not findings("Calcetines calados para mujer", "女士镂空袜", "name", fact)
    assert any(f.rule_id == "SEMANTIC_FACT_DROPPED" for f in findings(
        "Calcetines", "女士镂空袜", "name", fact))
