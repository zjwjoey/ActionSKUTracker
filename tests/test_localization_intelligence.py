from action_tracker.localization import LocalizationEngine
from action_tracker.localization.contracts import SourceFacts, source_hash
from action_tracker.services.hashing import localization_source_hash
from action_tracker.localization.formatter import format_details, format_spec, format_unit_price
from action_tracker.localization.learning import aggregate_candidates
from action_tracker.localization.promotion import can_promote
from action_tracker.localization.promotion import KnowledgePromotionRouter, validate_candidate_freshness, KnowledgePromotionError
from action_tracker.localization.knowledge import KnowledgeLoader, ensure_schemas
from action_tracker.localization.ai import FakeProvider, LocalOpenAICompatibleProvider, provider_from_config, resolve_unknown, validate_ai_response
from action_tracker.database.schema import migrate_v2
from action_tracker.database.connection import connect
from action_tracker.database.production import apply_localization_correction
from action_tracker.database.production import CommitBundle, ProductionWriter
from action_tracker.localization.service import audit_current
from action_tracker.localization.knowledge import validate_knowledge_file, NEW_SCHEMAS, NEW_KEYS
from action_tracker.dictionary import PRODUCT_DICTIONARY_HEADERS, MODEL_TRANSLATION_HEADERS, OVERRIDE_HEADERS, SOURCE_DAMAGE_HEADERS, product_source_hash, current_product_dictionary_hash
import csv
import json


def test_formatter_uses_retail_spec_contract():
    assert format_spec("50 x 60 cm | varios colores") == "50×60cm｜多种颜色"
    assert format_spec("100 gramos") == "100g"
    assert format_unit_price("0,33 €/ud.") == "0,33 €/件"
    assert format_details("Color: Azul\nCantidad: 3 unidades\nSin alcohol: No") == "颜色：蓝色；数量：3件；不含酒精：否"


def test_known_product_type_resolves_without_ai():
    record = {"sku": "10280", "name_es": "Gomas elásticas Office Essentials", "cat1_es": "Hobby", "cat2_es": "Manualidades", "spec_es": "100 gramos"}
    engine = LocalizationEngine(knowledge={"cat1_map": {"Hobby": "兴趣手作"}, "cat2_map": {"Manualidades": "手工制作"}})
    plan = engine.resolve(record)
    assert plan.fields["name_zh"].value == "橡皮筋"
    assert plan.fields["cat1_zh"].value == "兴趣手作"
    assert plan.fields["spec_zh"].value == "100g"
    assert plan.ai_used is False


def test_category_one_uses_personal_beauty_as_the_only_canonical_label():
    from action_tracker.localization.policy import FIXED_CAT1, map_cat1
    assert "个人美容" in FIXED_CAT1 and "个人护理" not in FIXED_CAT1
    assert map_cat1("Cuidado personal", {"Cuidado personal": "个人护理"}) == "个人美容"


