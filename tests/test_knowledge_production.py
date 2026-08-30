from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.knowledge.approval import evaluate_candidate
from action_tracker.knowledge.contracts import KNOWLEDGE_FIELDS, source_hash
from action_tracker.knowledge.queue import build_queue
from action_tracker.knowledge.resolver import resolve
from action_tracker.knowledge.validator import validate_candidate
from action_tracker.knowledge.storage import KnowledgeStore


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
