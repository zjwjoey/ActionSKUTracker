"""Source-evidenced real SKU regressions; Guard is not semantic approval."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.services.hashing import localization_field_source_hash


CASES = json.loads((Path(__file__).parent / "fixtures" /
                   "historical_spec_review_20261010.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["sku"])
def test_reviewed_historical_spec_retains_numeric_and_unit_facts(case):
    evidence = case["source_evidence"]
    assert evidence["text"] == case["source"]
    assert len(evidence["file_hash"]) == 64
    assert evidence["reference"] and evidence["field_hash"]
    source = SourceFacts.from_record({"sku": case["sku"], "spec_es": case["source"]})
    good = guard_translation(source, {"spec": case["approved_target"]}, ("spec",))
    assert good["status"] == "PASS"
    corrupt = guard_translation(source, {"spec": "999 千克"}, ("spec",))
    assert corrupt["status"] == "FAIL"
    assert any(item["rule_id"] == "NUMERIC_DROPPED" for item in corrupt["findings"])


def test_real_decimal_dimension_error_requires_review():
    case = next(case for case in CASES if case["sku"] == "3218603")
    assert case["source"] == "5x5,5x5 cm | diferentes variantes"
    source = SourceFacts.from_record({"sku": case["sku"], "spec_es": case["source"]})
    bad = guard_translation(source, {"spec": case["provider_candidate"]}, ("spec",))
    assert bad["status"] == "FAIL"
    assert case["approved_target"] == "5×5.5×5厘米 | 不同款式"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["sku"])
def test_real_spec_hash_is_independent_of_description_change(case):
    record = {"sku": case["sku"], "spec_es": case["source"],
              "desc_es": case["context"].get("description", "")}
    changed = {**record, "desc_es": "Nuevo texto"}
    before = {field: localization_field_source_hash(record, field)
              for field in ("spec", "description")}
    after = {field: localization_field_source_hash(changed, field)
             for field in ("spec", "description")}
    assert before["spec"] == after["spec"]
    assert before["description"] != after["description"]
