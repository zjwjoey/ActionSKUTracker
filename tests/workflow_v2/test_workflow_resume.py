from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def test_resume_uses_same_run(workflow_root, source_row, fake_provider):
    context = new_context(workflow_root, business_date="2026-10-04", run_id="resume-1")
    first = WorkflowV2Runner(root=workflow_root, context=context, records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True).run()
    second = WorkflowV2Runner(root=workflow_root, context=context, records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True).run(resume=True)
    assert first.context.workflow_run_id == second.context.workflow_run_id == "resume-1"
    assert fake_provider.calls == 1
