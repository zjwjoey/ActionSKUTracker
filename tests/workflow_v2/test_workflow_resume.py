from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner
from action_tracker.database.connection import connect
from pathlib import Path


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


def test_resume_reprocesses_only_current_run_requeued_translation_work(workflow_root, source_row, fake_provider):
    context = new_context(workflow_root, business_date="2026-10-04", run_id="resume-requeued")
    first = WorkflowV2Runner(
        root=workflow_root, context=context, records=[source_row], expected_skus={"100"},
        provider=fake_provider, auto_translation=True,
    ).run()
    with connect(Path(first.context.database_path)) as db:
        db.execute(
            "UPDATE translation_queue SET status='RETRY',last_error='QA_RULE_REFRESH' "
            "WHERE run_id=? AND requested_fields='name'",
            (first.context.workflow_run_id,),
        )

    resumed = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2099-01-01", run_id="resume-requeued"),
        records=None, expected_skus=set(), provider=fake_provider, auto_translation=True,
    ).run(resume=True)

    recovery = resumed.report["translation_resume_recovery"]
    assert recovery["reason"] == "RUN_SCOPED_REQUEUED_TRANSLATION_WORK"
    assert recovery["skus"] == ["100"]
    assert resumed.stages["QWEN_TRANSLATE"].details["worker"]["completed"] == 1
    with connect(Path(resumed.context.database_path)) as db:
        assert db.execute(
            "SELECT status FROM translation_queue WHERE run_id=? AND requested_fields='name'",
            (resumed.context.workflow_run_id,),
        ).fetchone()[0] == "COMPLETED"


def test_resume_policy_uses_queue_field_scope_not_all_revisions_for_the_sku(workflow_root, source_row, fake_provider):
    context = new_context(workflow_root, business_date="2026-10-04", run_id="field-scope")
    first = WorkflowV2Runner(
        root=workflow_root, context=context, records=[source_row], expected_skus={"100"},
        provider=fake_provider, auto_translation=True,
    ).run()
    # The source version legitimately has six current revisions.  Model the
    # production shape where only its name queue belongs to this workflow;
    # the other completed fields are durable work from a different run.
    with connect(Path(first.context.database_path)) as db:
        db.execute(
            "UPDATE translation_queue SET run_id='historical-run' "
            "WHERE run_id=? AND requested_fields<>'name'",
            (first.context.workflow_run_id,),
        )

    resumed = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2099-01-01", run_id="field-scope"),
        records=None, expected_skus=set(), provider=fake_provider, auto_translation=True,
    ).run(resume=True)

    decisions = resumed.stages["TRANSLATION_POLICY"].details["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["sku"] == "100"
    assert decisions[0]["field"] == "name_es"
