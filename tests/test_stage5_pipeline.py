from __future__ import annotations

import importlib.util
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from action_tracker.stage5 import pipeline as stage5_pipeline
from action_tracker.exporting.dictionary_join import DictionaryContext
from action_tracker.services.hashing import localization_source_hash
from action_tracker.stage5.pipeline import (
    ContractError,
    ImmutableArtifactError,
    Stage5Contracts,
    apply_owner_correction,
    build_batch,
    environment_manifest,
    load_owner_correction_manifest,
    load_contracts,
    model_input_payload,
    model_requests,
    plan_batch,
    sha256_file,
    sha256_tree,
    tokenizer_sha256,
    validate_frozen_identity,
    validate_input_rows,
    write_batch_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


def _source(**changes):
    value = {
        "name": "Lámpara USB-C A4",
        "cat1": "Hogar",
        "cat2": "Iluminación",
        "spec": "4 voltios",
        "description": "Incluye 2 piezas de 10,5 cm",
        "details": "Protección IP44; potencia 18,5 vatios",
    }
    value.update(changes)
    return value


def _hash(source):
    return localization_source_hash({
        "name_es": source["name"], "cat1_es": source["cat1"], "cat2_es": source["cat2"],
        "spec_es": source["spec"], "desc_es": source["description"], "details_es": source["details"],
    })


def _row(source=None, *, assistant=False):
    source = source or _source()
    messages = [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}]
    if assistant:
        messages.append({"role": "assistant", "content": "{}"})
    return {
        "messages": messages,
        "metadata": {
            "batch_id": "batch-01", "run_id": "run-01", "observation_date": "2026-09-10",
            "sku": "1001", "canonical_id": "1001", "source_hash": _hash(source),
            "selection_reasons": ["NEEDS_REVIEW"],
        },
    }


def _context(source):
    digest = hashlib.sha256(
        "\x1f".join(source[field] for field in ("name", "cat1", "cat2", "spec")).encode("utf-8")
    ).hexdigest()
    return DictionaryContext(
        directory=ROOT / "data/dictionary",
        product_by_sku={
            "1001": {
                "sku": "1001", "source_hash": digest, "translation_status": "HUMAN_REVIEWED",
                "review_status": "APPROVED", "name_zh_standard": "USB-C A4灯",
                "spec_zh_standard": "4V", "cat1_zh": "家居布置", "cat2_zh": "照明用品",
                "brand_id": "",
            }
        },
        manual_by_sku={}, model_by_sku={}, brand_by_id={}, category_by_pair={}, category_by_cat1={},
        terms=(), damage_by_sku={}, brand_reference_keys=frozenset(), unresolved_brand_ids=frozenset(),
        content_hash="dictionary-hash", source_quality_by_sku={}, allow_provisional_brands=False,
    )


def _identity():
    return {
        "base_model_config_sha256": "base", "tokenizer_sha256": "tokenizer",
        "adapter_tree_sha256": "adapter", "stage4_inference_contract_sha256": "stage4",
        "model_path": "model", "adapter_path": "adapter-path", "stage4_full_release": False,
        "training_authorized": False, "production_writes_authorized": False,
    }


def test_stage5_input_rejects_reference_target_and_source_hash_change():
    contracts = load_contracts(ROOT)
    with pytest.raises(ContractError, match="SOURCE_MESSAGE_ROLES_INVALID"):
        validate_input_rows([_row(assistant=True)], contracts)
    row = _row()
    row["metadata"]["source_hash"] = "stale"
    with pytest.raises(ContractError, match="SOURCE_HASH_MISMATCH"):
        validate_input_rows([row], contracts)


def test_shared_term_resolver_implementation_is_bound_to_stage5_identity(monkeypatch):
    before = load_contracts(ROOT)
    term_resolver_path = ROOT / "src/action_tracker/translation/term_resolver.py"
    original_sha256_file = stage5_pipeline.sha256_file

    def changed_term_resolver_hash(path):
        if Path(path) == term_resolver_path:
            return "0" * 64
        return original_sha256_file(path)

    monkeypatch.setattr(stage5_pipeline, "sha256_file", changed_term_resolver_hash)
    after = load_contracts(ROOT)

    assert before.hashes["pipeline"] != after.hashes["pipeline"]


