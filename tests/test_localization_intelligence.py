from action_tracker.localization import LocalizationEngine
from action_tracker.localization.contracts import SourceFacts, source_hash
from action_tracker.localization.formatter import format_spec, format_unit_price
from action_tracker.localization.learning import aggregate_candidates
from action_tracker.localization.promotion import can_promote
from action_tracker.database.schema import migrate_v2
from action_tracker.database.connection import connect
from action_tracker.database.production import apply_localization_correction


def test_formatter_uses_retail_spec_contract():
    assert format_spec("50 x 60 cm | varios colores") == "50×60cm｜多种颜色"
    assert format_spec("100 gramos") == "100g"
    assert format_unit_price("0,33 €/ud.") == "0,33 €/件"


def test_known_product_type_resolves_without_ai():
    record = {"sku": "10280", "name_es": "Gomas elásticas Office Essentials", "cat1_es": "Hobby", "cat2_es": "Manualidades", "spec_es": "100 gramos"}
    engine = LocalizationEngine(knowledge={"cat1_map": {"Hobby": "兴趣手作"}, "cat2_map": {"Manualidades": "手工制作"}})
    plan = engine.resolve(record)
    assert plan.fields["name_zh"].value == "橡皮筋"
    assert plan.fields["cat1_zh"].value == "兴趣手作"
    assert plan.fields["spec_zh"].value == "100g"
    assert plan.ai_used is False


def test_technical_tokens_are_preserved_and_numeric_facts_checked():
    record = {"sku": "40258", "name_es": "Alfombrilla para cortar", "spec_es": "A3 | 50x60 cm"}
    plan = LocalizationEngine().resolve(record)
    assert "A3" in plan.fields["spec_zh"].value
    assert "50×60cm" in plan.fields["spec_zh"].value
    validation = LocalizationEngine().validate(record, plan)
    assert not validation.numeric_mismatches


def test_validator_blocks_unformatted_spec_and_missing_tech_token():
    record = {"sku": "x", "name_es": "Concentrador USB-C", "spec_es": "4 puertos"}
    engine = LocalizationEngine()
    plan = engine.resolve(record)
    assert "USB-C" in plan.fields["name_zh"].value or any(f.value == "USB-C" for f in plan.semantic_facts)
    bad_record = {"sku": "x", "name_es": "Concentrador USB-C", "spec_es": "4 x 5 cm | varios colores"}
    bad = engine.resolve(bad_record)
    bad_fields = dict(bad.fields); bad_fields["spec_zh"] = type(bad.fields["spec_zh"])("4 x 5 cm | colores", "test", "READY", bad.source_hash)
    from action_tracker.localization.contracts import LocalizationPlan
    bad_plan = LocalizationPlan(bad.sku, bad.source_hash, bad_fields, bad.semantic_facts)
    result = engine.validate(bad_record, bad_plan)
    assert "SPEC_FORMAT_REVIEW" in result.reasons


def test_changed_spanish_facts_keep_old_zh_as_stale_until_retranslation():
    record = {"sku": "1", "name_es": "Gomas elásticas", "spec_es": "100 gramos"}
    engine = LocalizationEngine()
    plan = engine.resolve(record, existing={"name_zh": "旧名称", "source_hash": "old-hash", "freshness_status": "CURRENT"})
    assert plan.fields["name_zh"].value == "旧名称"
    assert plan.fields["name_zh"].freshness_status == "STALE"
    assert plan.fields["name_zh"].source_hash == "old-hash"


def test_learning_candidates_are_aggregated(tmp_path):
    result = aggregate_candidates([
        {"sku": "1", "semantic_type": "PRODUCT_TYPE", "source_term": "gomas", "zh_value": "橡皮筋"},
        {"sku": "2", "semantic_type": "PRODUCT_TYPE", "source_term": "gomas", "zh_value": "橡皮筋"},
    ], tmp_path)
    assert result["count"] == 1
    assert result["rows"][0]["occurrence_count"] == 2
    assert result["rows"][0]["status"] == "EVIDENCE_ACCUMULATED"


def test_promotion_requires_human_for_product_type():
    ok, reasons = can_promote({"semantic_type": "PRODUCT_TYPE", "status": "EVIDENCE_ACCUMULATED"}, validator_pass=True, source_hash_match=True)
    assert not ok and "HUMAN_APPROVAL_REQUIRED" in reasons
    ok, _ = can_promote({"semantic_type": "PRODUCT_TYPE", "status": "EVIDENCE_ACCUMULATED"}, validator_pass=True, source_hash_match=True, human_approved=True)
    assert ok


def test_sqlite_localization_apply_creates_versioned_zh_only_commit(tmp_path):
    path = tmp_path / "primary.db"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1','1','CURRENT')")
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('r1','2026-09-01','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','r1','h','2.0.0','now','now','COMMITTED')")
    result = apply_localization_correction(path, run_id="2026-09-01", localizations_by_sku={"1": {"name": "测试商品"}}, source_hashes={"1": "facts-hash"})
    assert result["base_commit_id"] == "C1"
    assert result["commit_id"] != "C1"
    with connect(path) as db:
        row = db.execute("SELECT name,last_commit_id,source_hash,freshness_status FROM product_localizations WHERE official_sku='1' AND language='zh'").fetchone()
        assert tuple(row) == ("测试商品", result["commit_id"], "facts-hash", "CURRENT")
        assert db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0] == 2
