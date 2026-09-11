import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_assistant_only_labels_mask_prompt_tokens():
    train = load_script("train_qwen_qlora.py")
    assert train.assistant_only_labels([10, 11, 12, 13], 2) == [-100, -100, 12, 13]


def test_assistant_only_labels_rejects_fully_truncated_target():
    train = load_script("train_qwen_qlora.py")
    with pytest.raises(ValueError, match="TARGET_TRUNCATED"):
        train.assistant_only_labels([10, 11], 2)


def test_short_run_can_use_exact_budget_without_changing_default_guard():
    train = load_script("train_qwen_qlora.py")
    assert train.effective_steps(60, short_run=True) == 60
    assert train.effective_steps(60, smoke=True) == 60
    assert train.effective_steps(60) == 200


def test_early_stopping_requires_best_checkpoint_mode():
    train = load_script("train_qwen_qlora.py")
    train.validate_early_stopping(short_run=True, patience=3)
    with pytest.raises(ValueError, match="EARLY_STOPPING_REQUIRES_SHORT_RUN"):
        train.validate_early_stopping(short_run=False, patience=3)


def test_evaluator_parses_markdown_json_fence():
    evaluate = load_script("evaluate_qwen_model.py")
    assert evaluate.parse_object("```json\n{\"name\": \"测试\"}\n```") == {"name": "测试"}


def test_evaluator_numeric_tokens_normalize_decimal_separator():
    evaluate = load_script("evaluate_qwen_model.py")
    assert evaluate.nums("1,5 litros | 20 piezas") == ["1.5", "20"]


def test_finalizer_source_hash_binds_to_serialized_spanish_payload():
    finalize = load_script("finalize_qwen_datasets.py")
    source = {
        "name": "Producto",
        "cat1": "Hogar",
        "cat2": "Cocina",
        "spec": "2 unidades",
        "description": "Descripción",
        "details": "Material: acero",
    }
    row = {
        "messages": [{
            "role": "user",
            "content": json.dumps(source, ensure_ascii=False, sort_keys=True),
        }],
    }
    assert finalize.source_hash_from_messages(row) == finalize.source_hash_for_source(source)


def test_finalizer_source_hash_does_not_include_unrelated_live_fields():
    finalize = load_script("finalize_qwen_datasets.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "", "spec": "", "description": "", "details": ""}
    changed = {**source, "price": "999", "product_url": "https://example.invalid/changed"}
    assert finalize.source_hash_for_source(source) == finalize.source_hash_for_source(changed)


def test_model_guard_rejects_cross_field_numeric_hallucination():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"description": "Con mango de plástico.", "details": "Cantidad: 3 piezas"}
    prediction = {"description": "带塑料手柄，包含3件。", "details": "数量：3件"}
    result = validate_model_output(source, prediction)
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_HALLUCINATED",)


def test_model_guard_rejects_dropped_percentage_without_auto_fixing():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"description": "100 % libre de plásticos"}
    prediction = {"description": "不含塑料"}
    result = validate_model_output(source, prediction)
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_DROPPED",)


def test_model_guard_allows_confirmed_brand_but_rejects_untranslated_colour():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"name": "Paño de cocina La Sonata Antracita"}
    prediction = {"name": "La Sonata Antracita 厨房抹布"}
    result = validate_model_output(source, prediction, allowed_brand_phrases=["La Sonata"])
    assert not result.accepted
    assert result.field_reasons["name"] == ("SPANISH_RESIDUAL",)


def test_model_guard_accepts_numeric_preserving_translation_and_brand():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"name": "Paño de cocina La Sonata Antracita", "spec": "50x50 cm"}
    prediction = {"name": "La Sonata 厨房抹布 炭灰色", "spec": "50×50cm"}
    result = validate_model_output(source, prediction, allowed_brand_phrases=["La Sonata"])
    assert result.accepted


def test_model_guard_rejects_english_fallback_but_allows_acronyms():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"description": "100 % libre de plásticos"}
    prediction = {"description": "100% free of plastics; PEFC"}
    result = validate_model_output(source, prediction)
    assert not result.accepted
    assert "ENGLISH_RESIDUAL" in result.reasons