def test_approved_term_qa_implementation_is_bound_to_stage5_identity(monkeypatch):
    before = load_contracts(ROOT)
    checker_path = ROOT / "src/action_tracker/translation/approved_terms.py"
    original_sha256_file = stage5_pipeline.sha256_file

    def changed_checker_hash(path):
        if Path(path) == checker_path:
            return "f" * 64
        return original_sha256_file(path)

    monkeypatch.setattr(stage5_pipeline, "sha256_file", changed_checker_hash)
    after = load_contracts(ROOT)
    assert before.hashes["pipeline"] != after.hashes["pipeline"]


def test_stage5_input_rejects_non_incremental_scope_and_duplicates():
    contracts = load_contracts(ROOT)
    row = _row()
    row["metadata"]["selection_reasons"] = ["ALL_PRODUCTS"]
    with pytest.raises(ContractError, match="SELECTION_REASON_NOT_ALLOWED"):
        validate_input_rows([row], contracts)
    duplicate = _row()
    with pytest.raises(ContractError, match="DUPLICATE_INPUT"):
        validate_input_rows([_row(), duplicate], contracts)


def test_rule_first_plan_never_sends_categories_to_model():
    contracts = load_contracts(ROOT)
    source = _source()
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    requests = model_requests(plans)
    assert [request["field"] for request in requests] == ["description", "details"]
    assert {plan.field for plan in plans if plan.resolution_path == "RULE"} == {"name", "cat1", "cat2", "spec"}


def test_rule_covered_detail_cell_bypasses_model_and_preserves_pair_order():
    contracts = load_contracts(ROOT)
    source = _source(details=(
        "Número de turnos de limpieza: 42; A color: No; "
        "Material: Polipropileno (PP); Tipo de plato: No desechable"
    ))
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    detail_plan = next(plan for plan in plans if plan.field == "details")

    assert detail_plan.resolution_path == "RULE"
    assert detail_plan.reason == "DETAIL_TERMINOLOGY_RULE"
    assert detail_plan.resolver is not None
    assert detail_plan.resolver.source == "detail_terminology"
    assert detail_plan.resolver.value == "洗涤次数: 42次; 是否彩色: 否; 材质: 聚丙烯（PP）; 餐具类型: 非一次性"
    assert all(request["field"] != "details" for request in model_requests(plans))

    candidates, failures, _, _ = build_batch(
        [detail_plan], {}, contracts, _identity(), dictionary_hash="dictionary-v1",
    )
    detail_candidate = candidates[0]
    assert detail_candidate["model_invoked"] is False
    assert detail_candidate["status"] == "RULE_RESOLVED"
    assert detail_candidate["final_candidate"] == detail_plan.resolver.value
    assert failures == []


def test_unmapped_closed_enum_details_route_to_review_without_model_request():
    contracts = load_contracts(ROOT)
    source = _source(details="Tipo de batería: Alcalina")
    rows = validate_input_rows([_row(source)], contracts)

    plans = plan_batch(rows, _context(source), contracts)
    detail_plan = next(plan for plan in plans if plan.field == "details")

    assert detail_plan.resolution_path == "RESOLVER"
    assert detail_plan.reason == "DETAIL_ENUM_UNMAPPED"
    assert detail_plan.resolver is not None
    assert detail_plan.resolver.status == "REVIEW"
    assert "Alcalina" in detail_plan.resolver.value
    assert all(request["field"] != "details" for request in model_requests(plans))

    candidates, failures, reviews, _ = build_batch(
        plans, {}, contracts, _identity(), dictionary_hash="dictionary",
    )
    detail_candidate = next(item for item in candidates if item["source_field"] == "details")
    detail_review = next(item for item in reviews if item["field"] == "details")
    detail_failure = next(item for item in failures if item["field"] == "details")
    assert detail_candidate["model_invoked"] is False
    assert detail_candidate["final_candidate"] is None
    assert detail_review["review_status"] == "PENDING"
    assert "DETAIL_ENUM_UNMAPPED" in detail_review["guard_findings"]
    assert detail_failure["action"] == "HUMAN_REVIEW_REQUIRED"

    assert detail_failure["failure_type"] == "SEMANTIC_REVIEW_REQUIRED"
    assert detail_failure["severity"] == "P2"

    unknown_route = replace(detail_plan, reason="UNCLASSIFIED_INTERNAL_ROUTE")
    _, unknown_failures, _, _ = build_batch(
        [unknown_route], {}, contracts, _identity(), dictionary_hash="dictionary",
    )
    assert unknown_failures[0]["failure_type"] == "SEMANTIC_REVIEW_REQUIRED"
    assert unknown_failures[0]["failure_type"] != "UNCLASSIFIED_INTERNAL_ROUTE"


