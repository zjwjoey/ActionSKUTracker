from action_tracker.workflow_v2.translation_stage import plan_translations


def test_plan_is_field_scoped(source_row):
    plan = plan_translations([source_row], approved={"100": {"name": "unchanged"}})
    assert {item.field_name for item in plan} == {"name", "cat1", "cat2", "spec", "description", "details"}
