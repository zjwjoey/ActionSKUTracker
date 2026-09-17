from pathlib import Path
from action_tracker.database.connection import connect

from action_tracker.localization.canonical_qa import canonical_guard
from action_tracker.localization.feedback import mine_family_feedback
from action_tracker.localization.normalization.structured_details import parse_structured_details
from action_tracker.localization.product_family import (
    UNKNOWN_FAMILY,
    build_translation_context,
    classify_product_family,
)
from action_tracker.localization.engine import LocalizationEngine
from action_tracker.localization.knowledge import KnowledgeLoader, ensure_schemas
from action_tracker.dictionary import PRODUCT_DICTIONARY_HEADERS
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.resolver import TranslationResolver, Resolution
from action_tracker.localization.memory.repository import TranslationMemoryRepository
from action_tracker.localization.runtime import shadow_run, canary
from action_tracker.localization.contracts import SourceFacts, source_hash
from action_tracker.knowledge.storage import KnowledgeStore
import csv


def test_cleaning_cloth_classifier_is_phrase_aware_and_fail_closed():
    match = classify_product_family({"sku": "1", "name_es": "Paño de microfibra para el suelo", "cat1_es": "Hogar", "cat2_es": "Limpieza"})
    assert match.family_id == "CLEANING_CLOTH"
    assert match.policy_version == "CLEANING_CLOTH_V1"
    assert classify_product_family({"sku": "2", "name_es": "Pañuelo decorativo", "cat1_es": "Hogar"}).family_id == UNKNOWN_FAMILY
    assert classify_product_family({"sku": "3", "name_es": "Paño"}).family_id == UNKNOWN_FAMILY


def test_family_context_has_source_hash_and_field_scope():
    record = {"sku": "1", "name_es": "Bayeta", "cat1_es": "Hogar", "cat2_es": "Limpieza"}
    plan = LocalizationEngine().resolve(record)
    context = build_translation_context(record, "name", semantic_facts=plan.semantic_facts)
    assert context.family_id == "CLEANING_CLOTH"
    assert context.field_name == "name"
    assert context.source_hash


def test_cleaning_cloth_canonical_rules_separate_name_description_and_material():
    record = {"sku": "1", "name_es": "Paño de microfibra", "cat1_es": "Hogar", "cat2_es": "Limpieza", "desc_es": "Paño de microfibra", "details_es": "Material: Goma"}
    plan = LocalizationEngine().resolve(record)
    name_context = build_translation_context(record, "name", semantic_facts=plan.semantic_facts)
    assert canonical_guard(name_context, {"name": "微纤维清洁布"})["status"] == "PASS"
    assert any(item["rule_id"] == "FAMILY_NAME_ORDER_VIOLATION" for item in canonical_guard(name_context, {"name": "清洁布微纤维"})["findings"])
    desc_context = build_translation_context(record, "description", semantic_facts=plan.semantic_facts)
    assert canonical_guard(desc_context, {"description": "超细纤维清洁布"})["status"] == "PASS"
    detail_context = build_translation_context(record, "details", semantic_facts=plan.semantic_facts, detail_key="material")
    assert canonical_guard(detail_context, {"details": "材质：橡皮筋"})["status"] == "FAIL"
    assert any(item["rule_id"] == "DETAIL_CONTEXT_TERMINOLOGY_VIOLATION" for item in canonical_guard(detail_context, {"details": "材质：橡皮筋"})["findings"])


def test_structured_details_parser_preserves_duplicate_keys_and_context():
    rows = parse_structured_details("Material:: Goma; Color: Azul; Tamaño: 20 x 30 cm")
    assert [row.context_key for row in rows] == ["material", "color", "size"]
    assert rows[0].value == "Goma"
    assert rows[2].value_type == "DIMENSION"


def test_feedback_mining_requires_threshold_and_never_approves():
    rows = [{"sku": str(i), "family_id": "CLEANING_CLOTH", "field_name": "name", "source_term": "paño", "old_target": "抹布", "human_target": "清洁布"} for i in range(3)]
    result = mine_family_feedback(rows, min_occurrences=3)
    assert len(result) == 1
    assert result[0]["suggested_action"] == "REVIEW_CANDIDATE"


