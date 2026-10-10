"""CI_SAFE: archived own-field headphone fit and unchanged QA contracts."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts


CASES = json.loads((Path(__file__).parent / "fixtures/historical_overear_fit_20261010.json").read_text("utf8"))


def check(source, target, field="spec", **other):
    source_key = {"spec": "spec_es", "name": "name_es", "description": "desc_es", "details": "details_es"}[field]
    facts = SourceFacts(sku="1001", **{source_key: source, **other})
    return guard_translation(facts, {field: target}, (field,), semantic_facts=parse_semantic_facts(facts))


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_real_previously_approved_specs(row):
    assert row["source_evidence"]["selected"]["text"] == row["source"]
    assert len(row["source_evidence"]["selected"]["file_hash"]) == 64
    assert row["before_current_native_qa"]["status"] == "PASS"
    assert check(row["source"], row["before"])["status"] == "FAIL"
    assert check(row["source"], row["source_supported_proposed_correction"])["status"] == "PASS"


@pytest.mark.parametrize("target", ["包耳式", "罩耳式", "耳罩式", "全包耳式", "头戴式包耳式"])
def test_source_supported_fit_aliases(target):
    assert check("Over-ear", target)["status"] == "PASS"


@pytest.mark.parametrize("target", ["头戴式", "入耳式", "贴耳式", "挂耳式", "非包耳式", "不是耳罩式", "非全包耳式", "不采用罩耳式"])
def test_missing_wrong_or_negated_fit_is_blocked(target):
    result = check("Over-ear", target)
    assert result["status"] == "FAIL"
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


@pytest.mark.parametrize("field,source,target", [
    ("name", "Auriculares over-ear", "包耳式耳机"),
    ("description", "Auriculares over-ear", "包耳式耳机"),
    ("details", "Tipo: Over-ear", "类型：包耳式"),
])
def test_fit_requires_its_own_field(field, source, target):
    assert check(source, target, field)["status"] == "PASS"
    assert check(source, target.replace("包耳式", "头戴式"), field)["status"] == "FAIL"


def test_other_field_does_not_force_fit_into_generic_spec_or_name():
    assert check("Plegable", "可折叠", name_es="Auriculares over-ear")["status"] == "PASS"
    assert check("Auriculares", "耳机", "name", spec_es="Over-ear")["status"] == "PASS"


def test_partial_token_does_not_create_fit_fact():
    facts = SourceFacts(sku="1001", spec_es="Clover-earpiece")
    assert not any(f.source_text == "over-ear" for f in parse_semantic_facts(facts))


def test_fit_alias_does_not_waive_numbers_or_interfaces():
    assert check("Over-ear, 10 W, USB-C", "包耳式")["status"] == "FAIL"
    assert check("Over-ear, 10 W, USB-C", "包耳式，10 W，USB-C")["status"] == "PASS"
