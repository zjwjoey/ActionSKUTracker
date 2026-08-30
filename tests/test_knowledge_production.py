from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.services.hashing import localization_source_hash
from action_tracker.knowledge.approval import evaluate_candidate
from action_tracker.knowledge.contracts import KNOWLEDGE_FIELDS, source_hash
from action_tracker.knowledge.queue import build_queue
from action_tracker.knowledge.resolver import resolve
from action_tracker.knowledge.validator import validate_candidate
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.knowledge.scoped import ScopedRule, match_scoped, blast_radius
from action_tracker.knowledge.ai import ProviderResult, candidate_cache_key, run_candidates


def _record(**overrides):
    record = {
        "sku": "1001", "name_es": "Caja", "cat1_es": "Hogar", "cat2_es": "Cajas",
        "spec_es": "2 unidades", "desc_es": "Caja de plástico", "details_es": "Con tapa",
    }
    record.update(overrides)
    return record


def test_source_hash_is_stable_and_only_uses_six_spanish_fields():
    first = source_hash(_record(current_price=1.0, name_zh="不参与"))
    second = source_hash(_record(current_price=9.0, name_zh="仍不参与"))
    assert first == second
    assert first == source_hash(_record())
    assert first == localization_source_hash(_record())


def test_resolver_is_field_level_and_manual_wins():
    result = resolve(
        _record(),
        manual={"name": "人工名称"},
        product={"name": "商品字典名", "spec": "2件"},
        scoped={"cat1": "家居布置"},
        dictionaries={"cat2": "收纳用品"},
    )
    assert result.fields["name"].value == "人工名称"
    assert result.fields["name"].source == "manual_override"
    assert result.fields["spec"].source == "product_dictionary"
    assert result.fields["cat1"].source == "scoped_dictionary"
    assert result.readiness == "AI_PENDING"


def test_source_blocked_never_falls_back_to_spanish_or_ai():
    result = resolve(_record(source_quality="SOURCE_DAMAGED"), model_cache={"name": "猜测", "source_hash": "x", "validation_status": "PASS"})
    assert result.readiness == "SOURCE_BLOCKED"
    assert all(field.source == "source_blocked" for field in result.fields.values())


def test_valid_model_cache_is_reused_only_for_matching_source_hash():
    record = _record()
    result = resolve(record, model_cache={"name": "模型名", "source_hash": source_hash(record), "validation_status": "PASS"})
    assert result.fields["name"].source == "model_cache"
    stale = resolve(record, model_cache={"name": "旧模型名", "source_hash": "old", "validation_status": "PASS"})
    assert stale.fields["name"].source == "spanish_fallback"


def test_queue_is_incremental_and_deduplicated():
    record = _record(is_new=True)
    first = build_queue([record], run_id="r1")
    second = build_queue([record], run_id="r2")
    assert len(first) == len(second) == 1
    assert first[0]["queue_id"] == second[0]["queue_id"]
    assert first[0]["priority"] == "P0"


def test_queue_excludes_source_blocked():
    assert build_queue([_record(source_quality="SOURCE_POLLUTED", is_new=True)]) == []


def test_queue_detects_source_change_and_missing_localization():
    record = _record()
    rows = build_queue([record], localizations={"1001": {"source_hash": "old", "name": "旧名"}})
    assert rows[0]["reason"] == "SOURCE_HASH_CHANGED"
    assert "spec" in rows[0]["requested_fields"]


def test_queue_treats_missing_source_hash_as_stale():
    record = _record()
    rows = build_queue([record], localizations={"1001": {"name": "旧名"}}, run_id="r1")
    assert rows[0]["reason"] == "SOURCE_HASH_CHANGED"


def test_candidate_validator_rejects_sku_hash_and_url_contamination():
    record = _record()
    result = validate_candidate({"sku": "other", "source_hash": "old", "fields": {"name": "https://bad"}}, record)
    assert not result.ok
    assert {"SKU_MISMATCH", "SOURCE_HASH_MISMATCH", "NAME_URL_CONTAMINATION"} <= set(result.reasons)


