from action_tracker.translation.model_guard import validate_model_output


def test_guard_exposes_pass_flag_without_semantic_approval():
    passed = validate_model_output({"name": "Plancha F48"}, {"name": "F48 电熨斗"}, expected_fields=("name",))
    flagged = validate_model_output({"description": "Incluye 2 piezas"}, {"description": "含3件"}, expected_fields=("description",))
    assert passed.status == "PASS"
    assert flagged.status == "FLAG"
    assert flagged.accepted is False
