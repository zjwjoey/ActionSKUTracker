from __future__ import annotations

import csv
from pathlib import Path

import pytest

from action_tracker.config import config_evidence, load_settings, validate_phase1_profile
from action_tracker.database.connection import connect
from action_tracker.database.production import CommitBundle, ProductionWriter
from action_tracker.services.hashing import localization_field_source_hashes, localization_source_hash
from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner, run_workflow_v2
from action_tracker.workflow_v2.export_audit import audit_zh


def _source(name="Producto", spec="10 cm"):
    return {
        "sku": "100", "canonical_id": "ACT100", "name_es": name,
        "cat1_es": "Hogar", "cat2_es": "Muebles", "spec_es": spec,
        "desc_es": "Mesa roja", "details_es": "Color: Rojo; Número del artículo: 100",
        "product_url": "https://www.action.com/es-es/p/100", "current_price": 4.99,
        "status": "CURRENT", "presence_source": "sitemap",
    }


def _cfg(tmp_path: Path, *, evidence_hash="runtime-hash"):
    return {
        "project_root": tmp_path,
        "storage": {"mode": "SQLITE_SHADOW", "db_path": str(tmp_path / "db.sqlite3")},
        "paths": {},
        "workflow_v2": {"enabled": True, "translation_batch_limit": 50},
        "_config_evidence": {
            "base_config_path": str(tmp_path / "settings.yaml"),
            "profile_path": str(tmp_path / "profile.yaml"),
            "base_config_sha256": "base",
            "profile_sha256": "profile",
            "effective_config_hash": evidence_hash,
        },
    }


def _localization_rows(source, *, zh_values=None, freshness="CURRENT"):
    zh_values = zh_values or {"name": "旧商品", "cat1": "家居", "cat2": "家具", "spec": "10 厘米", "description": "红色桌子", "details": "颜色：红色"}
    source_hash = localization_source_hash(source)
    field_hashes = localization_field_source_hashes(source)
    es = {
        "sku": "100", "language": "es", "name": source["name_es"], "cat1": source["cat1_es"],
        "cat2": source["cat2_es"], "spec": source["spec_es"], "description": source["desc_es"],
        "details": source["details_es"], "source": "OFFICIAL_FACT", "review_status": "VERIFIED",
        "source_hash": source_hash, **{f"{field}_source_hash": value for field, value in field_hashes.items()},
    }
    zh = {
        "sku": "100", "language": "zh", **zh_values, "source": "LOCALIZATION",
        "review_status": "APPROVED", "freshness_status": freshness, "source_hash": source_hash,
        **{f"{field}_source_hash": value for field, value in field_hashes.items()},
        **{f"{field}_freshness_status": freshness for field in field_hashes},
    }
    return es, zh


def _bundle(run_id, source, localizations, *, base=None):
    return CommitBundle(
        run_id=run_id, observation_date="2026-10-03", base_commit_id=base, qa_state="PASS",
        current_products=({"sku": "100", "name_es": source["name_es"], "current_price": 4.99, "status": "CURRENT"},),
        localization_updates=tuple(localizations),
        observations=({"run_id": run_id, "sku": "100", "observation_date": "2026-10-03", "presence_state": "PRESENT", "observation_complete": True, "absence_capable": True},),
    )


def test_default_settings_remain_fail_closed():
    cfg = load_settings()
    assert cfg["workflow_v2"]["enabled"] is False
    assert cfg["workflow_v2"]["auto_translation"]["enabled"] is False
    assert cfg["localization"]["ai"]["enabled"] is False