def test_candidate_validator_accepts_well_formed_candidate():
    record = _record()
    result = validate_candidate({"sku": "1001", "source_hash": source_hash(record), "fields": {"name": "中文名", "cat1": "家居布置"}, "confidence": 0.98}, record)
    assert result.ok


def test_auto_approval_is_shadow_only_when_disabled():
    record = _record()
    candidate = {"sku": "1001", "source_hash": source_hash(record), "fields": {"cat1": "家居布置", "description": "描述"}, "confidence": 0.99}
    validation = validate_candidate(candidate, record)
    decisions = evaluate_candidate(candidate, validation, enabled=False, shadow=True)
    assert {d.field: d.decision for d in decisions} == {"cat1": "WOULD_AUTO_APPROVE", "description": "REVIEW_REQUIRED"}


def test_auto_approval_requires_validator_and_blocks_conflicts():
    record = _record()
    candidate = {"sku": "1001", "source_hash": source_hash(record), "fields": {"cat1": "家居布置"}, "confidence": 0.99}
    bad = validate_candidate({**candidate, "source_hash": "old"}, record)
    decision = evaluate_candidate(candidate, bad)[0]
    assert decision.decision == "REVIEW_REQUIRED"
    assert "SOURCE_HASH_MISMATCH" in decision.rules_failed
    good = validate_candidate(candidate, record)
    conflict = evaluate_candidate(candidate, good, human_conflict=True)[0]
    assert conflict.decision == "REVIEW_REQUIRED"


def test_auto_approval_invalid_confidence_fails_closed():
    record = _record()
    candidate = {"sku": "1001", "source_hash": source_hash(record), "fields": {"cat1": "家居布置"}, "confidence": "not-a-number"}
    validation = validate_candidate(candidate, record)
    decision = evaluate_candidate(candidate, validation)[0]
    assert decision.decision == "REVIEW_REQUIRED"
    assert "CONFIDENCE_INVALID" in decision.rules_failed


def test_schema_contains_knowledge_production_tables_and_provenance(tmp_path: Path):
    db_path = tmp_path / "knowledge.db"
    migrate_v2(db_path, role="SHADOW")
    with connect(db_path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"translation_resolution", "translation_queue", "translation_candidates", "translation_approval_audit"} <= tables
        columns = {row[1] for row in db.execute("PRAGMA table_info(product_localizations)")}
    assert {"source_hash", "resolution_status", "name_source", "details_source", "freshness_status", "approved_at"} <= columns


def test_contract_exposes_exact_six_knowledge_fields():
    assert KNOWLEDGE_FIELDS == ("name", "cat1", "cat2", "spec", "description", "details")


def test_knowledge_store_persists_preview_queue_candidate_and_audit_idempotently(tmp_path: Path):
    record = _record()
    resolution = resolve(record, product={field: "中文" for field in KNOWLEDGE_FIELDS})
    db_path = tmp_path / "store.db"
    store = KnowledgeStore(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1001','1001','CURRENT')")
    assert store.save_resolutions([resolution]) == 1
    queue = build_queue([record], localizations={"1001": {}}, run_id="r1")
    assert store.enqueue(queue) == 1
    candidate = {
        "queue_id": queue[0]["queue_id"], "sku": "1001", "source_hash": resolution.source_hash,
        "fields": {"cat1": "家居布置"}, "confidence": 0.99,
        "prompt_version": "translation_v1", "validation_status": "PASS",
    }
    cid = store.save_candidate(candidate)
    validation = validate_candidate({**candidate, "candidate_id": cid}, record)
    decisions = evaluate_candidate(candidate, validation)
    assert store.save_approval_audit(cid, "1001", resolution.source_hash, decisions) == 1
    assert store.enqueue(queue) == 1
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM translation_resolution").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM translation_queue").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM translation_candidates").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM translation_approval_audit").fetchone()[0] == 1


def test_knowledge_store_cannot_demote_primary_database(tmp_path: Path):
    db_path = tmp_path / "primary.db"
    migrate_v2(db_path, role="PRIMARY")
    try:
        KnowledgeStore(db_path, role="SHADOW")
    except ValueError as exc:
        assert "EXPLICIT_CUTOVER" in str(exc)
    else:
        raise AssertionError("knowledge store silently demoted a PRIMARY database")
    with connect(db_path) as db:
        assert db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()[0] == "PRIMARY"


