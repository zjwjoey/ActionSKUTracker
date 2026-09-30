from __future__ import annotations

import importlib.util
from pathlib import Path

from action_tracker.dictionary_resolver import FieldResolution


def load_stage():
    path = Path(__file__).resolve().parents[1] / "scripts" / "qwen_offline_stage5.py"
    spec = importlib.util.spec_from_file_location("qwen_offline_stage5", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def row(source: dict[str, str], *, field: str | None = None):
    metadata = {"sku": "1001", "source_hash": "hash"}
    if field:
        metadata["field"] = field
    return {"messages": [{"role": "system", "content": "system"}, {"role": "user", "content": __import__("json").dumps(source, ensure_ascii=False)}], "metadata": metadata}


def test_stage_accepts_numeric_preserving_candidate_without_reference_target():
    stage = load_stage()
    candidate = stage.classify_candidate(row({"spec": "2 unidades"}, field="spec"), {"spec": "2件"})
    assert candidate["accepted_by_guard"] is True
    assert candidate["status"] == "GUARD_PASS_PENDING_REVIEW"
    assert "prediction" in candidate


def test_stage_routes_numeric_hallucination_to_review_without_fixing_it():
    stage = load_stage()
    candidate = stage.classify_candidate(row({"description": "Incluye 2 piezas"}, field="description"), {"description": "含 3 件"})
    assert candidate["accepted_by_guard"] is False
    assert candidate["status"] == "REVIEW_REQUIRED"
    assert "NUMERIC_HALLUCINATED" in candidate["reasons"]
    assert candidate["prediction"]["description"] == "含 3 件"


def test_stage_never_accepts_or_fabricates_an_empty_source_field():
    stage = load_stage()
    candidate = stage.classify_candidate(row({"cat2": ""}, field="cat2"), {"cat2": "糖果"})
    assert candidate["accepted_by_guard"] is False
    assert candidate["status"] == "REVIEW_REQUIRED"
    assert candidate["prediction"] is None
    assert candidate["reasons"] == ["SOURCE_EMPTY"]


def test_stage_routes_spanish_residual_and_confirmed_brand_retention_to_review():
    stage = load_stage()
    bad = stage.classify_candidate(row({"name": "Rotulador negro"}, field="name"), {"name": "黑色 negro"})
    assert bad["accepted_by_guard"] is False
    assert "SPANISH_RESIDUAL" in bad["reasons"]
    good = stage.classify_candidate(row({"name": "Bolsa Alvira"}, field="name"), {"name": "Alvira 牌收纳袋"}, allowed_brand_phrases=("Alvira",))
    assert good["accepted_by_guard"] is False
    assert "BRAND_RETAINED" in good["reasons"]


def test_stage_summary_is_explicitly_non_production():
    stage = load_stage()
    rows = [row({"cat1": "Hobby"}, field="cat1")]
    candidates, summary = stage.stage(rows, [{"cat1": "兴趣手作"}])
    assert len(candidates) == 1
    assert summary["production_writes"] is False


def test_stage_uses_rule_closed_fields_and_only_guards_model_gap():
    stage = load_stage()
    candidate = stage.classify_candidate(
        row({"cat1": "Hogar", "description": "Incluye 2 piezas"}),
        {"description": "含2件"},
        rule_fields={"cat1": FieldResolution("家居布置", "category_dictionary", "READY")},
    )
    assert candidate["accepted_by_guard"] is True
    assert candidate["model_gap_fields"] == ["description"]
    assert candidate["rule_resolved_fields"]["cat1"]["value"] == "家居布置"


def test_stage_never_sends_rule_only_candidate_to_model_or_production():
    stage = load_stage()
    candidate = stage.classify_candidate(
        row({"cat1": "Hogar"}, field="cat1"),
        {},
        rule_fields={"cat1": FieldResolution("家居布置", "category_dictionary", "READY")},
    )
    assert candidate["status"] == "RULE_RESOLVED_CANDIDATE"
    assert candidate["accepted_by_guard"] is True
    assert candidate["model_gap_fields"] == []


def test_inference_row_validation_keeps_reference_shape_for_fieldwise_runner():
    stage = load_stage()
    original = row({"description": "Incluye 2 piezas"}, field="description")
    original["messages"].append({"role": "assistant", "content": '{"description":"含2件"}'})
    prepared = stage._inference_rows([original])
    assert [message["role"] for message in prepared[0]["messages"]] == ["system", "user", "assistant"]


def test_unsafe_dictionary_value_is_reopened_as_model_gap():
    stage = load_stage()
    candidate = stage.classify_candidate(
        row({"spec": "10 unidades"}, field="spec"),
        {"spec": "10件"},
        rule_fields={"spec": FieldResolution("9件", "product_dictionary", "READY")},
    )
    assert candidate["accepted_by_guard"] is True
    assert candidate["model_gap_fields"] == ["spec"]
    assert candidate["rule_resolved_fields"] == {}
    assert set(candidate["rule_validation_failures"]["spec"]) == {"NUMERIC_DROPPED", "NUMERIC_HALLUCINATED"}