def test_model_guard_allows_technical_letters_and_vitamin_notation():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Papel de formato A4"},
        {"description": "A4规格"},
        expected_fields=["description"],
    )
    assert result.accepted
    result = validate_model_output(
        {"details": "Vitaminas A, D3 y C"},
        {"details": "维生素A、D3和C"},
        expected_fields=["details"],
    )
    assert result.accepted


def test_model_guard_still_rejects_ordinary_english_article():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Producto"},
        {"description": "A product"},
        expected_fields=["description"],
    )
    assert not result.accepted
    assert "ENGLISH_RESIDUAL" in result.reasons


def test_fieldwise_retry_never_applies_an_unsafe_candidate():
    compare = load_script("compare_qwen_baselines.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "", "description": "Sin cantidad", "details": "Cantidad: 3 piezas"}
    row = {"metadata": {"sku": "1001"}, "messages": [
        {"role": "system", "content": ""},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(source, ensure_ascii=False)},
    ]}
    original = [{"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "", "description": "无数量", "details": "数量：3件"}]
    requests = [(row, "description")]
    unsafe = [{"description": "无数量，包含3件"}]
    updated, accepted = compare.apply_fieldwise_retries(row and [row], original, unsafe, requests, {"1001": ()})
    assert updated == original
    assert accepted == []


def _qwen_full_row(module, sku, *, name_es="Producto", name_zh="商品"):
    source = {
        "name": name_es, "cat1": "Hogar", "cat2": "Cocina", "spec": "10 cm",
        "description": "Para adultos", "details": "Cantidad: 2 piezas",
    }
    target = {
        "name": name_zh, "cat1": "家居布置", "cat2": "厨房", "spec": "10厘米",
        "description": "适合成人", "details": "数量：2件",
    }
    return {
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ],
        "metadata": {"sku": sku, "source_hash": module.source_hash(source)},
    }


def test_combined_training_merge_preserves_split_membership_and_provenance():
    merge = load_script("merge_qwen_gold_datasets.py")
    old = _qwen_full_row(merge, "1001")
    new = _qwen_full_row(merge, "2001", name_es="Producto nuevo", name_zh="新品")
    empty = {"train": [], "validation": [], "test": []}
    combined, report = merge.combine_rows(
        {**empty, "train": [old]},
        {**empty, "train": [new]},
    )
    assert {row["metadata"]["sku"] for row in combined["train"]} == {"1001", "2001"}
    assert report["splits"] == {"train": 2, "validation": 0, "test": 0}
    assert report["validation"] == "PASS"


def test_combined_training_merge_rejects_cross_split_source_leakage():
    merge = load_script("merge_qwen_gold_datasets.py")
    row = _qwen_full_row(merge, "1001")
    empty = {"train": [], "validation": [], "test": []}
    with pytest.raises(ValueError, match="cross_split_overlap"):
        merge.combine_rows({**empty, "train": [row]}, {**empty, "test": [row]})


def test_stage4_safety_counts_english_empty_and_invalid_category_as_hard_errors():
    compare = load_script("compare_qwen_baselines.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "", "description": "Para adultos", "details": "Cantidad: 2 piezas"}
    target = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "规格", "description": "适合成人", "details": "数量：2件"}
    row = {"metadata": {"sku": "1001"}, "messages": [
        {"role": "system", "content": ""},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
    ]}
    prediction = {**target, "cat1": "错误类目", "description": "the product", "details": ""}
    metrics = compare.aggregate([row], [prediction], "unsafe")
    assert metrics["issue_reason_counts"]["INVALID_CAT1"] == 1
    assert metrics["issue_reason_counts"]["ENGLISH_RESIDUAL"] == 1
    assert metrics["issue_reason_counts"]["EMPTY_REQUIRED_FIELD"] == 1
    assert metrics["hard_error_count"] >= 3
    assert not compare.automated_safety_pass(metrics)