def test_profile_overlay_deep_merge_and_evidence():
    import yaml

    base = Path("tests") / "_runtime_base.yaml"
    profile = Path("tests") / "_runtime_profile.yaml"
    base.write_text(yaml.safe_dump({"workflow_v2": {"enabled": False, "detail_retry": {"enabled": True}}, "localization": {"ai": {"enabled": False}}}), encoding="utf-8")
    profile.write_text(yaml.safe_dump({"workflow_v2": {"enabled": True, "translation_batch_limit": 50, "detail_retry": {"enabled": False}, "auto_translation": {"enabled": True, "provider": "qwen_mt"}, "auto_policy_approval": {"enabled": False}, "auto_export": {"enabled": False}}, "localization": {"ai": {"enabled": True, "provider": "qwen_mt"}, "production_apply_enabled": False}, "knowledge": {"production_apply_enabled": False, "fallback_to_spanish": False}}), encoding="utf-8")
    try:
        cfg = load_settings(path=base, overlay_paths=[profile])
    finally:
        base.unlink(missing_ok=True)
        profile.unlink(missing_ok=True)
    assert validate_phase1_profile(cfg) == []
    assert cfg["workflow_v2"]["translation_batch_limit"] == 50
    assert cfg["workflow_v2"]["detail_retry"]["enabled"] is False
    evidence = config_evidence(cfg)
    assert evidence["profile_path"].endswith("_runtime_profile.yaml")
    assert all(evidence[key] for key in ("base_config_sha256", "profile_sha256", "effective_config_hash"))
    assert "DASHSCOPE_API_KEY" not in evidence


def test_cli_profile_is_explicit_and_environment_is_fallback():
    from action_tracker.cli import build_parser

    args = build_parser().parse_args(["data-update-v2", "--profile", "explicit.yaml"])
    assert args.profile == "explicit.yaml"
    # The parser does not invent a profile; precedence is resolved by main().
    assert build_parser().parse_args(["data-update-v2"]).profile is None


def test_phase1_requires_profile_and_rejects_apply_enabled_profile(tmp_path):
    with pytest.raises(ValueError, match="PRODUCTION_TRANSLATION_PROFILE_REQUIRED"):
        run_workflow_v2({"project_root": tmp_path, "workflow_v2": {"enabled": True}}, production_mode=True)
    cfg = _cfg(tmp_path)
    cfg["knowledge"] = {"production_apply_enabled": True, "fallback_to_spanish": False}
    with pytest.raises(ValueError, match="PRODUCTION_TRANSLATION_PROFILE_INVALID"):
        run_workflow_v2(cfg, production_mode=True)


