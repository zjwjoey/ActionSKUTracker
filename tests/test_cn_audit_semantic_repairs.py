"""Real field-source regressions and negative controls; no runtime I/O."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.services.hashing import localization_field_source_hash, localization_source_hash


FIXTURES = json.loads((Path(__file__).parent / "fixtures" /
                      "cn_audit_semantic_repairs_20261027.json").read_text(encoding="utf-8"))


def check(record, target, field="description"):
    source = SourceFacts.from_record(record)
    return guard_translation(source, {field: target}, (field,),
                             semantic_facts=parse_semantic_facts(source, dictionaries={
                                 "terms": [{"term_es": "madera", "term_zh": "木质", "term_type": "MATERIAL"}],
                             }))


@pytest.mark.parametrize("case", FIXTURES, ids=lambda c: c["sku"] + "-" + c["field"])
def test_real_source_context_semantics(case):
    assert localization_source_hash(case["source"]) == case["source_hash"]
    assert localization_field_source_hash(case["source"], case["field"]) == case["field_source_hash"]
    target = case["target"]
    if case["sku"] == "3226213" and case["field"] == "description":
        # Preserve the two named options without an additional numeric claim.
        target = target.replace('有“经典款”与“白麝香”两款可供选择。',
                                '可选The Original和White Musk香型。')
    result = check(case["source"], target, case["field"])
    assert result["overall_ready"], result["findings"]


@pytest.mark.parametrize("target", ["洗洁精", "洗衣粉", "洗涤剂", "不是洗衣液", "洗衣液/洗洁精"])
def test_laundry_liquid_still_rejects_wrong_product_or_negation(target):
    record = {"name_es": "Detergente Ejemplo", "desc_es": "Detergente líquido para ropa."}
    assert not check(record, target, "name")["overall_ready"]


@pytest.mark.parametrize("record", [
    {"name_es": "Detergente"},
    {"name_es": "Detergente", "cat2_es": "Detergentes y lavavajillas"},
    {"name_es": "Detergente líquido para platos"},
    {"name_es": "Detergente líquido para suelos", "desc_es": "Para ropa"},
    {"name_es": "Detergente en polvo para ropa", "desc_es": "Detergente líquido"},
    {"name_es": "Detergente sólido para ropa", "desc_es": "Detergente líquido"},
    {"name_es": "Detergente líquido", "desc_es": "No usar para ropa"},
    {"name_es": "Bolsa para detergente líquido para ropa"},
    {"name_es": "Detergente líquido para ropa", "desc_es": "Detergente"},
])
def test_liquid_context_does_not_guess_purpose_form_or_accessory(record):
    field = "description" if record.get("desc_es") == "Detergente" else "name"
    assert not check(record, "洗衣液", field)["overall_ready"]


@pytest.mark.parametrize("record,target", [
    ({"desc_es": "Papel sin madera"}, "木质纸"),
    ({"desc_es": "Papel sin madera"}, "不是化学浆纸"),
    ({"desc_es": "Papel sin madera; material de madera"}, "化学浆纸"),
    ({"name_es": "Papel sin madera", "desc_es": "Hecho de madera"}, "化学浆纸"),
    ({"desc_es": "Cinceles de madera"}, "木工凿"),
    ({"desc_es": "Con hoja de acero y mango de plástico. Cinceles de madera. Caja de madera."}, "钢刃塑料手柄木工凿"),
])
def test_qualified_wood_does_not_erase_separate_material_or_borrow_context(record, target):
    assert not check(record, target)["overall_ready"]


def test_eraser_context_preserves_separately_asserted_rubber():
    source = {"desc_es": "Tinta borrable. Bolígrafo con goma para corregir. Mango de goma."}
    assert not check(source, "可擦墨水，附橡皮用于修改文字。")["overall_ready"]
    assert check(source, "可擦墨水，附橡皮用于修改文字，橡胶手柄。")["overall_ready"]


@pytest.mark.parametrize("source,target", [
    ("Con iluminación RGB", "不带RGB灯效"),
    ("Iluminación", "RGB灯效"),
    ("Con iluminación RGB", "RGB支架"),
    ("Para una iluminación brillante", "明亮照明"),
])
def test_illumination_alias_requires_own_complete_phrase(source, target):
    assert not check({"desc_es": source}, target)["overall_ready"]


def test_numeric_and_interface_guards_still_block_dropped_facts():
    source = {"desc_es": "Con iluminación RGB y 2 puertos USB-C"}
    assert check(source, "带RGB灯效和2个USB-C接口")["overall_ready"]
    assert not check(source, "带RGB灯效")["overall_ready"]