def test_source_anomaly_is_review_evidence_not_translation_failure_or_model_request():
    contracts = load_contracts(ROOT)
    source = _source(details="Sustancia: Válido; Número del artículo: 1001")
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    detail_plan = next(plan for plan in plans if plan.field == "details")

    assert detail_plan.resolution_path == "SOURCE_ANOMALY"
    assert detail_plan.reason == "SOURCE_ANOMALY_REVIEW"
    assert detail_plan.source_anomaly_evidence[0]["code"] == "SOURCE_ANOMALY_SUSTANCIA_VALIDO"
    assert not any(request["field"] == "details" for request in model_requests(plans))

    candidates, failures, reviews, _ = build_batch(
        plans, {}, contracts, _identity(), dictionary_hash="dictionary-v1",
    )
    detail = next(item for item in candidates if item["source_field"] == "details")
    assert detail["status"] == "SOURCE_ANOMALY_REVIEW"
    assert detail["source_anomaly_evidence"]
    assert any(
        item["field"] == "details" and item["proposed_disposition"] == "SOURCE_ANOMALY_REVIEW"
        for item in reviews
    )
    assert not any(item["field"] == "details" for item in failures)


def test_compressed_model_description_is_held_for_manual_review():
    contracts = load_contracts(ROOT)
    source = _source(description=("Este producto ofrece comodidad y facilidad de uso para el hogar. " * 5))
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "耐用，适合日常使用。" if plan.field == "description" else "防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }

    candidates, failures, reviews, _ = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
    )

    description = next(item for item in candidates if item["source_field"] == "description")
    failure = next(item for item in failures if item["field"] == "description")
    review = next(item for item in reviews if item["field"] == "description")
    assert description["final_candidate"] is None
    assert description["status"] == "GUARD_REJECT"
    assert "DESCRIPTION_COMPRESSION_REVIEW" in description["guard_result"]["reasons"]
    assert any(
        finding["code"] == "DESCRIPTION_COMPRESSION_REVIEW"
        for finding in description["semantic_fact_findings"]
    )
    assert failure["failure_type"] == "SEMANTIC_REVIEW_REQUIRED"
    assert failure["severity"] == "P2"
    assert review["review_status"] == "PENDING"


def test_guard_passed_model_description_still_requires_owner_review():
    """A machine Guard pass is not semantic approval for free-text descriptions."""
    contracts = load_contracts(ROOT)
    source = _source(description="Madera natural para uso diario.")
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "天然木材，适合日常使用。" if plan.field == "description"
            else "防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }

    candidates, failures, reviews, _ = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
    )

    description = next(item for item in candidates if item["source_field"] == "description")
    review = next(item for item in reviews if item["field"] == "description")
    assert description["guard_result"]["accepted"] is True
    assert description["status"] == "GUARD_PASS_PENDING_REVIEW"
    assert description["review_status"] == "PENDING"
    assert description["final_candidate"] == "天然木材，适合日常使用。"
    assert review["review_status"] == "PENDING"
    assert review["guard_findings"] == ""
    assert not any(item["field"] == "description" for item in failures)


