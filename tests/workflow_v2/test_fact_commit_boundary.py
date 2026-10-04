from action_tracker.workflow_v2.runner import WorkflowV2Runner
from action_tracker.workflow_v2.context import new_context


def test_fact_commit_precedes_translation(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True, auto_policy=True, apply_enabled=True, auto_export=True).run()
    assert result.context.source_commit_id
    assert result.stages["QWEN_TRANSLATE"].details["called"] == 1