def test_workflow_business_date_is_forwarded_to_extraction(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    with (snapshot / "products_normalized.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_source()))
        writer.writeheader(); writer.writerow(_source())
    captured = {}
    runner = WorkflowV2Runner(
        root=tmp_path / "reports", context=new_context(tmp_path / "reports", business_date="2026-10-03", run_id="wf-1"),
        cfg=_cfg(tmp_path), records=None,
    )

    def fake_daily(_cfg, **kwargs):
        captured.update(kwargs)
        return {"run_id": "wf-1", "business_date": "2026-10-03", "snapshot_dir": str(snapshot)}

    import action_tracker.orchestrator.daily as daily
    original = daily.run_daily
    daily.run_daily = fake_daily
    try:
        result = runner._default_extraction_adapter(cfg=runner.cfg, business_date="2026-10-03", workflow_run_id="wf-1")
    finally:
        daily.run_daily = original
    assert captured["business_date"] == "2026-10-03"
    assert captured["workflow_run_id"] == "wf-1"
    assert result["business_date"] == "2026-10-03"


def test_extraction_date_mismatch_blocks_commit(tmp_path):
    runner = WorkflowV2Runner(
        root=tmp_path / "reports", context=new_context(tmp_path / "reports", business_date="2026-10-03", run_id="wf-2"),
        cfg=_cfg(tmp_path), records=None, extraction_adapter=lambda **_: {"records": [_source()], "business_date": "2026-10-04"},
    )
    result = runner.run()
    assert result.stages["EXTRACT"].error_code == "EXTRACTION_BUSINESS_DATE_MISMATCH"
    assert result.stages["FACT_COMMIT"].status == "BLOCKED_BY_DEPENDENCY"


def test_resume_rejects_changed_profile_hash(tmp_path):
    cfg = _cfg(tmp_path, evidence_hash="hash-a")
    first = WorkflowV2Runner(root=tmp_path / "reports", context=new_context(tmp_path / "reports", business_date="2026-10-03", run_id="resume-1"), cfg=cfg, records=[_source()]).run()
    assert first.context.business_date == "2026-10-03"
    changed = _cfg(tmp_path, evidence_hash="hash-b")
    resumed = WorkflowV2Runner(root=tmp_path / "reports", context=new_context(tmp_path / "reports", business_date="2026-10-03", run_id="resume-1"), cfg=changed, records=None)
    with pytest.raises(ValueError, match="CONFIG_CHANGED_SINCE_RUN"):
        resumed.run(resume=True)


def test_changed_es_name_stales_only_name_and_preserves_zh(tmp_path):
    db = tmp_path / "freshness.sqlite3"
    old = _source(name="Producto viejo")
    es, zh = _localization_rows(old)
    first = ProductionWriter(db).commit(_bundle("r1", old, (es, zh)))
    new = _source(name="Producto nuevo")
    new_es, new_zh = _localization_rows(new, zh_values={key: "" for key in ("name", "cat1", "cat2", "spec", "description", "details")}, freshness="PENDING")
    ProductionWriter(db).commit(_bundle("r2", new, (new_es, new_zh), base=first))
    with connect(db) as conn:
        wide = conn.execute("SELECT name,source_hash,freshness_status FROM product_localizations WHERE official_sku='100' AND language='zh'").fetchone()
        fields = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT field_name,value,freshness_status FROM localization_fields WHERE official_sku='100' AND language='zh'")}
    assert wide[0] == "旧商品"
    assert wide[2] == "STALE"
    assert fields["name"] == ("旧商品", "STALE")
    assert fields["cat1"][1] == "CURRENT"
    assert fields["spec"][1] == "CURRENT"


def test_changed_es_spec_stales_only_spec_and_preserves_other_fields(tmp_path):
    db = tmp_path / "freshness-spec.sqlite3"
    old = _source(spec="10 cm")
    es, zh = _localization_rows(old)
    first = ProductionWriter(db).commit(_bundle("r1", old, (es, zh)))
    new = _source(spec="20 cm")
    new_es, new_zh = _localization_rows(new, zh_values={key: "" for key in ("name", "cat1", "cat2", "spec", "description", "details")}, freshness="PENDING")
    ProductionWriter(db).commit(_bundle("r2", new, (new_es, new_zh), base=first))
    with connect(db) as conn:
        fields = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT field_name,value,freshness_status FROM localization_fields WHERE official_sku='100' AND language='zh'")}
    assert fields["spec"] == ("10 厘米", "STALE")
    assert fields["name"] == ("旧商品", "CURRENT")
    assert fields["description"] == ("红色桌子", "CURRENT")


def test_registry_and_projection_freshness_match(tmp_path):
    db = tmp_path / "freshness-parity.sqlite3"
    old = _source(name="Producto viejo")
    es, zh = _localization_rows(old)
    first = ProductionWriter(db).commit(_bundle("r1", old, (es, zh)))
    new = _source(name="Producto nuevo")
    new_es, new_zh = _localization_rows(new, zh_values={key: "" for key in ("name", "cat1", "cat2", "spec", "description", "details")}, freshness="PENDING")
    ProductionWriter(db).commit(_bundle("r2", new, (new_es, new_zh), base=first))
    with connect(db) as conn:
        projection = conn.execute("SELECT freshness_status FROM product_localizations WHERE official_sku='100' AND language='zh'").fetchone()[0]
        registry = {row[0]: row[1] for row in conn.execute("SELECT field_name,freshness_status FROM localization_fields WHERE official_sku='100' AND language='zh'")}
    assert projection == "STALE"
    assert registry["name"] == "STALE"
    assert registry["cat1"] == "CURRENT"


def test_explicit_historical_business_date_reaches_fact_registry_and_export(tmp_path):
    context = new_context(tmp_path / "reports", business_date="2026-10-03", run_id="historical-1")
    result = WorkflowV2Runner(root=tmp_path / "reports", context=context, cfg=_cfg(tmp_path), records=[_source()]).run()
    assert result.context.business_date == "2026-10-03"
    assert result.report["business_date"] == "2026-10-03"
    with connect(Path(result.context.database_path)) as conn:
        assert conn.execute("SELECT observation_date FROM observations WHERE run_id=?", (result.context.workflow_run_id,)).fetchone()[0] == "2026-10-03"
        assert conn.execute("SELECT observed_at FROM translation_source_versions WHERE source_run_id=?", (result.context.workflow_run_id,)).fetchone()[0] == "2026-10-03"


def test_cross_midnight_does_not_change_business_date(tmp_path, monkeypatch):
    import action_tracker.workflow_v2.context as context_module
    from datetime import datetime
    from zoneinfo import ZoneInfo

    before = datetime(2026, 10, 3, 23, 59, 59, tzinfo=ZoneInfo("Europe/Madrid"))
    after = datetime(2026, 10, 4, 0, 0, 5, tzinfo=ZoneInfo("Europe/Madrid"))
    monkeypatch.setattr(context_module, "madrid_now", lambda: before)
    context = new_context(tmp_path / "reports", run_id="midnight-1")

    def extraction(**kwargs):
        monkeypatch.setattr(context_module, "madrid_now", lambda: after)
        assert kwargs["business_date"] == "2026-10-03"
        return {"records": [_source()], "business_date": kwargs["business_date"]}

    result = WorkflowV2Runner(root=tmp_path / "reports", context=context, cfg=_cfg(tmp_path), records=None, extraction_adapter=extraction).run()
    assert result.context.business_date == "2026-10-03"
    assert result.report["business_date"] == "2026-10-03"


def test_unchanged_es_keeps_approved_zh_current(tmp_path):
    db = tmp_path / "freshness.sqlite3"
    source = _source()
    es, zh = _localization_rows(source)
    first = ProductionWriter(db).commit(_bundle("r1", source, (es, zh)))
    _, placeholder = _localization_rows(source, zh_values={key: "" for key in ("name", "cat1", "cat2", "spec", "description", "details")}, freshness="PENDING")
    ProductionWriter(db).commit(_bundle("r2", source, (es, placeholder), base=first))
    with connect(db) as conn:
        row = conn.execute("SELECT name,freshness_status FROM product_localizations WHERE official_sku='100' AND language='zh'").fetchone()
        assert tuple(row) == ("旧商品", "CURRENT")


def test_new_sku_does_not_fake_current_localization(tmp_path):
    db = tmp_path / "freshness.sqlite3"
    source = _source()
    es, zh = _localization_rows(source, zh_values={key: "" for key in ("name", "cat1", "cat2", "spec", "description", "details")}, freshness="PENDING")
    ProductionWriter(db).commit(_bundle("new", source, (es, zh)))
    with connect(db) as conn:
        assert conn.execute("SELECT freshness_status FROM product_localizations WHERE official_sku='100' AND language='zh'").fetchone()[0] == "PENDING"
        assert conn.execute("SELECT DISTINCT freshness_status FROM localization_fields WHERE official_sku='100' AND language='zh'").fetchone()[0] == "PENDING"


def test_stale_zh_blocks_release():
    row = {
        "sku": "100", "translation_status": "PASS", "name_es": "Producto", "cat1_es": "Hogar",
        "name_zh": "旧商品", "cat1_zh": "家居", "zh_field_provenance": {
            "name": {"value": "旧商品", "freshness_status": "STALE"},
            "cat1": {"value": "家居", "freshness_status": "CURRENT"},
        },
    }
    assert audit_zh([row])["status"] == "FAIL"
