"""Public retail names and source-bound BBQ flavour negative controls."""
import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import _is_allowed_translated_tech_token, guard_translation


@pytest.mark.parametrize("source,target", [
    ("Lay's Super Chips Deep American BBQ", "美式烧烤味薯片"),
    ("Doritos Honey BBQ", "蜂蜜烧烤味薯片"),
    ("Fideos Tummie Time Korean BBQ", "韩式烧烤味面条"),
    ("Fideos Tummie Time Korean BBQ", "韩式烤肉味面条"),
])
def test_real_food_flavour_acronym_can_be_translated(source, target):
    # These are frozen official name fields from 3001049/3001051/3205026.
    # No SKU, brand row or category is used to authorize the equivalence.
    result = guard_translation(SourceFacts.from_record({"name_es": source}),
                               {"name": target}, ("name",))
    assert result["overall_ready"], result["findings"]


@pytest.mark.parametrize("target", ["奶酪味薯片", "原味薯片", "不含烧烤味薯片", "非烤肉味面条", "不是烧烤风味"])
def test_wrong_or_negated_flavour_does_not_satisfy_bbq(target):
    result = guard_translation(SourceFacts.from_record({"name_es": "Doritos Honey BBQ"}),
                               {"name": target}, ("name",))
    assert not result["overall_ready"]
    assert any(f["rule_id"] == "PROTECTED_TOKEN_MISSING" for f in result["findings"])


@pytest.mark.parametrize("source", [
    "Doritos Honey", "Chips barbacoa", "Dispositivo BBQ", "Dispositivo Honey BBQ",
    "Modelo Korean BBQ", "Chips BBQ-120", "Chips XBBQ", "Chips BBQX",
    "Chips sin BBQ", "Chips no BBQ",
])
def test_translation_equivalence_cannot_waive_absent_model_or_negative_sources(source):
    assert not _is_allowed_translated_tech_token(source, "BBQ", "烧烤味")


def test_another_source_field_cannot_authorize_the_name():
    source = SourceFacts.from_record({"name_es": "Dispositivo BBQ", "desc_es": "Honey BBQ chips"})
    assert not guard_translation(source, {"name": "烧烤味设备"}, ("name",))["overall_ready"]


def test_unknown_brand_tokens_still_require_review():
    source = SourceFacts.from_record({"name_es": "TCX Chips Korean BBQ"})
    result = guard_translation(source, {"name": "韩式烧烤味薯片"}, ("name",))
    assert not result["overall_ready"]
    assert any(f["evidence"].get("value") == "TCX" for f in result["findings"])
