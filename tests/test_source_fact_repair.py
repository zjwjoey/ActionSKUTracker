from action_tracker.translation.source_fact_repair import repair_model_output


def test_source_fact_repair_returns_candidate_unchanged_and_suggestion_only():
    source = {"name": "Cable CR2032", "description": "2 en 1; un calcetín"}
    prediction = {"name": "数据线", "description": "双面；一双"}
    result, suggestions = repair_model_output(source, prediction)
    assert result == prediction
    assert {item["status"] for item in suggestions} == {"SEMANTIC_REVIEW_REQUIRED"}
    assert {item["issue_type"] for item in suggestions} == {
        "MODEL_TOKEN_MISSING", "FUNCTIONAL_TOKEN_MISSING", "QUANTITY_OR_NUMBER_REVIEW",
    }


def test_source_fact_repair_never_writes_authoritative_correction():
    prediction = {"description": "原候选"}
    result, suggestions = repair_model_output({"description": "2 en 1"}, prediction)
    assert result is prediction or result == prediction
    assert suggestions and suggestions[0]["suggested_value"] != result["description"]
