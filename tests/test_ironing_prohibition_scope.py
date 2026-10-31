"""CI_SAFE: attribute-local care polarity, with real-source regression fragments."""
import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation


def care_rules(source, target, **context):
    result = guard_translation(SourceFacts.from_record({
        "sku": "2577090-CARE-CONTROL", "details_es": source, **context,
    }), {"details": target}, ("details",))
    return {finding["rule_id"] for finding in result["findings"]}


@pytest.mark.parametrize("target", [
    "不可熨烫", "禁止熨烫", "不能熨烫", "请勿熨烫", "不可以熨烫",
    "不得熨烫", "不熨烫", "请勿使用熨斗", "不可用熨斗熨烫", "否", "禁止",
])
def test_ironing_prohibition_keeps_explicit_prohibition(target):
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(
        "Instrucciones de planchado: Sin planchado", "熨烫说明：" + target)


@pytest.mark.parametrize("source", ["Sin planchado", "No planchar"])
@pytest.mark.parametrize("target", [
    "可熨烫", "可以熨烫", "允许熨烫", "熨烫", "无需熨烫", "免熨", "免烫",
    "不可熨烫，但可低温熨烫", "不建议熨烫",
])
def test_ironing_prohibition_cannot_be_dropped_weakened_or_contradicted(source, target):
    assert "CARE_INSTRUCTION_CHANGED" in care_rules(
        "Instrucciones de planchado: " + source,
        "熨烫说明：" + target)


def test_glove_source_drying_prohibition_cannot_cover_affirmative_ironing():
    source = ("Instrucciones de lavado: Lavado a máquina hasta 40 °C; "
              "Instrucciones de planchado: Sin planchado; "
              "Instrucciones de secado: No secar en tambor de secado")
    good = "洗涤说明：最高40°C机洗；熨烫说明：不可熨烫；干燥说明：不可滚筒烘干"
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(source, good)
    assert "CARE_INSTRUCTION_CHANGED" in care_rules(source, good.replace("不可熨烫", "可熨烫"))


def test_duplicate_ironing_values_keep_their_order_and_independent_meaning():
    source = "Instrucciones de planchado: Sin planchado; Instrucciones de planchado: Máximo 200 °C"
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(
        source, "熨烫说明：不可熨烫；熨烫说明：最高200°C熨烫")
    assert "CARE_INSTRUCTION_CHANGED" in care_rules(
        source, "熨烫说明：可熨烫；熨烫说明：最高200°C不可熨烫")
    assert "CARE_INSTRUCTION_CHANGED" in care_rules(source, "熨烫说明：最高200°C熨烫")


def test_positive_ironing_and_other_field_context_are_not_restricted():
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(
        "Instrucciones de planchado: Máximo 200 °C", "熨烫说明：最高200°C熨烫")
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(
        "Material: Algodón", "材质：棉", desc_es="No planchar")


@pytest.mark.parametrize("included", ["Sí", "No"])
def test_ironing_strip_accessory_is_not_an_ironing_instruction(included):
    source = f"Incluye tira de planchado: {included}; Instrucciones de planchado: Sin planchado"
    value = "是" if included == "Sí" else "否"
    target = f"含熨烫条：{value}；熨烫说明：不可熨烫"
    assert "CARE_INSTRUCTION_CHANGED" not in care_rules(source, target)
    assert "CARE_INSTRUCTION_CHANGED" in care_rules(source, target.replace("不可熨烫", "可熨烫"))


@pytest.mark.parametrize("key,value,blocked", [
    ("是否可熨烫", "否", False), ("是否可熨烫", "是", True),
    ("禁止熨烫", "是", False), ("禁止熨烫", "否", True),
    ("是否不可以熨烫", "否", True),
])
def test_boolean_care_keys_do_not_reverse_the_prohibition(key, value, blocked):
    rules = care_rules("Instrucciones de planchado: No planchar", f"{key}：{value}")
    assert ("CARE_INSTRUCTION_CHANGED" in rules) == blocked
