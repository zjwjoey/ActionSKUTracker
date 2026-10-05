import pytest

from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner
from action_tracker.workflow_v2.runner import run_workflow_v2
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from pathlib import Path


def test_fake_qwen_flow(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"), records=[source_row], expected_skus={"100"}, provider=fake_provider, auto_translation=True, auto_policy=True, apply_enabled=True).run()
    # Translation queue units are field-scoped; one SKU with six requested
    # Chinese fields results in six provider calls.
    assert result.stages["QWEN_TRANSLATE"].details["called"] == 6
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


def test_resume_accepts_human_approval_for_high_risk_fields(workflow_root, source_row, fake_provider):
    first = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04", run_id="human-approval"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True,
    ).run()
    assert first.stages["TRANSLATION_POLICY"].status == "BLOCKED"
    high_risk = {"name_es", "desc_es", "details_es"}
    with connect(Path(first.context.database_path)) as handle:
        revision_ids = [str(row[0]) for row in handle.execute(
            "SELECT r.revision_id FROM translation_revisions r JOIN translation_units u ON u.current_revision_id=r.revision_id WHERE u.field_name IN (?,?,?)",
            tuple(sorted(high_risk)),
        ).fetchall()]
    registry = LocalizationRegistry(Path(first.context.database_path), role="PRIMARY")
    for revision_id in revision_ids:
        assert registry.approve_revision(revision_id, actor="human:test") is True
    resumed = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04", run_id=first.context.workflow_run_id),
        records=None, expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True,
        temp_db=Path(first.context.database_path),
    ).run(resume=True)
    assert resumed.stages["TRANSLATION_POLICY"].status == "PASS"
    assert resumed.context.translation_ready is True


def test_translation_apply_reports_the_real_commit_id(workflow_root, source_row, fake_provider):
    result = WorkflowV2Runner(
        root=workflow_root, context=new_context(workflow_root, business_date="2026-10-04"),
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        auto_translation=True, auto_policy=True, apply_enabled=True,
        auto_export=False, allow_high_risk_auto_approval=True,
    ).run()
    applied = result.stages["TRANSLATION_APPLY"]
    assert applied.status == "PASS"
    assert result.context.localization_commit_id
    assert result.context.localization_commit_id == applied.details["commit_id"]
    assert result.context.localization_commit_id != f"localization-{result.context.workflow_run_id}"


def test_production_apply_targets_configured_primary_and_creates_backup(tmp_path, source_row, fake_provider):
    primary = tmp_path / "primary.sqlite3"
    migrate_v2(primary, role="PRIMARY")
    cfg = {
        "project_root": tmp_path,
        "storage": {"mode": "SQLITE_PRIMARY", "db_path": str(primary)},
        "workflow_v2": {"enabled": True},
        "knowledge": {"production_apply_enabled": True},
        "localization": {"production_apply_enabled": True, "ai": {"enabled": False}},
        "paths": {"backups": tmp_path / "backups"},
    }
    result = run_workflow_v2(
        cfg, business_date="2026-10-04", run_id="production-target",
        records=[source_row], expected_skus={"100"}, provider=fake_provider,
        dry_run=False, auto_translation=True, auto_policy=True, auto_export=False,
        apply_enabled=True, production_apply=True, allow_high_risk_auto_approval=True,
    )
    assert Path(result["context"]["database_path"]).resolve() == primary.resolve()
    assert result["stages"]["BACKUP"]["status"] == "PASS"
    backup = tmp_path / "backups" / "workflow_v2" / "2026-10-04" / "production-target.sqlite3"
    assert backup.exists()


def test_production_apply_rejects_dry_run_before_creating_local_db(tmp_path):
    cfg = {
        "project_root": tmp_path,
        "storage": {"mode": "SQLITE_PRIMARY", "db_path": str(tmp_path / "primary.sqlite3")},
        "workflow_v2": {"enabled": True},
        "knowledge": {"production_apply_enabled": True},
        "localization": {"production_apply_enabled": True},
    }
    with pytest.raises(ValueError, match="WORKFLOW_V2_PRODUCTION_REQUIRES_NO_DRY_RUN"):
        run_workflow_v2(cfg, production_apply=True)


def test_production_formal_export_pair_is_published_to_configured_root(tmp_path):
    from action_tracker.workflow_v2.runner import WorkflowV2Runner

    export_root = tmp_path / "exports"
    formal_root = tmp_path / "formal"
    formal_root.mkdir()
    files = {
        "es": (formal_root / "es.xlsx", formal_root / "es.manifest.json"),
        "zh": (formal_root / "zh.xlsx", formal_root / "zh.manifest.json"),
    }
    for output, manifest in files.values():
        output.write_bytes(output.name.encode())
        manifest.write_text("{}", encoding="utf-8")
    runner = WorkflowV2Runner(
        root=tmp_path / "reports", context=new_context(tmp_path / "reports", business_date="2026-10-04"),
        production_apply=True, cfg={"paths": {"exports": export_root}},
    )
    runner._publish_formal_exports({
        language: {"output": str(output), "manifest": str(manifest)}
        for language, (output, manifest) in files.items()
    })
    assert sorted(path.name for path in export_root.iterdir()) == [
        "es.manifest.json", "es.xlsx", "zh.manifest.json", "zh.xlsx",
    ]


def test_canary_export_does_not_fail_without_legacy_formal_run(tmp_path, monkeypatch):
    from action_tracker.exporting.service import ExportValidationError

    runner = WorkflowV2Runner(
        root=tmp_path / "reports",
        context=new_context(tmp_path / "reports", business_date="2026-10-04"),
        auto_export=True,
        cfg={"paths": {"exports": tmp_path / "exports"}},
    )
    runner.context.export_ready = True
    runner.es_projection = [{"编号": "100"}]
    runner.zh_projection = [{"编号": "100"}]

    def missing_formal_run(*args, **kwargs):
        raise ExportValidationError("FORMAL_RUN_NOT_FOUND: synthetic")

    monkeypatch.setattr("action_tracker.exporting.service.export_catalog", missing_formal_run)
    result = runner._export_write()
    assert result.status == "PASS"
    assert result.details["formal_exports"]["es"]["status"] == "SKIPPED"