def test_model_requests_carry_numeric_and_approved_fact_ledgers():
    contracts = load_contracts(ROOT)
    source = _source(description="Incluye 2 piezas de 10,5 cm en madera FSC®", details="Protección IP44; potencia 18,5 vatios")
    rows = validate_input_rows([_row(source)], contracts)
    terms = ({
        "term_es": "piezas", "term_zh": "件", "term_type": "quantity",
        "review_status": "APPROVED",
    }, {
        "term_es": "piezas", "term_zh": "组件", "term_type": "quantity",
        "review_status": "PENDING",
    })
    requests = model_requests(plan_batch(rows, _context(source), contracts), approved_terms=terms)
    by_field = {request["field"]: request for request in requests}
    assert set(by_field) == {"description", "details"}
    assert by_field["description"]["numeric_ledger"] == ["10.5", "2"]
    assert by_field["description"]["unit_ledger"] == ["cm"]
    assert by_field["description"]["certification_ledger"] == ["FSC"]
    assert by_field["description"]["approved_fact_ledger"] == [{
        "source_term": "piezas", "approved_translation": "件", "term_type": "quantity",
        "translation_candidates": ["件"], "ambiguous_mapping": False,
    }]
    assert by_field["details"]["numeric_ledger"] == ["18.5", "44"]
    assert by_field["details"]["unit_ledger"] == ["w"]
    assert by_field["details"]["technical_token_ledger"] == ["IP44"]
    assert all(request["rule_first"] is True for request in requests)


def test_model_request_identity_changes_when_selected_dictionary_changes():
    contracts = load_contracts(ROOT)
    source = _source(description="Incluye madera FSC")
    rows = validate_input_rows([_row(source)], contracts)
    context = _context(source)

    original = plan_batch(rows, context, contracts)
    changed_dictionary = plan_batch(rows, replace(context, content_hash="different-dictionary-hash"), contracts)
    original_id = next(plan.request_id for plan in original if plan.field == "description")
    changed_id = next(plan.request_id for plan in changed_dictionary if plan.field == "description")

    assert original_id != changed_id


def test_approved_fact_ledger_does_not_hide_conflicting_approved_translations():
    from action_tracker.translation.approved_terms import approved_source_term_ledger

    ledger = approved_source_term_ledger("Incluye madera", (
        {"term_es": "madera", "term_zh": "木质", "term_type": "material", "review_status": "APPROVED"},
        {"term_es": "madera", "term_zh": "木材", "term_type": "material", "review_status": "HUMAN_REVIEWED"},
    ))

    assert ledger == [{
        "source_term": "madera", "term_type": "material", "approved_translation": "",
        "translation_candidates": ["木材", "木质"], "ambiguous_mapping": True,
    }]


def test_model_input_payload_passes_all_protected_fact_ledgers():
    payload = model_input_payload({
        "field": "description", "source": "Incluye 2 piezas de madera FSC",
        "numeric_ledger": ["2"], "unit_ledger": [], "technical_token_ledger": [],
        "certification_ledger": ["FSC"],
        "approved_fact_ledger": [{
            "source_term": "madera", "term_type": "material", "approved_translation": "木质",
            "translation_candidates": ["木质"], "ambiguous_mapping": False,
        }],
    })

    assert payload["description"] == "Incluye 2 piezas de madera FSC"
    assert payload["__protected_fact_ledger__"] == {
        "numbers": ["2"], "units": [], "technical_tokens": [],
        "certifications": ["FSC"], "approved_terms": [{
            "source_term": "madera", "term_type": "material", "approved_translation": "木质",
            "translation_candidates": ["木质"], "ambiguous_mapping": False,
        }],
    }


def test_guard_rejects_unit_or_technical_corruption_before_candidate_acceptance():
    contracts = load_contracts(ROOT)
    source = _source()
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    requests = model_requests(plans)
    raw = {}
    for request in requests:
        if request["field"] == "description":
            raw[request["request_id"]] = json.dumps({"description": "包含2件，尺寸10.5克"}, ensure_ascii=False)
        else:
            raw[request["request_id"]] = json.dumps({"details": "防护等级IP45；功率18.5瓦"}, ensure_ascii=False)
    candidates, failures, reviews, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary",
    )
    rejected = {item["source_field"]: item for item in candidates if item["status"] == "GUARD_REJECT"}
    assert "UNIT_DROPPED" in rejected["description"]["guard_result"]["reasons"]
    assert "TECH_TOKEN_DROPPED" in rejected["details"]["guard_result"]["reasons"]
    assert rejected["description"]["final_candidate"] is None
    assert evaluation["guard_reject"] == 2
    assert evaluation["unit_fact_corruption_escaped"] == 0
    assert evaluation["tech_token_corruption_escaped"] == 0
    assert len(failures) == 2
    assert len(reviews) == 2


