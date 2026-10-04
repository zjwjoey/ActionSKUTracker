from pathlib import Path

from action_tracker.workflow_v2.runner import WorkflowV2Runner
from action_tracker.workflow_v2.context import new_context
from action_tracker.database.connection import connect


def test_fact_commit_precedes_translation(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True, auto_policy=True, apply_enabled=True, auto_export=True).run()
    assert result.context.source_commit_id
    assert result.stages["QWEN_TRANSLATE"].details["called"] == 1


def test_presence_only_commit_preserves_existing_fact(workflow_root, source_row, tmp_path):
    db = tmp_path / "primary-like.sqlite3"
    first = WorkflowV2Runner(
        root=workflow_root / "first",
        context=new_context(workflow_root / "first", business_date="2026-10-04", run_id="first"),
        records=[source_row], expected_skus={"100"}, temp_db=db,
    ).run()
    assert first.context.source_commit_id
    partial = dict(source_row)
    partial["name_es"] = ""
    second = WorkflowV2Runner(
        root=workflow_root / "second",
        context=new_context(workflow_root / "second", business_date="2026-10-04", run_id="second"),
        records=[partial], expected_skus={"100"}, temp_db=db,
    ).run()
    assert second.stages["FACT_COMMIT"].status == "PASS"
    assert second.stages["FACT_COMMIT"].details["presence_only_rows"] == 1
    with connect(db) as handle:
        assert handle.execute("SELECT name_es FROM products WHERE official_sku='100'").fetchone()[0] == "Mesa"


def test_fact_missing_field_is_not_sent_to_translation(workflow_root, source_row, fake_provider, tmp_path):
    db = tmp_path / "fact-gate-translation.sqlite3"
    WorkflowV2Runner(
        root=workflow_root / "first", context=new_context(workflow_root / "first", business_date="2026-10-04", run_id="first-fact-gate"),
        records=[source_row], expected_skus={"100"}, temp_db=db,
        provider=fake_provider, auto_translation=True, auto_policy=True, apply_enabled=True,
    ).run()
    partial = dict(source_row)
    partial["name_es"] = ""
    second_provider = type(fake_provider)(mapping=fake_provider.mapping)
    second = WorkflowV2Runner(
        root=workflow_root / "second", context=new_context(workflow_root / "second", business_date="2026-10-04", run_id="second-fact-gate"),
        records=[partial], expected_skus={"100"}, temp_db=db,
        provider=second_provider, auto_translation=True, auto_policy=True, apply_enabled=True,
    ).run()
    assert second.context.fact_ready is False
    ready_fields = {item["field"] for item in second.stages["TRANSLATION_SOURCE_AUDIT"].details["ready_fields"]}
    assert "name_es" not in ready_fields
    with connect(Path(second.context.database_path)) as handle:
        queued_fields = [str(row[0]) for row in handle.execute("SELECT requested_fields FROM translation_queue WHERE run_id=?", (second.context.workflow_run_id,)).fetchall()]
    assert all("name" not in field for field in queued_fields)
    assert second_provider.calls <= 5


def test_pending_detail_does_not_erase_existing_es_localization(workflow_root, source_row, tmp_path):
    db = tmp_path / "detail-preserve.sqlite3"
    first = WorkflowV2Runner(
        root=workflow_root / "first", context=new_context(workflow_root / "first", business_date="2026-10-04", run_id="first-detail"),
        records=[source_row], expected_skus={"100"}, temp_db=db,
    ).run()
    assert first.context.source_commit_id
    partial = dict(source_row)
    partial["desc_es"] = ""
    partial["details_es"] = ""
    partial["detail_status"] = "DETAIL_PENDING"
    WorkflowV2Runner(
        root=workflow_root / "second", context=new_context(workflow_root / "second", business_date="2026-10-04", run_id="second-detail"),
        records=[partial], expected_skus={"100"}, temp_db=db,
    ).run()
    with connect(db) as handle:
        row = handle.execute("SELECT description,details FROM product_localizations WHERE official_sku='100' AND language='es'").fetchone()
        assert row[0] == "Mesa roja"
        assert row[1] == "Color: Rojo; Número del artículo: 100"


def test_export_staging_publishes_one_complete_set(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True, auto_export=True,
        allow_high_risk_auto_approval=True,
    ).run()
    assert result.stages["EXPORT_WRITE"].status == "PASS"
    staging = workflow_root / "2026-10-04" / result.context.workflow_run_id / "staging"
    assert (staging / "publish_manifest.json").exists()
    assert (staging / "es.json").exists()
    assert (staging / "zh.json").exists()


def test_existing_exporter_uses_localization_apply_commit_head(monkeypatch, workflow_root, source_row, fake_provider):
    calls = []

    def fake_export_catalog(cfg, *, language, export_date, no_images, run_id=None, **_kwargs):
        calls.append((language, run_id, str(cfg["storage"]["db_path"])))
        return {"output": f"{language}.xlsx", "manifest": f"{language}.manifest.json"}

    monkeypatch.setattr("action_tracker.exporting.service.export_catalog", fake_export_catalog)
    run_id = "export-head"
    runner = WorkflowV2Runner(
        root=workflow_root,
        context=new_context(workflow_root, business_date="2026-10-04", run_id=run_id),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True, auto_export=True,
        allow_high_risk_auto_approval=True,
        cfg={"paths": {"exports": workflow_root / "exports"}},
    )
    result = runner.run()
    assert result.stages["EXPORT_WRITE"].status == "PASS"
    assert [run for _language, run, _path in calls] == [
        f"{run_id}-localization-apply", f"{run_id}-localization-apply",
    ]