def test_product_dictionary_trust_gate_and_status(tmp_path: Path):
    ensure_schemas(tmp_path)
    row = {key: "" for key in PRODUCT_DICTIONARY_HEADERS}
    row.update({"sku": "1", "review_status": "HUMAN_REVIEWED", "locked": "1", "name_zh_standard": "清洁布"})
    with (tmp_path / "product_dictionary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRODUCT_DICTIONARY_HEADERS); writer.writeheader(); writer.writerow(row)
    loaded = KnowledgeLoader(tmp_path).load()
    assert "1" in loaded["trusted_product_by_sku"]
    row["review_status"] = "MODEL_TRANSLATED"; row["locked"] = "0"
    with (tmp_path / "product_dictionary.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRODUCT_DICTIONARY_HEADERS); writer.writeheader(); writer.writerow(row)
    loaded = KnowledgeLoader(tmp_path).load()
    assert "1" not in loaded["trusted_product_by_sku"]
    assert loaded["product_trust_by_sku"]["1"] == "MODEL_TRANSLATED"


def test_translation_memory_family_scope_isolated(tmp_path: Path):
    registry = LocalizationRegistry(tmp_path / "registry.db")
    registry.add_tm("paño", "清洁布", field_name="name", family_id="CLEANING_CLOTH", context_key="name", approval_status="APPROVED")
    registry.add_tm("paño", "布", field_name="name", family_id="OTHER", context_key="name", approval_status="APPROVED")
    repo = TranslationMemoryRepository(tmp_path / "registry.db")
    assert repo.exact("paño", field_name="name", family_id="CLEANING_CLOTH", context_key="name").target_text == "清洁布"
    assert repo.exact("paño", field_name="name", family_id="OTHER", context_key="name").target_text == "布"


def test_scoped_terminology_conflict_fails_closed(tmp_path: Path):
    from action_tracker.localization.terminology.repository import TerminologyRepository
    registry = LocalizationRegistry(tmp_path / "registry.db")
    registry.add_term("Goma", "橡胶", field_scope="details", family_scope="CLEANING_CLOTH", context_key="material", approval_status="APPROVED", priority=5)
    registry.add_term("Goma", "橡皮筋", field_scope="details", family_scope="CLEANING_CLOTH", context_key="material", approval_status="APPROVED", priority=5)
    repo = TerminologyRepository(tmp_path / "registry.db")
    assert repo.resolve("Material: Goma", field_name="details", family_id="CLEANING_CLOTH", context_key="material") == ()
    assert len(repo.last_conflicts) == 1


def test_product_dictionary_reviewed_row_is_stale_when_source_hash_changes():
    record = {"sku": "1", "name_es": "Paño de microfibra", "cat1_es": "Hogar", "cat2_es": "Limpieza"}
    engine = LocalizationEngine(knowledge={"trusted_product_by_sku": {"1": {"source_hash": "old", "name_zh_standard": "旧值", "locked": "1"}}, "product_by_sku": {"1": {"source_hash": "old", "locked": "1"}}})
    plan = engine.resolve(record)
    assert plan.readiness == "REVIEW_REQUIRED"
    assert engine.knowledge["product_trust_by_sku"]["1"] == "STALE_LOCKED_KNOWLEDGE"


def test_shadow_and_canary_reports_carry_family_context(tmp_path: Path):
    record = {"sku": "1", "name_es": "Paño de microfibra", "cat1_es": "Hogar", "cat2_es": "Limpieza", "details_es": "Material: Goma"}
    shadow = shadow_run([record], output_dir=tmp_path / "shadow")
    assert shadow["production_writes"] is False
    assert (tmp_path / "shadow" / "translation_units.csv").read_text(encoding="utf-8-sig").splitlines()[0].find("family_id") >= 0
    canary_result = canary([record], output_dir=tmp_path / "canary", limit=1)
    assert canary_result["production_writes"] is False
    assert (tmp_path / "canary" / "translation_units.csv").exists()


def test_scoped_terminology_specific_rule_wins(tmp_path: Path):
    from action_tracker.localization.terminology.repository import TerminologyRepository
    registry = LocalizationRegistry(tmp_path / "registry.db")
    registry.add_term("Goma", "通用", approval_status="APPROVED", priority=10)
    registry.add_term("Goma", "橡胶", field_scope="details", family_scope="CLEANING_CLOTH", context_key="material", approval_status="APPROVED", priority=1)
    repo = TerminologyRepository(tmp_path / "registry.db")
    hints = repo.resolve("Material: Goma", field_name="details", family_id="CLEANING_CLOTH", context_key="material")
    assert hints and hints[0].target_term == "橡胶"


def _seed_family_worker(tmp_path: Path, target: str):
    db_path = tmp_path / f"family-worker-{target}.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    record = {
        "sku": "123456", "name_es": "Paño de microfibra",
        "cat1_es": "", "cat2_es": "", "spec_es": "",
        "desc_es": "", "details_es": "",
    }
    registry.ingest_records([record], source_run_id="family-run", observed_at="2026-09-17")
    class StubResolver:
        provider = None
        engine = LocalizationEngine()

        def resolve(self, record, *, allow_provider=False):
            return {"name": self.resolve_field(record, "name", allow_provider=allow_provider)}

        def resolve_field(self, record, field_name, *, allow_provider=False, context=None):
            source = SourceFacts.from_record(record)
            plan = self.engine.resolve(record)
            return Resolution(source.sku, field_name, source.name_es, source.source_hash, target,
                              "qwen_mt", "PENDING", False, False, provenance={
                                  "provider": "fixture", "model": "fixture",
                                  "request_hash": "rq", "response_hash": "rs",
                              })

    resolver = StubResolver()
    return db_path, registry, record, resolver


def test_worker_canonical_fail_is_reviewable_but_never_approved_or_projected(tmp_path: Path):
    db_path, registry, record, resolver = _seed_family_worker(tmp_path, "微纤维抹布")
    from action_tracker.localization.worker import TranslationQueueWorker
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1, worker_id="family-test")
    assert result.completed == 1 and result.blocked == 0 and result.review_required == 1
    with connect(db_path) as db:
        row = db.execute("SELECT r.qa_status,r.canonical_qa_status,r.review_status,q.status FROM translation_revisions r JOIN translation_units u ON u.current_revision_id=r.revision_id JOIN translation_queue q ON q.official_sku='123456'").fetchone()
        revision_id = db.execute("SELECT revision_id FROM translation_revisions").fetchone()[0]
        finding = db.execute("SELECT qa_layer,field_name,rule_id FROM translation_qa_findings WHERE revision_id=?", (revision_id,)).fetchone()
    assert tuple(row) == ("PASS", "FAIL", "PENDING", "COMPLETED")
    assert tuple(finding) == ("CANONICAL", "name", "FAMILY_PRODUCT_TYPE_NONCANONICAL")
    assert registry.approve_revision(revision_id, actor="human:test") is False
    assert KnowledgeStore(db_path).approved_registry_projection() == []