def test_stage5_rejects_certification_and_negation_loss_or_invention():
    contracts = load_contracts(ROOT)
    cases = (
        (
            "Papel FSC® certificado, sin BPA",
            "纸张",
            {"CERTIFICATION_DROPPED", "NEGATION_DROPPED"},
        ),
        (
            "Papel para manualidades",
            "纸张，FSC认证，不含BPA",
            {"CERTIFICATION_HALLUCINATED", "NEGATION_HALLUCINATED"},
        ),
        (
            "Fórmula vegana",
            "纯素配方，无硫酸盐基底",
            {"UNSUPPORTED_NEGATIVE_ATTRIBUTE"},
        ),
    )

    for source_description, target_description, expected in cases:
        source = _source(description=source_description)
        plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
        raw = {
            plan.request_id: json.dumps({plan.field: (
                target_description if plan.field == "description" else "防护等级IP44；功率18.5瓦"
            )}, ensure_ascii=False)
            for plan in plans if plan.resolution_path == "MODEL"
        }
        candidates, _, reviews, _ = build_batch(
            plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
        )

        description = next(item for item in candidates if item["source_field"] == "description")
        assert description["status"] == "GUARD_REJECT"
        assert expected.issubset(set(description["guard_result"]["reasons"]))
        assert any(item["field"] == "description" for item in reviews)


def test_stage5_blocks_approved_forbidden_term_in_same_source_field():
    contracts = load_contracts(ROOT)
    source = _source(description="Incluye 2 piezas de madera")
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "包含2件木材" if plan.field == "description" else "防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }
    candidates, failures, reviews, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "木材", "review_status": "APPROVED",
        },),
    )
    description = next(item for item in candidates if item["source_field"] == "description")
    assert description["status"] == "GUARD_REJECT"
    assert "TERM_FORBIDDEN_TRANSLATION" in description["guard_result"]["reasons"]
    assert description["final_candidate"] is None
    assert any(item["field"] == "description" for item in failures)
    assert any(item["field"] == "description" for item in reviews)
    assert evaluation["term_forbidden_reject"] == 1
    assert evaluation["forbidden_term_escaped"] == 0


def test_stage5_routes_unapproved_canonical_synonym_to_human_review():
    contracts = load_contracts(ROOT)
    source = _source(description="Incluye 2 piezas de madera")
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "包含2件木头" if plan.field == "description" else "防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }
    candidates, _, reviews, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )
    description = next(item for item in candidates if item["source_field"] == "description")
    assert description["status"] == "GUARD_REJECT"
    assert "APPROVED_TERM_CANONICAL_ABSENT_REVIEW" in description["guard_result"]["reasons"]
    finding = next(item for item in description["semantic_fact_findings"] if item["code"] == "APPROVED_TERM_CANONICAL_ABSENT_REVIEW")
    assert finding["canonical_absence_is_not_proof_of_error"] is True
    assert any(item["field"] == "description" for item in reviews)
    assert evaluation["approved_term_review"] == 1


