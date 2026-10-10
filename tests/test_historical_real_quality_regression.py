"""Real source evidence: known guard errors and source conflicts, not gold guesses."""
import json
from pathlib import Path

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.normalization.structured_details import parse_structured_details
from action_tracker.localization.contracts import SemanticFact
import pytest

CASES = json.loads((Path(__file__).parent / "fixtures" /
                    "historical_quality_review_20261010.json").read_text(encoding="utf8"))


def case(sku):
    return next(row for row in CASES if row["sku"] == sku)


def test_real_full_width_details_delimiters_preserve_boolean_facts():
    row = case("1325690")
    source = SourceFacts.from_record({"sku": row["sku"], "details_es": row["source"]})
    assert len(parse_structured_details(row["source"])) == 11
    pairs = parse_structured_details(row["target"])
    assert len(pairs) == 11
    assert [(pair.key, pair.value) for pair in pairs][5] == ("无硅", "否")
    assert guard_translation(source, {"details": row["target"]}, ("details",))["status"] == "PASS"
    reversed_value = row["target"].replace("无硅：否", "无硅：是")
    bad = guard_translation(source, {"details": reversed_value}, ("details",))
    assert any(f["rule_id"] == "DETAIL_BOOLEAN_POLARITY_CHANGED" for f in bad["findings"])


def test_full_width_separator_preserves_duplicate_keys_and_order():
    pairs = parse_structured_details("颜色：红色；颜色：蓝色；材质：木质")
    assert [(pair.key, pair.value) for pair in pairs] == [("颜色", "红色"), ("颜色", "蓝色"), ("材质", "木质")]


def test_real_chinese_layer_digit_is_preserved_without_lexical_number_addition():
    row = case("2533753")
    source = SourceFacts.from_record({"sku": row["sku"], "desc_es": row["source"]})
    assert guard_translation(source, {"description": row["target"]}, ("description",))["status"] == "PASS"
    bad = guard_translation(source, {"description": row["target"].replace("五层", "四层")}, ("description",))
    assert any(f["rule_id"] == "NUMERIC_DROPPED" for f in bad["findings"])
    ordinary = SourceFacts.from_record({"desc_es": "Herramientas"})
    assert guard_translation(ordinary, {"description": "五金工具"}, ("description",))["status"] == "PASS"


def test_real_nonsterile_mistranslation_stays_blocked_with_material_conflict():
    row = case("2523375")
    assert "no estériles" in row["source"]
    assert "látex" in row["source"] and "vinilo" in row["source"]
    assert row["expected_behavior"] == "OWNER_REVIEW_SOURCE_CONFLICT_NO_GOLD_TARGET"
    source = SourceFacts.from_record({"desc_es": row["source"]})
    result = guard_translation(source, {"description": row["target"]}, ("description",))
    assert any(f["rule_id"] == "STERILITY_STATUS_CHANGED" for f in result["findings"])


def test_real_unapproved_category_synonym_is_blocked():
    row = case("3214854")
    source = SourceFacts.from_record({"cat1_es": row["source"]})
    result = guard_translation(source, {"cat1": row["target"]}, ("cat1",))
    assert any(f["rule_id"] == "CATEGORY_INVALID" for f in result["findings"])


def test_all_real_cases_have_immutable_source_breadcrumbs():
    for row in CASES:
        assert row["source_evidence"]["text"] == row["source"]
        assert len(row["source_evidence"]["file_hash"]) == 64
        assert len(row["source_evidence"]["field_hash"]) == 64
        assert row["source_evidence"]["reference"]


@pytest.mark.parametrize("sku,target", [("3016045", "木盖罐子"), ("3203195", "装饰木屑"),
                                        ("3204372", "芒果木盒")])
def test_real_wood_compounds_preserve_material_without_allowing_woodgrain(sku, target):
    row = case(sku)
    source = SourceFacts.from_record({"sku": sku, "name_es": row["source"]})
    fact = SemanticFact("MATERIAL", "madera", "木质", "木质", "name_es")
    assert guard_translation(source, {"name": target}, ("name",), semantic_facts=(fact,))["status"] == "PASS"
    result = guard_translation(source, {"name": "木纹塑料制品"}, ("name",), semantic_facts=(fact,))
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


@pytest.mark.parametrize("sku", ["3205379", "3206321"])
def test_real_karaoke_lexical_ok_is_not_an_invented_technical_model(sku):
    row = case(sku)
    source = SourceFacts.from_record({"sku": sku, "name_es": row["source"]})
    assert guard_translation(source, {"name": row["target"]}, ("name",))["status"] == "PASS"
    for bad, rule in (("OK麦克风", "PROTECTED_TOKEN_ADDED"),
                      ("卡拉OK麦克风 OK", "PROTECTED_TOKEN_ADDED"),
                      ("卡拉OK99麦克风", "MODEL_CHANGED")):
        result = guard_translation(source, {"name": bad}, ("name",))
        assert result["status"] == "FAIL"
        assert any(f["rule_id"] == rule for f in result["findings"])


def test_karaoke_context_cannot_waive_an_added_token_in_the_title():
    source = SourceFacts.from_record({"name_es": "Micrófono", "desc_es": "Para karaoke"})
    result = guard_translation(source, {"name": "卡拉OK麦克风"}, ("name",))
    assert any(f["rule_id"] == "PROTECTED_TOKEN_ADDED" for f in result["findings"])


def test_real_bamboo_compound_retains_material_but_bamboo_pattern_is_not_material():
    row = case("2529728")
    source = SourceFacts.from_record({"sku": row["sku"], "name_es": row["source"]})
    fact = SemanticFact("MATERIAL", "bambú", "竹制", "竹制", "name_es")
    assert guard_translation(source, {"name": row["target"]}, ("name",), semantic_facts=(fact,))["status"] == "PASS"
    result = guard_translation(source, {"name": "竹纹塑料签"}, ("name",), semantic_facts=(fact,))
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


def test_real_bbq_flavour_translation_cannot_waive_device_models_or_cross_field_tokens():
    row = case("3209565")
    source = SourceFacts.from_record({"name_es": row["source"]})
    assert guard_translation(source, {"name": row["target"]}, ("name",))["status"] == "PASS"
    assert guard_translation(source, {"name": "薯片"}, ("name",))["status"] == "FAIL"
    for record in ({"name_es": "Dispositivo BBQ", "desc_es": "BBQ style"},
                   {"name_es": "Dispositivo BBQ-120 style"}):
        bad = guard_translation(SourceFacts.from_record(record), {"name": "烧烤风味设备"}, ("name",))
        assert bad["status"] == "FAIL"
