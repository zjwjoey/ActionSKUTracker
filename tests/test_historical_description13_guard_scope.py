"""Real source failures and deliberately invalid candidates, never approvals."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

CASES = json.loads((Path(__file__).parent / "fixtures" /
                    "historical_description13_guard_scope_20261010.json").read_text(encoding="utf8"))


def check(record, target):
    source = SourceFacts.from_record(record)
    return guard_translation(source, {"description": target}, ("description",),
                             semantic_facts=parse_semantic_facts(source))


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_real_description13_source_bound_guard_repair(row):
    assert row["source_evidence"]["selected"]["text"] == row["source"]
    assert len(row["source_evidence"]["selected"]["file_hash"]) == 64
    assert row["before_module_guard"]["status"] == "FAIL"
    assert row["formal_approval"] is False
    keys = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
            "spec": "spec_es", "description": "desc_es", "details": "details_es"}
    assert check({keys[key]: value for key, value in row["context"].items()}, row["target"])["status"] == "PASS"


@pytest.mark.parametrize("source,target", [
    ("Iluminación", "氛围照明"),
    ("Iluminación ambiental 20 cm", "氛围照明"),
    ("4 modos de iluminación", "3种照明模式"),
])
def test_illumination_scope_never_waives_product_or_numeric_facts(source, target):
    assert check({"desc_es": source}, target)["status"] == "FAIL"


def test_other_field_ambient_phrase_cannot_waive_generic_lamp_identity():
    assert check({"name_es": "Iluminación ambiental", "desc_es": "Iluminación"},
                 "氛围照明")["status"] == "FAIL"


@pytest.mark.parametrize("unit,chinese", [("mm²", "平方毫米"), ("cm²", "平方厘米"),
                                         ("m²", "平方米"), ("km²", "平方千米")])
def test_area_unit_alias_keeps_exponent_and_measurement(unit, chinese):
    assert check({"desc_es": "1.5 " + unit}, "1.5" + chinese)["status"] == "PASS"


@pytest.mark.parametrize("source,target", [("1.5 mm²", "1.5毫米"), ("20 cm²", "20厘米"),
                                          ("20 m²", "20米"), ("20 cm", "20平方厘米"),
                                          ("1.5 mm²", "2.5平方毫米")])
def test_mutated_area_power_or_value_is_blocked(source, target):
    assert check({"desc_es": source}, target)["status"] == "FAIL"


@pytest.mark.parametrize("token", ["Pro-max", "T-Rex", "gsm"])
def test_exact_source_tokens_are_not_general_residual_exceptions(token):
    assert check({"desc_es": "Producto"}, token + " 产品")["status"] == "FAIL"
    assert check({"name_es": token, "desc_es": "Producto"}, token + " 产品")["status"] == "FAIL"


@pytest.mark.parametrize("source,target", [("dos altavoces", "两个扬声器"),
                                          ("dos bolsillos", "两个口袋"),
                                          ("tres modos", "3个模式"),
                                          ("dos horas", "2小时")])
def test_spelled_quantity_noun_requires_own_source_and_exact_count(source, target):
    assert check({"desc_es": source}, target)["status"] == "PASS"
    assert check({"desc_es": "Producto", "spec_es": source}, target.replace("两", "2"))["status"] == "FAIL"


@pytest.mark.parametrize("source", ["treinta y dos altavoces", "ciento dos horas"])
def test_compound_count_does_not_become_last_digit(source):
    from action_tracker.localization.qa import _numbers
    assert not _numbers(source)


@pytest.mark.parametrize("source,target", [("dos horas y tres modos", "2小时和3个模式"),
                                          ("Incluye cable y cinco unidades", "含线缆和5件")])
def test_conjunction_between_independent_facts_keeps_both_quantities(source, target):
    assert check({"desc_es": source}, target)["status"] == "PASS"
    assert check({"desc_es": source}, target.replace("3", "4").replace("5", "6"))["status"] == "FAIL"