def test_stage5_routes_glossary_backed_cross_field_movement_to_review():
    contracts = load_contracts(ROOT)
    source = _source(details="Material: madera; Protección IP44; potencia 18,5 vatios")
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "包含2件木质产品，尺寸10.5厘米" if plan.field == "description"
            else "材质：木质；防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }

    candidates, failures, reviews, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )

    description = next(item for item in candidates if item["source_field"] == "description")
    assert description["status"] == "GUARD_REJECT"
    assert description["final_candidate"] is None
    assert "APPROVED_TERM_CROSS_FIELD_REVIEW" in description["guard_result"]["reasons"]
    finding = next(
        item for item in description["semantic_fact_findings"]
        if item["code"] == "APPROVED_TERM_CROSS_FIELD_REVIEW"
    )
    assert finding["source_field"] == "details"
    assert finding["field"] == "description"
    review = next(item for item in reviews if item["field"] == "description")
    assert review["review_id"] == hashlib.sha256(
        f"stage5-review-v1|{description['candidate_id']}".encode("utf-8")
    ).hexdigest()
    assert any(item["field"] == "description" for item in failures)
    assert evaluation["approved_term_cross_field_review"] == 1


def test_stage5_routes_glossary_term_without_any_source_field_support_to_review():
    contracts = load_contracts(ROOT)
    source = _source()
    plans = plan_batch(validate_input_rows([_row(source)], contracts), _context(source), contracts)
    raw = {
        plan.request_id: json.dumps({plan.field: (
            "包含2件木质产品，尺寸10.5厘米" if plan.field == "description"
            else "防护等级IP44；功率18.5瓦"
        )}, ensure_ascii=False)
        for plan in plans if plan.resolution_path == "MODEL"
    }

    candidates, failures, reviews, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary-v1",
        approved_terms=({
            "term_es": "madera", "term_zh": "木质", "term_type": "material",
            "forbidden_zh": "", "review_status": "APPROVED",
        },),
    )

    description = next(item for item in candidates if item["source_field"] == "description")
    assert description["status"] == "GUARD_REJECT"
    assert description["final_candidate"] is None
    assert "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW" in description["guard_result"]["reasons"]
    finding = next(
        item for item in description["semantic_fact_findings"]
        if item["code"] == "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW"
    )
    assert finding["source_term_absent_from_all_localized_fields"] is True
    assert finding["unsupported_target_is_not_proof_of_hallucination"] is True
    assert any(item["field"] == "description" for item in failures)
    assert next(item for item in failures if item["field"] == "description")["failure_type"] == "SEMANTIC_REVIEW_REQUIRED"
    assert any(item["field"] == "description" for item in reviews)
    assert evaluation["approved_term_unsupported_target_review"] == 1
    assert "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW" not in contracts.guard["hard_reasons"]


def test_strict_parser_routes_code_fence_or_wrong_schema_to_model_failure():
    contracts = load_contracts(ROOT)
    source = _source()
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    requests = model_requests(plans)
    raw = {
        request["request_id"]: (
            '```json\n{"description":"包含2件，尺寸10.5厘米"}\n```'
            if request["field"] == "description" else '{"wrong":"防护等级IP44；功率18.5瓦"}'
        )
        for request in requests
    }
    candidates, _, _, evaluation = build_batch(
        plans, raw, contracts, _identity(), dictionary_hash="dictionary",
    )
    model_rows = [item for item in candidates if item["model_invoked"]]
    assert all(item["status"] == "MODEL_FAILURE" for item in model_rows)
    assert evaluation["schema_reject"] == 2


def test_source_omission_never_invokes_model():
    contracts = load_contracts(ROOT)
    source = _source(description="")
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    description = next(plan for plan in plans if plan.field == "description")
    assert description.resolution_path == "SOURCE"
    assert description.reason == "SOURCE_AMBIGUOUS"
    assert description.request_id not in {item["request_id"] for item in model_requests(plans)}


