"""Source-bound description equivalences; a passing Guard is not approval."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

CASES = json.loads((Path(__file__).parent / "fixtures" /
                    "historical_description_guard_aliases_20261010.json").read_text(encoding="utf8"))


def check(record, target, field="description"):
    source = SourceFacts.from_record(record)
    return guard_translation(source, {field: target}, (field,),
                             semantic_facts=parse_semantic_facts(source))


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_real_description_equivalences_retain_their_own_source_evidence(row):
    assert row["source_evidence"]["text"] == row["source"]
    assert row["source_hash"] == value_hash(row["source"])
    assert len(row["source_evidence"]["file_hash"]) == 64
    assert row["formal_approval"] is False
    assert row["before_module_guard"]["status"] == "FAIL"
    assert check({"desc_es": row["source"]}, row["target"])["status"] == "PASS"


@pytest.mark.parametrize("record,target,field", [
    ({"desc_es": "Iluminación"}, "照明", "description"),
    ({"name_es": "Iluminación", "desc_es": "Iluminación focal"}, "照明", "name"),
    ({"desc_es": "Efectos de iluminación"}, "普通效果", "description"),
    ({"desc_es": "Iluminación focal 20 cm"}, "重点照明", "description"),
    ({"desc_es": "4 efectos de iluminación"}, "3种灯光效果", "description"),
])
def test_lighting_aliases_cannot_erase_product_nouns_or_numeric_facts(record, target, field):
    assert check(record, target, field)["status"] == "FAIL"


@pytest.mark.parametrize("source,target", [
    ("Cable de carga", "Re-load 充电线"),
    ("Juego de cartas", "Skip-Bo 卡牌"),
    ("Juego de cartas", "UNO-Flip 卡牌"),
    ("Cable Re-load", "re load 充电线"),
    ("Cable Re-load", "Re-load con cable 充电线"),
    ("Juego Skip-Bo", "Skip-Bo para jugar 游戏"),
])
def test_brand_spans_require_complete_same_field_source_and_keep_prose_blocked(source, target):
    result = check({"desc_es": source}, target)
    assert any(x["rule_id"] == "SPANISH_RESIDUAL" for x in result["findings"])


@pytest.mark.parametrize("record,target", [
    ({"desc_es": "Gel de ducha"}, "三效合一沐浴露"),
    ({"desc_es": "2 en 1"}, "三效合一"),
    ({"desc_es": "Gel de ducha", "name_es": "Gel 3 en 1"}, "三效合一沐浴露"),
    ({"desc_es": "3 en 1, 3 en 1"}, "三效合一"),
    ({"desc_es": "3 en 1 modelo X-17"}, "三效合一 X-18"),
])
def test_three_effect_alias_preserves_scope_counts_and_models(record, target):
    assert check(record, target)["status"] == "FAIL"


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_valid_real_descriptions_still_pass_with_full_context(row):
    keys = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
            "spec": "spec_es", "description": "desc_es", "details": "details_es"}
    record = {keys[key]: value for key, value in row["context"].items()}
    assert check(record, row["target"])["status"] == "PASS"


@pytest.mark.parametrize("suffix", ["\n10", "\n1300", "\n长10米"])
def test_real_led_description_cannot_import_spec_length_or_details_lumen(suffix):
    row = next(row for row in CASES if row["sku"] == "3015660")
    keys = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
            "spec": "spec_es", "description": "desc_es", "details": "details_es"}
    record = {keys[key]: value for key, value in row["context"].items()}
    # These intentionally mutated candidates are failures, not gold targets.
    result = check(record, row["target"] + suffix)
    assert any(x["rule_id"] == "NUMERIC_ADDED" for x in result["findings"])


@pytest.mark.parametrize("field", ["name", "cat1", "cat2", "description", "details"])
def test_other_field_number_never_authorizes_a_non_spec_translation(field):
    keys = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
            "description": "desc_es", "details": "details_es"}
    result = check({keys[field]: "Color azul", "spec_es": "10 cm"}, "蓝色10", field)
    assert any(x["rule_id"] == "NUMERIC_ADDED" for x in result["findings"])


def test_spanish_article_does_not_turn_into_a_false_added_quantity():
    assert check({"desc_es": "Una vela azul"}, "一支蓝色蜡烛")["status"] == "PASS"


WORD_CASES = json.loads((Path(__file__).parent / "fixtures" /
                         "historical_spanish_quantity_words_20261010.json").read_text(encoding="utf8"))


def test_real_charger_preserves_numeric_two_and_spelled_out_two_without_retranslation():
    row = next(x for x in WORD_CASES if x["sku"] == "3013594")
    assert row["evidence"]["selected"]["text"] == row["source"]
    assert any(x["rule_id"] == "NUMERIC_ADDED" for x in row["qa"]["findings"])
    assert check({"desc_es": row["source"]}, row["target"])["status"] == "PASS"


@pytest.mark.parametrize("target,status", [("5卷热敏纸", "PASS"), ("五卷热敏纸", "PASS"),
                                           ("4卷热敏纸", "FAIL")])
def test_real_thermal_paper_spelled_quantity_and_corrupted_mutation(target, status):
    row = next(x for x in WORD_CASES if x["sku"] == "3224425")
    assert row["evidence"]["selected"]["text"] == row["source"]
    assert check({"desc_es": row["source"]}, target)["status"] == status
    # Even the correct quantity cannot authorize imported dimensions.
    assert check({"desc_es": row["source"], "spec_es": "57 mm x 18 m"},
                 target + "57毫米×18米")["status"] == "FAIL"


@pytest.mark.parametrize("source", ["UNO-Flip", "Dos", "tres favoritos",
                                     "treinta y cinco rollos", "ciento cinco rollos"])
def test_cardinal_phrases_do_not_invent_brand_pronoun_or_compound_quantities(source):
    from action_tracker.localization.qa import _numbers
    assert not _numbers(source)


@pytest.mark.parametrize("source,target", [("dos dispositivos", "两个设备"),
                                           ("cinco unidades", "5件"),
                                           ("tres pares", "3双")])
def test_explicit_counted_noun_requires_own_field_cardinal(source, target):
    assert check({"desc_es": source}, target)["status"] == "PASS"
    assert check({"desc_es": "Producto", "spec_es": source}, target.replace("两", "2"))["status"] == "FAIL"
