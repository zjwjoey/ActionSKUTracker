from action_tracker.translation.source_fact_repair import repair_model_output


def test_repair_only_suggests_missing_model_token_without_writing_candidate():
    value, repairs = repair_model_output(
        {"name": "Auriculares inalámbricos Solix SL-300"},
        {"name": "无线耳机"},
    )
    assert value == {"name": "无线耳机"}
    assert repairs[0]["issue_type"] == "MODEL_TOKEN_MISSING"
    assert repairs[0]["status"] == "SEMANTIC_REVIEW_REQUIRED"
    assert repairs[0]["suggested_value"] == "SL-300无线耳机"


def test_repair_only_suggests_two_in_one_semantics():
    value, repairs = repair_model_output(
        {"description": "Diseño 2 en 1: un adaptador para varios tipos"},
        {"description": "双面设计：适用于多种插孔"},
    )
    assert value == {"description": "双面设计：适用于多种插孔"}
    assert repairs[0]["issue_type"] == "FUNCTIONAL_TOKEN_MISSING"
    assert repairs[0]["status"] == "SEMANTIC_REVIEW_REQUIRED"


def test_repair_only_suggests_singular_sock_phrase():
    value, repairs = repair_model_output(
        {"description": "Puedes ponerte un calcetín grande y suelto"},
        {"description": "你可以穿上一双宽松的袜子"},
    )
    assert value == {"description": "你可以穿上一双宽松的袜子"}
    assert repairs[0]["issue_type"] == "QUANTITY_OR_NUMBER_REVIEW"
    assert repairs[0]["status"] == "SEMANTIC_REVIEW_REQUIRED"
