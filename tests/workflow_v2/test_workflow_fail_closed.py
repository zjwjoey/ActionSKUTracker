from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def test_source_failure_makes_qwen_unavailable(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100", "missing"}, provider=fake_provider, auto_translation=True).run()
    assert result.state == "BLOCKED"
    assert result.stages["QWEN_TRANSLATE"].status in {"BLOCKED", "SKIPPED"}
