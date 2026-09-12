from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

from action_tracker.exporting.dictionary_join import DictionaryContext
from action_tracker.services.hashing import localization_source_hash
from action_tracker.stage5.pipeline import (
    ContractError,
    ImmutableArtifactError,
    Stage5Contracts,
    build_batch,
    environment_manifest,
    load_contracts,
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
