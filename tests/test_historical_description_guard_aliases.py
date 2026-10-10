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
