import json
from pathlib import Path

from action_tracker.localization.formatter import format_details, format_spec
from action_tracker.translation.source_fact_repair import repair_model_output


def test_20260921_regression_matrix_is_present_and_guard_boundary_is_explicit():
    root = Path(__file__).resolve().parents[1] / ".agents" / "skills"
    expected_by_skill = {
        "action-qwen-translation": {
            "description_cross_field_fact", "empty_source_no_source",
            "details_key_semantic_mismatch", "details_negation_reversed", "source_conflict",
        },
        "action-cn-description-details-review": {
            "description_cross_field_fact", "source_empty_no_source",
            "details_key_semantic_mismatch", "negation_reversed", "source_conflict",
        },
    }
    for skill, expected in expected_by_skill.items():
        payload = json.loads((root / skill / "examples" / "semantic_regressions.json").read_text(encoding="utf-8"))
        ids = {item["id"] for item in payload["cases"]}
        assert expected <= ids
        assert any(item.get("keep") == 1999 and item.get("corrected") == 0 for item in payload["cases"])


def test_skill_contract_requires_independent_semantic_review():
    details = (root := Path(__file__).resolve().parents[1] / ".agents" / "skills" / "action-cn-description-details-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "GUARD PASS IS NOT TRANSLATION PASS" in details
    assert "Guard PASS cannot create `KEEP`" in details
    assert "A fluent but semantically wrong key is `CORRECTED`" in details


def test_details_review_preserves_key_semantics_and_negation():
    rendered = format_details("Voltaje: 9 V; Potencia: 10 W; Contenido: 200 gramos; Sin alcohol: No")
    assert "电压：9V" in rendered
    assert "功率：10W" in rendered
    assert "含量：200g" in rendered
    assert "不含酒精：否" in rendered
    assert "电压：10W" not in rendered
    assert "功率：9V" not in rendered


def test_spec_formatter_protects_technical_tokens_while_normalizing_dimensions():
    rendered = format_spec("Talla XL | USB-C | CR2032 | A3 | 50 x 60 cm")
    assert "XL" in rendered and "USB-C" in rendered and "CR2032" in rendered and "A3" in rendered
    assert "50×60cm" in rendered
    assert "X×L" not in rendered and "USB-C" in rendered


def test_guard_style_source_fact_check_stays_candidate_only_for_semantic_errors():
    source = {"description": "2 en 1"}
    prediction = {"description": "双面"}
    result, suggestions = repair_model_output(source, prediction)
    assert result == prediction
    assert suggestions and all(item["status"] == "SEMANTIC_REVIEW_REQUIRED" for item in suggestions)
