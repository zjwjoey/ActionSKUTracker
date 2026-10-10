"""Two historical source-backed capsule nouns, plus non-laundry boundaries."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.semantic import parse_semantic_facts

CASES = json.loads((Path(__file__).parent / "fixtures" /
                    "historical_detergent_capsules_20261010.json").read_text(encoding="utf8"))


def check(record, target):
    source = SourceFacts.from_record(record)
    return guard_translation(source, {"name": target}, ("name",),
                             semantic_facts=parse_semantic_facts(source))


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_real_detergent_capsule_noun_has_field_bound_evidence(row):
    evidence = row["source_evidence"]
    assert evidence["text"] == row["source"]
    assert len(evidence["file_hash"]) == len(evidence["field_hash"]) == 64
    assert check({"name_es": row["source"]}, row["target"])["status"] == "PASS"
    bad = check({"name_es": row["source"]}, "玫瑰清洁用品")
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in bad["findings"])


@pytest.mark.parametrize("record,target", [
    ({"name_es": "Cápsulas de vitaminas"}, "洗衣凝珠"),
    ({"name_es": "Cápsulas", "desc_es": "Detergente en cápsulas color"}, "洗涤凝珠"),
    ({"name_es": "Detergente", "desc_es": "Detergente en cápsulas color"}, "洗衣凝珠"),
    ({"name_es": "Cápsulas de vitaminas junto a detergente"}, "洗涤凝珠"),
    ({"name_es": "Detergente en cápsulas para lavavajillas color"}, "洗衣凝珠"),
    ({"name_es": "Detergente en cápsulas"}, "洗衣凝珠"),
])
def test_capsule_alias_does_not_generalize_to_medicine_dishes_or_other_fields(record, target):
    result = check(record, target)
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


def test_detergent_capsule_alias_preserves_quantity_and_models():
    source = {"name_es": "Cápsulas de lavado color 17 uds. modelo X-17"}
    assert check(source, "洗衣凝珠")["status"] == "FAIL"
    result = check(source, "洗衣凝珠 17个 X-18")
    assert any(f["rule_id"] in {"MODEL_CHANGED", "NUMERIC_DROPPED"} for f in result["findings"])
