from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def test_resume_uses_same_run(workflow_root, source_row, fake_provider):
    context = new_context(workflow_root, business_date="2026-10-04", run_id="resume-1")
    first = WorkflowV2Runner(root=workflow_root, context=context, records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True).run()
    calls_after_first = fake_provider.calls
    # New-process semantics: discard the first Runner/context and recreate
    # only the run identity before loading state/artifacts from disk.
    second_context = new_context(workflow_root, business_date="2099-01-01", run_id="resume-1")
    second = WorkflowV2Runner(root=workflow_root, context=second_context, records=None, expected_skus=set(), provider=fake_provider, auto_translation=True).run(resume=True)
    assert first.context.workflow_run_id == second.context.workflow_run_id == "resume-1"
    assert second.context.business_date == "2026-10-04"
    assert second.context.source_commit_id == first.context.source_commit_id
    assert calls_after_first >= 1
    assert fake_provider.calls == calls_after_first
