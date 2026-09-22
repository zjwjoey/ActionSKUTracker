from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = Path(r"C:\Users\Administrator\.codex\skills")
DETAILS = SKILLS / "action-cn-description-details-review" / "SKILL.md"
SHORT = SKILLS / "action-cn-localization-review" / "SKILL.md"
EXAMPLES = SKILLS / "action-cn-description-details-review" / "examples" / "semantic_regressions.json"
QWEN_EXAMPLES = ROOT / ".agents" / "skills" / "action-qwen-translation" / "examples" / "semantic_regressions.json"


def _text(path: Path) -> str:
    # Skill prose may wrap a contract phrase over multiple Markdown lines;
    # normalize whitespace so tests check the contract, not line wrapping.
    return " ".join(path.read_text(encoding="utf-8").split())


def test_updated_skill_files_exist_and_keep_boundaries():
    # The installed runtime has two real localization Skills; the old
    # action-qwen-translation path is not a separate installed Skill.
    assert DETAILS.exists()
    assert SHORT.exists()
    assert "description/details are context only" in _text(SHORT)
    assert "Never modify `name`, `cat1`, `cat2`, `spec`" in _text(DETAILS)


def test_qwen_same_field_source_contract_and_brand_policy():
    text = _text(DETAILS) + " " + _text(SHORT)
    for needle in (
        "FIELD CONTRACT",
        "PRIMARY SOURCE",
        "CONTEXT FOR DISAMBIGUATION",
        "Never use another field as fallback",
        "NO_BRAND",
        "SOURCE_FAITHFUL",
        "description/details",
        "(?<=\\d)\\s*[xX]\\s*(?=\\d)",
        "GUARD PASS IS NOT TRANSLATION PASS",
    ):
        assert needle in text


def test_details_is_model_led_and_guard_is_not_semantic_approval():
    text = _text(DETAILS)
    for needle in (
        "MODEL-LED MTPE SEMANTIC REVIEWER",
        "GUARD PASS IS NOT TRANSLATION PASS",
        "Details semantic contract",
        "Voltaje",
        "Contenido",
        "Sin alcohol",
        "ordinary semantic/translation mismatch is `CORRECTED`",
        "qwen_calls_for_review=0",
        "master_writes=0",
    ):
        assert needle in text


def test_regression_fixture_contains_requested_failure_classes():
    payload = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    assert payload["historical_reviewer_baseline"] == {
        "keep": 1999,
        "corrected": 0,
        "note": "Historical regression reference only; Guard PASS never auto-creates KEEP.",
    }
    ids = {case["id"] for case in payload["cases"]}
    assert {
        "description_cross_field_pollution",
        "empty_source_nonempty_candidate",
        "protected_x_token",
        "brand_ip_removed_from_details",
        "details_key_semantic_mismatch",
        "details_negation_reversed",
        "product_identity_mistranslation",
        "ordinary_spanish_residual",
        "source_conflict",
    } <= ids


def test_repo_qwen_skill_has_versioned_regression_fixture():
    payload = json.loads(QWEN_EXAMPLES.read_text(encoding="utf-8"))
    ids = {case["id"] for case in payload["cases"]}
    assert "description_cross_field_fact" in ids
    assert "details_key_semantic_mismatch" in ids
    assert "source_conflict" in ids


def test_guard_pass_does_not_hide_semantic_identity_error():
    """The numeric Guard may pass; the semantic reviewer must still correct it."""

    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Bolsas para lavadora"},
        {"description": "袜子"},
        expected_fields=("description",),
    )
    assert result.accepted is True

    payload = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    case = next(row for row in payload["cases"] if row["id"] == "product_identity_mistranslation")
    assert case["expected_decision"] == "CORRECTED"


def test_spec_safety_protects_x_tokens_and_allows_only_dimension_context():
    text = _text(SHORT)
    assert 'Never run a global `text.replace("x", "\\u00D7")`' in text
    assert "(?<=\\d)\\s*[xX]\\s*(?=\\d)" in text
    for token in ("XL", "XXL", "XXXL", "USB-C", "CR2032", "A4", "A3", "IP44"):
        assert token in text