def test_worker_canonical_pass_can_be_approved_and_projected(tmp_path: Path):
    db_path, registry, record, resolver = _seed_family_worker(tmp_path, "微纤维清洁布")
    from action_tracker.localization.worker import TranslationQueueWorker
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1, worker_id="family-test")
    assert result.completed == 1 and result.review_required == 0
    with connect(db_path) as db:
        revision_id = db.execute("SELECT revision_id FROM translation_revisions").fetchone()[0]
        status = db.execute("SELECT canonical_qa_status FROM translation_revisions WHERE revision_id=?", (revision_id,)).fetchone()[0]
    assert status == "PASS"
    assert registry.approve_revision(revision_id, actor="human:test") is True
    assert len(KnowledgeStore(db_path).approved_registry_projection()) == 1


def test_canonical_context_rules_cover_name_description_and_detail_material():
    record = {"sku": "x", "name_es": "Paño de microfibra", "cat1_es": "Hogar", "cat2_es": "Limpieza", "desc_es": "Paño de microfibra", "details_es": "Material: Goma"}
    plan = LocalizationEngine().resolve(record)
    assert canonical_guard(build_translation_context(record, "name", semantic_facts=plan.semantic_facts), {"name": "清洁布微纤维"})["status"] == "FAIL"
    assert canonical_guard(build_translation_context(record, "description", semantic_facts=plan.semantic_facts), {"description": "采用微纤维材质"})["status"] == "FAIL"
    detail = canonical_guard(build_translation_context(record, "details", semantic_facts=plan.semantic_facts, detail_key="material"), {"details": "材质：橡胶"})
    assert detail["status"] == "PASS"
    assert detail["approval_blocking"] is False