def test_scoped_dictionary_uses_specificity_and_current_spanish_categories():
    record = _record(cat1_es="Hogar", cat2_es="Cajas")
    rules = [ScopedRule("g", "GLOBAL", None, "name", "Caja", "通用盒子", "HUMAN_APPROVED"),
             ScopedRule("c", "CAT2", "Cajas", "name", "Caja", "收纳盒", "HUMAN_APPROVED"),
             ScopedRule("p", "PRODUCT", "1001", "name", "Caja", "产品盒", "HUMAN_APPROVED")]
    assert match_scoped({**record, "source_term": "Caja"}, "name", rules).value == "产品盒"


def test_scoped_dictionary_same_level_conflict_fails_closed():
    record = {**_record(cat2_es="Cajas"), "source_term": "Caja"}
    rules = [ScopedRule("a", "CAT2", "Cajas", "name", "Caja", "A", "HUMAN_APPROVED"),
             ScopedRule("b", "CAT2", "Cajas", "name", "Caja", "B", "HUMAN_APPROVED")]
    result = match_scoped(record, "name", rules)
    assert result.conflict and not result.value


def test_scoped_dictionary_requires_human_approval_and_reports_blast_radius():
    rule = ScopedRule("r", "CAT1", "Hogar", "name", None, "家居", "PENDING")
    assert not match_scoped(_record(cat1_es="Hogar"), "name", [rule]).value
    approved = ScopedRule("r", "CAT1", "Hogar", "name", None, "家居", "HUMAN_APPROVED")
    report = blast_radius([_record(cat1_es="Hogar"), _record(sku="1002", cat1_es="DIY")], approved)
    assert report["matched_sku_count"] == 1


def test_ai_runner_is_candidate_only_and_isolates_failures():
    record = _record()
    queue = [{"queue_id": "q", "sku": "1001", "source_hash": source_hash(record), "requested_fields": ("name",), "status": "PENDING"}]
    result = run_candidates(queue, {"1001": record}, lambda _r, _f: ProviderResult({"name": "盒子"}, .99))
    assert result[0]["status"] == "CANDIDATE" and result[0]["validation_status"] == "PASS"
    assert "product_localizations" not in result[0]
    assert candidate_cache_key("1001", "h", "v1", ("name",)) != candidate_cache_key("1001", "h", "v2", ("name",))


def test_ai_runner_retries_and_marks_permanent_failure():
    record = _record()
    queue = [{"queue_id": "q", "sku": "1001", "source_hash": source_hash(record), "requested_fields": ("name",), "status": "PENDING"}]
    result = run_candidates(queue, {"1001": record}, lambda _r, _f: (_ for _ in ()).throw(RuntimeError("offline")), max_retries=2)
    assert result[0]["status"] == "FAILED" and result[0]["retry_count"] == 2


def test_field_level_apply_preserves_unmentioned_localization_and_requires_gate(tmp_path: Path):
    db_path = tmp_path / "primary.db"
    migrate_v2(db_path, role="PRIMARY")
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('ACT1001','1001','CURRENT')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,spec,updated_at) VALUES('1001','zh','旧名','旧规格','now')")
    store = KnowledgeStore(db_path, role="PRIMARY")
    record = _record()
    candidate = {"sku": "1001", "source_hash": source_hash(record), "fields": {"name": "新名"}, "approval_status": "HUMAN_APPROVED", "provenance": "human_approved_ai"}
    assert store.preview_apply([candidate], {"1001": record})[0]["old_value"] == "旧名"
    try:
        store.apply_localizations([candidate], {"1001": record})
    except PermissionError:
        pass
    else:
        raise AssertionError("disabled apply was not blocked")
    assert store.apply_localizations([candidate], {"1001": record}, enabled=True, commit_id="c1") == 1
    with connect(db_path) as db:
        row = db.execute("SELECT name,spec,name_source FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()
    assert tuple(row) == ("新名", "旧规格", "human_approved_ai")
