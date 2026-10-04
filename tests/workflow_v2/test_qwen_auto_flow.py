from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def test_fake_qwen_flow(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True, auto_policy=True, apply_enabled=True).run()
    assert result.stages["QWEN_TRANSLATE"].details["called"] == 1
    assert result.stages["TRANSLATION_QA"].status == "PASS"


def test_high_risk_fields_require_review(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True,
    ).run()
    decisions = result.stages["TRANSLATION_POLICY"].details["decisions"]
    assert {row["field"] for row in decisions if row["decision"] == "REVIEW_REQUIRED"} >= {"name_es", "desc_es", "details_es"}
    assert result.context.translation_ready is False


def test_default_extraction_adapter_contract(workflow_root, source_row):
    calls = []

    def adapter(**kwargs):
        calls.append(kwargs["workflow_run_id"])
        return {"records": [source_row], "authoritative_skus": ["100"], "extraction_run_id": "daily-1"}

    result = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"),
        records=None, extraction_adapter=adapter,
    ).run()
    assert result.stages["EXTRACT"].status == "PASS"
    assert calls
    assert result.context.extraction_run_id == "daily-1"
