"""Actual mistranslations must fail Guard; Guard PASS is not approval."""
import json
from pathlib import Path

import pytest

from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.semantic import parse_semantic_facts
from action_tracker.localization.qa import guard_translation

CASES = json.loads((Path(__file__).parent / "fixtures" /
                    "historical_product_noun_errors_20261010.json").read_text(encoding="utf8"))


@pytest.mark.parametrize("row", CASES, ids=lambda row: row["sku"])
def test_real_product_noun_errors_are_blocked_without_fabricating_owner_approval(row):
    evidence = row["source_evidence"]
    assert evidence["text"] == row["source"]
    assert len(evidence["file_hash"]) == len(evidence["field_hash"]) == 64
    assert row["formal_approval"] is False
    source = SourceFacts.from_record({"name_es": row["source"]})
    facts = parse_semantic_facts(source)
    target = row["source_backed_target"] or row["observed_qwen_candidate"]
    result = guard_translation(source, {"name": target}, ("name",), semantic_facts=facts)
    if row["source_backed_target"]:
        assert result["status"] == "PASS"
    else:
        assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED"
                   and f["evidence"]["source_term"] == row["source_term"]
                   for f in result["findings"])


@pytest.mark.parametrize("noun", ["Marcadores grandes de libros", "Marcadores grandes para lectura",
                                  "Marcadores acrílicos de páginas", "Marcadores grandes de navegador"])
def test_book_or_browser_markers_do_not_inherit_pen_facts(noun):
    source = SourceFacts.from_record({"name_es": noun})
    facts = parse_semantic_facts(source)
    assert not any(f.semantic_type == "PRODUCT_TYPE" and f.source_text.startswith("marcadores")
                   for f in facts)
    assert guard_translation(source, {"name": "书签"}, ("name",), semantic_facts=facts)["status"] == "PASS"


def test_separate_pen_phrase_survives_book_marker_exception():
    source = SourceFacts.from_record({"name_es": "Marcadores grandes y marcadores grandes de libros"})
    result = guard_translation(source, {"name": "书签"}, ("name",),
                               semantic_facts=parse_semantic_facts(source))
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in result["findings"])


def test_cosmetic_phrase_cannot_transfer_from_description_to_name():
    source = SourceFacts.from_record({"name_es": "Brocha", "desc_es": "Brocha para polvos"})
    facts = parse_semantic_facts(source)
    assert not any(f.source_field == "name_es" and f.source_text == "brocha para polvos" for f in facts)
    assert guard_translation(source, {"name": "刷子"}, ("name",), semantic_facts=facts)["status"] == "PASS"
    bad = guard_translation(source, {"description": "装修刷"}, ("description",), semantic_facts=facts)
    assert any(f["rule_id"] == "SEMANTIC_FACT_DROPPED" for f in bad["findings"])


def test_single_words_do_not_force_cosmetic_product_nouns():
    source = SourceFacts.from_record({"name_es": "Brocha de pintura y ampollas en los pies"})
    assert not any(f.semantic_type == "PRODUCT_TYPE" and f.canonical_value in {"散粉刷", "安瓶"}
                   for f in parse_semantic_facts(source))
