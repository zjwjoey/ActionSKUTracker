import json
from pathlib import Path


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