def test_candidate_ids_and_artifacts_are_idempotent_and_immutable(tmp_path):
    contracts = load_contracts(ROOT)
    source = _source()
    input_path = tmp_path / "input.jsonl"
    input_path.write_text(json.dumps(_row(source), ensure_ascii=False) + "\n", encoding="utf-8")
    rows = validate_input_rows([_row(source)], contracts)
    plans = plan_batch(rows, _context(source), contracts)
    requests = model_requests(plans)
    raw = {
        request["request_id"]: json.dumps(
            {request["field"]: "包含2件，尺寸10.5厘米" if request["field"] == "description" else "防护等级IP44；功率18.5瓦"},
            ensure_ascii=False,
        ) for request in requests
    }
    first = build_batch(plans, raw, contracts, _identity(), dictionary_hash="dictionary")
    second = build_batch(plans, raw, contracts, _identity(), dictionary_hash="dictionary")
    assert [item["candidate_id"] for item in first[0]] == [item["candidate_id"] for item in second[0]]
    output = tmp_path / "batch"
    manifest1 = write_batch_artifacts(
        output, input_path=input_path, contracts=contracts, identity=_identity(),
        environment=environment_manifest(ROOT), dictionary_hash="dictionary",
        requests=requests, raw_outputs=raw, candidates=first[0], failures=first[1],
        reviews=first[2], evaluation=first[3],
    )
    manifest2 = write_batch_artifacts(
        output, input_path=input_path, contracts=contracts, identity=_identity(),
        environment=environment_manifest(ROOT), dictionary_hash="dictionary",
        requests=requests, raw_outputs=raw, candidates=second[0], failures=second[1],
        reviews=second[2], evaluation=second[3],
    )
    assert manifest1["manifest_sha256"] == manifest2["manifest_sha256"]
    assert all(value is False for value in manifest1["production_writes"].values())
    changed = list(second[0])
    changed[0] = {**changed[0], "final_candidate": "冲突"}
    with pytest.raises(ImmutableArtifactError, match="IMMUTABLE_ARTIFACT_CONFLICT"):
        write_batch_artifacts(
            output, input_path=input_path, contracts=contracts, identity=_identity(),
            environment=environment_manifest(ROOT), dictionary_hash="dictionary",
            requests=requests, raw_outputs=raw, candidates=changed, failures=second[1],
            reviews=second[2], evaluation=second[3],
        )


def test_frozen_identity_refuses_adapter_or_tokenizer_drift(tmp_path):
    model = tmp_path / "model"
    adapter = tmp_path / "adapter"
    state_path = tmp_path / "runtime/training/qwen3_8b/20260911"
    model.mkdir(parents=True)
    adapter.mkdir(parents=True)
    state_path.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer.json").write_text("token", encoding="utf-8")
    (adapter / "adapter.bin").write_bytes(b"adapter")
    contract_path = tmp_path / "stage4.json"
    contract_path.write_text("{}", encoding="utf-8")
    (state_path / "stage4_recovery_state.json").write_text(json.dumps({
        "state": "READY_FOR_STAGE5_OFFLINE_SHADOW",
        "gate_results": {"FULL_STAGE4_RELEASE": False},
        "production_writes_authorized": False,
    }), encoding="utf-8")
    pipeline = {
        "stage4_reference": {
            "recovery_state_required_for_shadow": "READY_FOR_STAGE5_OFFLINE_SHADOW",
            "base_model_path": "model", "base_model_config_sha256": sha256_file(model / "config.json"),
            "tokenizer_sha256": tokenizer_sha256(model), "adapter_path": "adapter",
            "adapter_tree_sha256": sha256_tree(adapter), "stage4_inference_contract_path": "stage4.json",
            "stage4_inference_contract_sha256": sha256_file(contract_path),
        }
    }
    contracts = Stage5Contracts({}, {}, pipeline, {}, {})
    validate_frozen_identity(tmp_path, contracts)
    (adapter / "adapter.bin").write_bytes(b"drift")
    with pytest.raises(ContractError, match="FROZEN_IDENTITY_MISMATCH"):
        validate_frozen_identity(tmp_path, contracts)


