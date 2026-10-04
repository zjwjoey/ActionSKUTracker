from pathlib import Path

from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def test_phase1_manual_review_is_pending(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2026-10-04"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=False, auto_export=False,
        apply_enabled=False,
    ).run()

    assert result.stages["FACT_COMMIT"].status == "PASS"
    assert result.stages["QWEN_TRANSLATE"].status == "PASS"
    assert result.stages["TRANSLATION_QA"].status == "PASS"
    assert result.stages["TRANSLATION_POLICY"].status == "REVIEW_REQUIRED"
    assert result.stages["TRANSLATION_APPLY"].status == "PENDING"
    assert result.stages["EXPORT_AUDIT"].status == "PENDING"
    assert result.stages["EXPORT_WRITE"].status == "PENDING"
    assert result.state == "SUCCESS_WITH_PENDING"


def test_bounded_batch_remains_resumable(workflow_root, source_row, fake_provider):
    records = []
    for index in range(20):
        row = dict(source_row)
        row["sku"] = str(100 + index)
        row["canonical_id"] = f"ACT{100 + index:07d}"
        row["product_url"] = f"https://www.action.com/es-es/p/{100 + index}"
        records.append(row)

    first = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2026-10-04", run_id="bounded"),
        records=records, expected_skus={row["sku"] for row in records}, provider=fake_provider,
        auto_translation=True, auto_policy=False,
    ).run()
    assert first.stages["QWEN_TRANSLATE"].status == "PENDING"
    assert first.stages["QWEN_TRANSLATE"].details["remaining"] == 70
    assert first.state == "SUCCESS_WITH_PENDING"

    calls = fake_provider.calls
    resumed = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2099-01-01", run_id="bounded"),
        records=None, expected_skus=set(), provider=fake_provider, auto_translation=True,
        auto_policy=False, temp_db=Path(first.context.database_path),
    ).run(resume=True)
    assert resumed.context.workflow_run_id == first.context.workflow_run_id
    assert resumed.stages["FACT_COMMIT"].status == "PASS"
    assert resumed.stages["QWEN_TRANSLATE"].details["remaining"] == 20
    assert fake_provider.calls > calls


def test_auto_export_false_is_operator_pending(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=False, auto_export=False,
    ).run()
    assert result.stages["EXPORT_WRITE"].status == "PENDING"
    assert result.stages["EXPORT_WRITE"].details["reason"] == "OPERATOR_PUBLICATION_REQUIRED"