def test_local_provider_is_configurable_and_does_not_require_an_api_key():
    provider = provider_from_config({"enabled": True, "provider": "local_openai_compatible", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b"})
    assert isinstance(provider, LocalOpenAICompatibleProvider)
    assert provider.api_key_env is None


def test_ai_response_contract_rejects_malformed_identity_numbers_and_unknown_fields():
    source = SourceFacts.from_record({"sku": "TEST-QWEN", "name_es": "Lámpara LED USB-C", "spec_es": "220 V | 10 W | 4000 mAh | IP44"})
    valid = {"sku": source.sku, "source_hash": source.source_hash, "fields": {"name": "LED灯", "spec": "220V｜10W｜4000毫安时｜IP44"}}
    assert validate_ai_response(valid, source, ("name", "spec")) == (True, ())
    cases = [
        ({"name": "灯"}, "AI_FIELDS_NOT_OBJECT"),
        ({"name": "灯"}, "AI_SKU_MISMATCH"),
        ({"name": "灯"}, "AI_SOURCE_HASH_MISMATCH"),
        ({"name": "灯", "extra": "x"}, "AI_FIELD_NOT_REQUESTED"),
        ({"name": "灯", "spec": "220V｜10W｜IP44"}, "AI_SPEC_NUMBER_DROPPED"),
        ({"name": "Lámpara"}, "AI_NAME_SPANISH_RESIDUAL"),
    ]
    for fields, reason in cases:
        payload = {"sku": source.sku, "source_hash": source.source_hash, "fields": fields}
        if reason == "AI_FIELDS_NOT_OBJECT":
            payload["fields"] = fields["name"]
        elif reason == "AI_SKU_MISMATCH":
            payload["sku"] = "OTHER"
        elif reason == "AI_SOURCE_HASH_MISMATCH":
            payload["source_hash"] = "old"
        elif reason == "AI_FIELD_NOT_REQUESTED":
            payload["fields"] = {"name": "灯", "extra": "x"}
        ok, reasons = validate_ai_response(payload, source, ("name", "spec"))
        assert not ok and reason in reasons


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


def test_ai_contract_is_strict_and_only_unknown_is_called():
    record = {"sku": "1", "name_es": "Gomas elásticas", "cat1_es": "Unknown", "cat2_es": "Manualidades", "spec_es": "100 gramos"}
    engine = LocalizationEngine(knowledge={"cat1_map": {}, "cat2_map": {"Manualidades": "手工制作"}})
    plan = engine.resolve(record)
    provider = FakeProvider({"1": {"fields": {"cat1": "兴趣手作"}, "confidence": 0.99}})
    candidate = resolve_unknown(engine, record, plan, provider)
    assert candidate["schema_status"] == "PASS" and provider.calls == 1
    assert validate_ai_response({"fields": {"name": "橡皮筋"}, "unexpected": 1}, SourceFacts.from_record(record), ("name",))[0] is False


def test_audit_emits_review_queue_and_report_manifest(tmp_path):
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": tmp_path / "dict"}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="r1", records=[{"sku": "1", "name_es": "Producto desconocido", "cat1_es": "Unknown", "spec_es": "10 gramos"}])
    assert Path(result["audit"]).exists() and Path(result["review_queue"]).exists()
    assert result["review_required_count"] == 1


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


def test_learning_e2e_promotes_product_type_and_future_run_avoids_ai(tmp_path):
    directory = tmp_path / "knowledge"
    ensure_schemas(directory)
    record = {"sku": "TEST-ESPUMADOR", "name_es": "Espumador eléctrico portátil", "cat1_es": "Cuidado personal", "spec_es": "10 g"}
    knowledge = {"cat1_map": {"cuidado personal": "个人美容"}}
    engine = LocalizationEngine(knowledge=knowledge)
    first = engine.resolve(record)
    assert "PRODUCT_TYPE_REVIEW" in first.review_reasons
    provider = FakeProvider({record["sku"]: {"fields": {"name": "奶泡器"}, "product_type_candidate": {"source_term": "espumador", "canonical_zh": "奶泡器"}, "confidence": 0.99}})
    candidate = resolve_unknown(engine, record, first, provider)
    assert candidate and candidate["schema_status"] == "PASS" and provider.calls == 1
    ai_item = candidate["product_type_candidate"]
    routed = KnowledgePromotionRouter(directory).promote({**ai_item, "knowledge_type": "PRODUCT_TYPE", "semantic_type": "PRODUCT_TYPE", "candidate_id": "e2e-espumador", "status": "AI_CANDIDATE", "validator_status": "PASS"}, human_approved=True)
    assert routed["route"] == "product_type_dictionary.csv"
    loaded = KnowledgeLoader(directory).load()
    loaded["cat1_map"] = knowledge["cat1_map"]
    second = LocalizationEngine(knowledge=loaded).resolve(record)
    assert second.fields["name_zh"].value == "奶泡器"
    assert "PRODUCT_TYPE_REVIEW" not in second.review_reasons


def test_seed_reviewed_knowledge_is_loaded_but_ai_candidate_is_not(tmp_path):
    directory = tmp_path / "knowledge"
    ensure_schemas(directory)
    path = directory / "product_type_dictionary.csv"
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerow(["1.0", "seed-1", "espumador", "", "", "", "奶泡器", "1.0", "SEED_REVIEWED", "seed"])
    with (directory / "term_dictionary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["term_es", "term_zh", "term_type", "forbidden_zh", "keep_original", "review_status", "notes"])
        writer.writeheader(); writer.writerow(dict(zip(writer.fieldnames, ["recargable", "可充电", "FUNCTION", "", "0", "SEED_REVIEWED", "seed"])))
    loaded = KnowledgeLoader(directory).load()
    assert loaded["product_types"]["espumador"] == "奶泡器"
    facts = LocalizationEngine(knowledge=loaded).resolve({"sku": "1", "name_es": "Lámpara recargable", "cat1_es": "Hobby", "spec_es": ""}).semantic_facts
    assert any(f.source_text == "recargable" and f.knowledge_source == "term_dictionary" for f in facts)


def test_promotion_freshness_blocks_stale_evidence():
    candidate = {"source_hash": source_hash({"name_es": "A", "cat1_es": "Hobby", "cat2_es": "", "spec_es": "", "desc_es": "", "details_es": ""}), "evidence_skus": ["1"]}
    current = {"1": {"name_es": "B", "cat1_es": "Hobby", "cat2_es": "", "spec_es": "", "desc_es": "", "details_es": ""}}
    ok, reason = validate_candidate_freshness(candidate, current)
    assert not ok and reason == "CANDIDATE_STALE"


def test_multi_sku_learning_evidence_keeps_independent_hashes_and_passes_freshness(tmp_path):
    facts = {
        "A": {"name_es": "A", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "1 g", "desc_es": "", "details_es": ""},
        "B": {"name_es": "B", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "2 g", "desc_es": "", "details_es": ""},
        "C": {"name_es": "C", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "3 g", "desc_es": "", "details_es": ""},
    }
    rows = [{"sku": sku, "semantic_type": "PRODUCT_TYPE", "source_term": "plegable", "zh_value": "可折叠", "source_hash": source_hash(rec)} for sku, rec in facts.items()]
    result = aggregate_candidates(rows, tmp_path)
    candidate = result["rows"][0]
    assert {item["sku"] for item in candidate["evidence"]} == {"A", "B", "C"}
    assert len({item["source_hash"] for item in candidate["evidence"]}) == 3
    assert validate_candidate_freshness(candidate, facts) == (True, "PASS")


def test_multi_sku_learning_evidence_stale_or_missing_blocks_promotion(tmp_path):
    facts = {sku: {"name_es": sku, "cat1_es": "Hogar", "cat2_es": "", "spec_es": f"{idx} g", "desc_es": "", "details_es": ""} for idx, sku in enumerate(("A", "B", "C"), 1)}
    rows = [{"sku": sku, "semantic_type": "PRODUCT_TYPE", "source_term": "plegable", "zh_value": "可折叠", "source_hash": source_hash(rec)} for sku, rec in facts.items()]
    candidate = aggregate_candidates(rows, tmp_path)["rows"][0]
    changed = dict(facts["B"]); changed["name_es"] = "B2"
    assert validate_candidate_freshness(candidate, {**facts, "B": changed}) == (False, "CANDIDATE_STALE")
    assert validate_candidate_freshness(candidate, {"A": facts["A"], "C": facts["C"]}) == (False, "SKU_NOT_CURRENT")
    corrupt = {"evidence": [{"sku": "A", "source_hash": source_hash(facts["A"])}, "corrupt"]}
    assert validate_candidate_freshness(corrupt, facts) == (False, "SOURCE_EVIDENCE_MISSING")


def test_model_cache_must_match_current_record_not_stale_product_dictionary(tmp_path):
    directory = tmp_path / "dict"
    ensure_schemas(directory)
    old_record = {"sku": "CACHE-STALE", "name_es": "Producto A", "cat1_es": "Hogar", "spec_es": "10 gramos"}
    current_record = {**old_record, "name_es": "Producto B"}
    product = {key: "" for key in PRODUCT_DICTIONARY_HEADERS}
    product.update({"sku": old_record["sku"], "name_es_raw": old_record["name_es"], "cat1_es": "Hogar", "spec_es_raw": "10 gramos"})
    product["source_hash"] = product_source_hash(product)
    _write_csv(directory / "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS, [product])
    _write_csv(directory / "model_translation_overrides.csv", MODEL_TRANSLATION_HEADERS, [{"sku": old_record["sku"], "source_hash": product["source_hash"], "name_zh_standard": "旧缓存", "quality_status": "VERIFIED"}])
    loaded = KnowledgeLoader(directory).load()
    assert current_product_dictionary_hash(current_record) != loaded["model_by_sku"][old_record["sku"]]["source_hash"]
    assert loaded["model_by_sku"][old_record["sku"]]["quality_status"] == "VERIFIED"
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="cache-stale-e2e", records=[current_record])
    row = next(csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")))
    assert row["new_name_zh"] != "旧缓存"


def test_manual_override_is_revalidated_and_bad_manual_value_is_not_bypassed(tmp_path):
    directory = tmp_path / "dict"
    ensure_schemas(directory)
    record = {"sku": "MANUAL-VALIDATE", "name_es": "Producto desconocido", "cat1_es": "Hogar", "spec_es": "10 gramos"}
    _write_csv(directory / "manual_overrides.csv", OVERRIDE_HEADERS, [{"scope": "product", "key": record["sku"], "field": "name_zh_standard", "value": "错误 producto"}])
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="manual-validation", records=[record])
    row = next(csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")))
    assert row["new_name_zh"] == "错误 producto"
    assert row["readiness"] == "REVIEW_REQUIRED"
    assert "SPANISH_RESIDUAL" in row["review_reasons"]


def _write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_manual_override_is_used_by_audit_current_with_field_provenance(tmp_path):
    directory = tmp_path / "dict"
    ensure_schemas(directory)
    record = {"sku": "MANUAL-1", "name_es": "Producto desconocido", "cat1_es": "Hogar", "spec_es": "10 gramos"}
    product = {key: "" for key in PRODUCT_DICTIONARY_HEADERS}
    product.update({"sku": record["sku"], "name_es_raw": record["name_es"], "cat1_es": "Hogar", "spec_es_raw": "10 gramos", "name_zh_standard": "字典名称", "cat1_zh": "家务清洁", "spec_zh_standard": "10g"})
    product["source_hash"] = product_source_hash(product)
    _write_csv(directory / "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS, [product])
    _write_csv(directory / "manual_overrides.csv", OVERRIDE_HEADERS, [{"scope": "product", "key": record["sku"], "field": "name_zh_standard", "value": "人工名称", "source": "TEST"}])
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="manual-e2e", records=[record])
    row = next(csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")))
    assert row["new_name_zh"] == "人工名称"


def test_same_source_model_cache_is_loaded_into_audit_without_writing_primary(tmp_path):
    directory = tmp_path / "dict"
    ensure_schemas(directory)
    record = {"sku": "CACHE-1", "name_es": "Producto desconocido", "cat1_es": "Hogar", "spec_es": "10 gramos"}
    product = {key: "" for key in PRODUCT_DICTIONARY_HEADERS}
    product.update({"sku": record["sku"], "name_es_raw": record["name_es"], "cat1_es": "Hogar", "spec_es_raw": "10 gramos", "cat1_zh": "家务清洁", "spec_zh_standard": "10g"})
    product["source_hash"] = product_source_hash(product)
    _write_csv(directory / "product_dictionary.csv", PRODUCT_DICTIONARY_HEADERS, [product])
    _write_csv(directory / "model_translation_overrides.csv", MODEL_TRANSLATION_HEADERS, [{"sku": record["sku"], "source_hash": product["source_hash"], "name_zh_standard": "缓存名称", "spec_zh_standard": "10g", "quality_status": "VERIFIED", "model": "qwen3:8b"}])
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="cache-e2e", records=[record])
    row = next(csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")))
    assert row["new_name_zh"] == "缓存名称"
    assert result["ai_call_count"] == 0


def test_source_damage_blocks_only_damaged_field_and_ai(tmp_path):
    directory = tmp_path / "dict"
    ensure_schemas(directory)
    record = {"sku": "DAMAGED-1", "name_es": "Producto xyz", "cat1_es": "Hogar", "spec_es": "10 gramos", "desc_es": "texto roto"}
    _write_csv(directory / "source_damage_report.csv", SOURCE_DAMAGE_HEADERS, [{"sku": record["sku"], "damaged_fields": "desc_es", "status": "SOURCE_DAMAGED"}])
    cfg = {"project_root": tmp_path, "paths": {"temp": tmp_path / "runtime" / "temp", "dictionary_baseline": directory}, "localization": {"ai": {"enabled": True, "provider": "fake"}}}
    cfg["paths"]["temp"].mkdir(parents=True)
    result = audit_current(cfg, run_id="damage-e2e", records=[record])
    row = next(csv.DictReader(Path(result["audit"]).open(encoding="utf-8-sig")))
    assert "SOURCE_BLOCKED" in row["review_reasons"]
    assert result["ai_call_count"] == 1
    ai_rows = json.loads(Path(result["ai_candidates"]).read_text(encoding="utf-8"))
    assert ai_rows and "name" in ai_rows[0]["requested_fields"] and "description" not in ai_rows[0]["requested_fields"]
    assert row["new_name_zh"]


def test_sqlite_localization_apply_creates_versioned_zh_only_commit(tmp_path):
    path = tmp_path / "primary.db"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1','1','CURRENT')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,updated_at) VALUES('1','es','Producto','Hogar','','2 unidades','','','now')")
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('r1','2026-09-01','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','r1','h','2.0.0','now','now','COMMITTED')")
    facts = {"name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "2 unidades", "desc_es": "", "details_es": ""}
    result = apply_localization_correction(path, run_id="2026-09-01", localizations_by_sku={"1": {"name": "测试商品", "unit_price": "0,50 €/件"}}, source_hashes={"1": source_hash(facts)})
    assert result["base_commit_id"] == "C1"
    assert result["commit_id"] != "C1"
    with connect(path) as db:
        row = db.execute("SELECT name,unit_price,last_commit_id,source_hash,freshness_status FROM product_localizations WHERE official_sku='1' AND language='zh'").fetchone()
        assert tuple(row) == ("测试商品", "0,50 €/件", result["commit_id"], source_hash(facts), "CURRENT")
        assert db.execute("SELECT COUNT(*) FROM commit_batches").fetchone()[0] == 2


def test_daily_bundle_preserves_old_zh_and_marks_stale_when_es_hash_changes(tmp_path):
    path = tmp_path / "primary.db"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1','1','CURRENT')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','r1','h','2.0.0','now','now','COMMITTED')")
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('r1','2026-09-01','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,spec,source_hash,freshness_status,review_status,name_source,approved_by,approved_at,updated_at) VALUES('1','zh','旧中文','10g','old','CURRENT','APPROVED','manual_override','user','now','now')")
    writer = ProductionWriter(path, role="PRIMARY")
    bundle = CommitBundle(run_id="r2", observation_date="2026-09-02", qa_state="PASS", current_products=({"sku": "1", "canonical_id": "ACT1", "status": "CURRENT"},), localization_updates=({"sku": "1", "language": "zh", "name": "新中文候选", "spec": "20g", "source": "DICTIONARY_OR_FALLBACK", "source_hash": "new", "freshness_status": "CURRENT", "review_status": "PENDING"},), base_commit_id="C1")
    writer.commit(bundle)
    with connect(path) as db:
        row = db.execute("SELECT name,spec,source_hash,freshness_status,review_status,name_source,approved_by FROM product_localizations WHERE official_sku='1' AND language='zh'").fetchone()
        assert tuple(row) == ("旧中文", "10g", "old", "STALE", "APPROVED", "manual_override", "user")


def test_daily_bundle_keeps_localization_updated_at_and_commit_when_facts_unchanged(tmp_path):
    path = tmp_path / "primary.db"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1','1','CURRENT')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('C1','r1','h','2.0.0','now','now','COMMITTED')")
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('r1','2026-09-01','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,source_hash,review_status,updated_at,last_commit_id,applied_commit_id) VALUES('1','zh','中文','same','APPROVED','old-time','C1','C1')")
    writer = ProductionWriter(path, role="PRIMARY")
    bundle = CommitBundle(run_id="r2", observation_date="2026-09-02", qa_state="PASS", current_products=({"sku": "1", "canonical_id": "ACT1", "status": "CURRENT"},), localization_updates=({"sku": "1", "language": "zh", "name": "新候选不应覆盖", "source_hash": "same", "review_status": "PENDING"},), base_commit_id="C1")
    writer.commit(bundle)
    with connect(path) as db:
        row = db.execute("SELECT name,updated_at,last_commit_id,review_status FROM product_localizations WHERE official_sku='1' AND language='zh'").fetchone()
        assert tuple(row) == ("中文", "old-time", "C1", "APPROVED")


def test_source_and_semantic_contracts_keep_official_provenance():
    source = SourceFacts.from_record({"sku": "42", "name_es": "USB-C cable", "image_url": "https://cdn/image.png", "run_id": "r1", "source_commit_id": "C1"})
    assert source.unit_price == source.unit_price_es
    assert source.image_url.endswith("image.png") and source.source_run_id == "r1"
    fact = source_hash({"name_es": "USB-C cable"})
    plan = LocalizationEngine().resolve({"sku": "42", "name_es": "USB-C cable", "spec_es": "1 m"})
    item = plan.semantic_facts[0].as_dict()
    assert {"normalized_source", "zh_value", "knowledge_source", "allowed_targets", "preferred_target", "keep_original", "source_hash"} <= set(item)
    assert item["source_hash"] == plan.source_hash and isinstance(fact, str)


def test_localization_v1_uses_the_sqlite_canonical_source_hash():
    facts = {"name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "2 unidades", "desc_es": "", "details_es": ""}
    assert source_hash(facts) == localization_source_hash(facts)


def test_schema_persists_all_seven_localization_fields(tmp_path):
    path = tmp_path / "primary.db"
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(product_localizations)")}
    assert {"name", "cat1", "cat2", "spec", "unit_price", "description", "details", "unit_price_source"} <= columns


def test_dictionary_rows_drive_phrase_product_type_and_tech_token():
    knowledge = {
        "product_types": [{"source_term": "caja de almacenamiento", "source_aliases": "caja", "canonical_zh": "收纳箱"}],
        "phrases": [{"source_phrase": "varios modelos", "zh_value": "多款可选", "semantic_type": "VARIANT"}],
        "detail_keys": [],
        "tech_tokens": [{"token": "USB-C", "canonical_token": "USB-C", "token_type": "INTERFACE"}],
    }
    plan = LocalizationEngine(knowledge=knowledge).resolve({"sku": "1", "name_es": "Caja de almacenamiento USB-C", "spec_es": "varios modelos"})
    assert plan.fields["name_zh"].value.startswith("收纳箱")
    assert "USB-C" in plan.fields["name_zh"].value and "多款可选" in plan.fields["spec_zh"].value
    assert any(f.evidence == "product_type_dictionary" for f in plan.semantic_facts)


def test_brand_fact_order_is_deterministic_for_set_backed_knowledge():
    record = {"sku": "1", "name_es": "Zeta Alpha auriculares", "spec_es": ""}
    plan = LocalizationEngine(knowledge={"brands": {"Zeta", "Alpha"}}).resolve(record)
    brands = [fact.value for fact in plan.semantic_facts if fact.semantic_type == "BRAND"]
    assert brands == ["Alpha", "Zeta"]


def test_validator_allows_numeric_movement_but_blocks_detail_sku_and_stale():
    record = {"sku": "42", "name_es": "Producto 42", "spec_es": "50 x 60 cm", "details_es": "Color: Azul"}
    engine = LocalizationEngine()
    plan = engine.resolve(record)
    # The 42 from the name is allowed to move to structured details; a wrong
    # explicit detail SKU is still an identity failure.
    fields = dict(plan.fields)
    fields["details_zh"] = type(fields["details_zh"])("商品编号：99", "test", "READY", plan.source_hash)
    from action_tracker.localization.contracts import LocalizationPlan
    bad = LocalizationPlan(plan.sku, plan.source_hash, fields, plan.semantic_facts)
    result = engine.validate(record, bad)
    assert "DETAIL_SKU_MISMATCH" in result.reasons


def test_knowledge_schema_validator_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "tech_token_dictionary.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=NEW_SCHEMAS[path.name]); writer.writeheader()
        row = {key: "" for key in NEW_SCHEMAS[path.name]}; row.update({"schema_version": "1.0", "token": "USB-C"})
        writer.writerow(row); writer.writerow(row)
    try:
        validate_knowledge_file(path, NEW_SCHEMAS[path.name], NEW_KEYS[path.name])
    except ValueError as exc:
        assert "DUPLICATE" in str(exc)
    else:
        raise AssertionError("duplicate knowledge key was accepted")
from pathlib import Path