def test_frozen_identity_accepts_released_source_bound_resolver_state(tmp_path):
    model = tmp_path / "model"
    adapter = tmp_path / "adapter"
    state_path = tmp_path / "runtime/training/qwen3_8b/20260911"
    model.mkdir(parents=True)
    adapter.mkdir(parents=True)
    state_path.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer.json").write_text("token", encoding="utf-8")
    (adapter / "adapter.bin").write_bytes(b"adapter")
    contract_path = tmp_path / "stage4.json"
    contract_path.write_text("{}", encoding="utf-8")
    (state_path / "stage4_recovery_state.json").write_text(json.dumps({
        "state": "STAGE4_RELEASED_SOURCE_BOUND_RESOLVER",
        "gate_results": {"FULL_STAGE4_RELEASE": True},
        "production_writes_authorized": False,
    }), encoding="utf-8")
    pipeline = {
        "stage4_reference": {
            "recovery_state_required_for_shadow": "READY_FOR_STAGE5_OFFLINE_SHADOW",
            "base_model_path": "model", "base_model_config_sha256": sha256_file(model / "config.json"),
            "tokenizer_sha256": tokenizer_sha256(model), "adapter_path": "adapter",
            "adapter_tree_sha256": sha256_tree(adapter), "stage4_inference_contract_path": "stage4.json",
            "stage4_inference_contract_sha256": sha256_file(contract_path),
        }
    }
    contracts = Stage5Contracts({}, {}, pipeline, {}, {})
    observed = validate_frozen_identity(tmp_path, contracts)
    assert observed["stage4_recovery_state"] == "STAGE4_RELEASED_SOURCE_BOUND_RESOLVER"
    assert observed["stage4_full_release"] is True


def test_batch_builder_strips_assistant_and_marks_review_scope():
    path = ROOT / "scripts/build_stage5_batch.py"
    spec = importlib.util.spec_from_file_location("build_stage5_batch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    raw = _row(assistant=True)
    raw["metadata"] = {
        "sku": "1001", "source_hash": _hash(_source()),
        "candidate_status": "REVIEW_REQUIRED", "collection": "independent-day",
    }
    built = module.build_rows(
        [raw], batch_id="batch-01", run_id="run-01", observation_date="2026-09-10",
    )
    assert [message["role"] for message in built[0]["messages"]] == ["user"]
    assert built[0]["metadata"]["selection_reasons"] == ["NEEDS_REVIEW"]
    assert built[0]["metadata"]["canonical_id"] == "1001"
    assert module.recorded_candidate_hash({
        "artifacts": {"candidate_jsonl": "source.jsonl", "candidate_jsonl_sha256": "abc"}
    }) == "abc"
    assert module.recorded_candidate_hash({
        "artifacts": {"candidate_jsonl": {"path": "source.jsonl", "sha256": "def"}}
    }) == "def"


def test_owner_correction_manifest_is_field_level_and_immutable(tmp_path):
    path = tmp_path / "owner_corrections.json"
    path.write_text(json.dumps({
        "review_status": "OWNER_REVIEW_COMPLETE",
        "corrections": [
            {"sku": "1001", "field": "name", "value": "标准名称"},
            {"sku": "1001", "field": "description", "replacements": [
                {"from": "防水", "to": "防泼水"},
            ]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    manifest, indexed = load_owner_correction_manifest(path)
    assert manifest["review_status"] == "OWNER_REVIEW_COMPLETE"
    assert set(indexed) == {("1001", "name"), ("1001", "description")}
    corrected, record = apply_owner_correction("1001", "name", "模型名称", indexed)
    assert corrected == "标准名称"
    assert record["field"] == "name"
    corrected, record = apply_owner_correction("1001", "description", "防水面料", indexed)
    assert corrected == "防泼水面料"
    assert record["replacements"][0]["to"] == "防泼水"
    unchanged, record = apply_owner_correction("9999", "name", "原值", indexed)
    assert unchanged == "原值"
    assert record is None


def test_owner_correction_manifest_rejects_duplicate_or_incomplete_rows(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps({
        "review_status": "OWNER_REVIEW_COMPLETE",
        "corrections": [
            {"sku": "1001", "field": "name", "value": "A"},
            {"sku": "1001", "field": "name", "value": "B"},
        ],
    }), encoding="utf-8")
    with pytest.raises(ContractError, match="OWNER_CORRECTION_DUPLICATE"):
        load_owner_correction_manifest(duplicate)
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({
        "review_status": "OWNER_REVIEW_COMPLETE",
        "corrections": [{"sku": "1001", "field": "name"}],
    }), encoding="utf-8")
    with pytest.raises(ContractError, match="OWNER_CORRECTION_ROW_INVALID"):
        load_owner_correction_manifest(incomplete)
