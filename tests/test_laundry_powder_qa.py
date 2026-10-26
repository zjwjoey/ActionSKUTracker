"""Real 3227276 regression and field/use/form boundaries; offline only."""
import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts


REAL_SOURCE = {
    "sku": "3227276",
    "name_es": "Detergente en polvo easy Tiger Lily & Lotus Flower",
    "cat1_es": "Hogar",
    "cat2_es": "Detergentes y lavavajillas",
    "spec_es": "130 lavados | 6,5 kilos",
    "desc_es": "Para ropa blanca y de color\nFórmula 4 en 1\nLava la ropa blanca y de color con el detergente en polvo Easy Tiger Lily & Lotus Flower. La fórmula 4 en 1 ayuda a eliminar las manchas, cuida las fibras y garantiza un frescor duradero.",
    "details_es": "Apto para tipo de aparato: Lavadora; Apto para tipo de colada: Lavado a mano y lavado a máquina; Apto para tipo de colada a color: Colada blanca, Colada a color; Concentrado: Sí; Contenido: 6.5 kg; Función de limpieza: Sí; Número de turnos de limpieza: 130; Sin perfume: No; Sustancia: Polvo; Tipo de dispensador: Caja; Uso previsto: Lavadora; Número del artículo: 3227276",
}
TARGET = "虎百合与莲花香洗衣粉"


def check(record, target, field="name"):
    source = SourceFacts.from_record(record)
    return guard_translation(source, {field: target}, (field,),
                             semantic_facts=parse_semantic_facts(source))


def test_real_3227276_powder_is_not_dishwashing_liquid():
    assert check(REAL_SOURCE, TARGET)["overall_ready"]


@pytest.mark.parametrize("record", [
    {"name_es": "Detergente en polvo para ropa"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en polvo", "details_es": "Apto para tipo de aparato: Lavadora"},
    {"name_es": "Detergente en polvo", "desc_es": "Para ropa blanca y de color"},
    {"name_es": "Detergente en polvo sin perfume para ropa"},
])
def test_explicit_laundry_purpose_supports_powder_name(record):
    assert check(record, "洗衣粉")["overall_ready"]


@pytest.mark.parametrize("target", ["洗洁精", "洗衣液", "洗衣凝珠", "清洁剂", "不是洗衣粉", "洗衣粉/洗洁精"])
def test_clear_powder_source_still_rejects_wrong_form_use_or_negation(target):
    result = check(REAL_SOURCE, target)
    assert not result["overall_ready"]
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


@pytest.mark.parametrize("record", [
    {"name_es": "Detergente en polvo"},
    {"name_es": "Detergente", "desc_es": "Detergente en polvo para ropa"},
    {"name_es": "Detergente líquido", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en cápsulas color", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en polvo para lavavajillas", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en polvo", "cat2_es": "Detergentes y lavavajillas"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: No para lavadora"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora", "desc_es": "No usar en lavadora"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora", "desc_es": "No es apto para ropa"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora", "desc_es": "No es para ropa"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora", "desc_es": "No recomendado para lavar la ropa"},
    {"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora", "desc_es": "No se debe usar en la lavadora"},
    {"name_es": "No se trata de detergente en polvo", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "No es detergente en polvo para ropa"},
    {"name_es": "Dosificador para detergente en polvo", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en polvo y detergente líquido", "details_es": "Uso previsto: Lavadora"},
    {"name_es": "Detergente en polvo para alfombras", "details_es": "Uso previsto: Lavadora"},
])
def test_powder_alias_does_not_import_form_or_guess_conflicting_purpose(record):
    assert not check(record, "洗衣粉")["overall_ready"]


@pytest.mark.parametrize("record,target", [
    ({"name_es": "Detergente para platos"}, "洗洁精"),
    ({"name_es": "Detergente"}, "洗涤剂"),
    ({"name_es": "Detergente líquido para vajilla"}, "洗洁精"),
    ({"name_es": "Detergente en cápsulas color"}, "洗衣凝珠"),
    ({"name_es": "Detergente en cápsulas para lavavajillas color"}, "洗涤凝珠"),
])
def test_existing_other_detergent_judgments_are_unchanged(record, target):
    assert check(record, target)["overall_ready"]


def test_numeric_and_model_protection_remain_required():
    record = {"name_es": "Detergente en polvo 2 unidades X-17", "details_es": "Uso previsto: Lavadora"}
    assert check(record, "洗衣粉 2件 X-17")["overall_ready"]
    assert not check(record, "洗衣粉")["overall_ready"]
    assert not check(record, "洗衣粉 3件 X-18")["overall_ready"]


def test_description_needs_its_own_complete_powder_phrase():
    assert check({"desc_es": "Detergente en polvo para ropa"}, "洗衣粉", "description")["overall_ready"]
    assert not check({"name_es": "Detergente en polvo para ropa", "desc_es": "Detergente"},
                     "洗衣粉", "description")["overall_ready"]


@pytest.mark.parametrize("name", [
    "Bolsa para detergente en polvo para ropa",
    "Soporte para detergente en polvo para ropa",
    "Tapa para detergente en polvo para ropa",
    "Bolsa para el detergente en polvo",
    "Tapa de detergente en polvo",
    "Soporte del detergente en polvo",
    "Organizador para tu detergente en polvo",
    "Para detergente en polvo",
])
def test_dependent_powder_noun_cannot_replace_accessory_identity(name):
    record = {"name_es": name, "details_es": "Uso previsto: Lavadora"}
    assert not check(record, "洗衣粉")["overall_ready"]


@pytest.mark.parametrize("record,field", [
    ({"name_es": "Detergente en polvo para ropa"}, "name"),
    ({"name_es": "Detergente en polvo", "details_es": "Uso previsto: Lavadora"}, "name"),
    ({"desc_es": "Lava la ropa con el detergente en polvo."}, "description"),
])
def test_product_identity_is_not_rejected_by_its_own_purpose_or_use(record, field):
    assert check(record, "洗衣粉", field)["overall_ready"]
