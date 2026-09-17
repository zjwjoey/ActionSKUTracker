from pathlib import Path

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
from action_tracker.localization.memory.repository import TranslationMemoryRepository
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
