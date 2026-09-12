import importlib.util
import csv
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


def test_snapshot_refuses_incomplete_training_output(tmp_path):
    snapshot = load_script("freeze_qwen_training_snapshot.py")
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="SNAPSHOT_INCOMPLETE"):
        snapshot.freeze_snapshot(
            repo=tmp_path,
            model_path=model,
            training_output=tmp_path / "output",
            train_file=tmp_path / "train.jsonl",
            validation_file=tmp_path / "validation.jsonl",
            test_file=tmp_path / "test.jsonl",
            training_script=tmp_path / "train.py",
            evaluation_script=tmp_path / "eval.py",
            snapshot_dir=tmp_path / "snapshots",
            snapshot_id="TEST",
            evaluation_policy="test",
        )


def test_snapshot_manifest_binds_inputs_and_adapter(tmp_path):
    snapshot = load_script("freeze_qwen_training_snapshot.py")
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"model_type":"qwen3"}', encoding="utf-8")
    (model / "tokenizer.json").write_text("tokenizer", encoding="utf-8")
    output = tmp_path / "output"
    adapter = output / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text('{"r":16}', encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    (output / "smoke_metrics.json").write_text('{"completed_steps": 10}', encoding="utf-8")
    (output / "training_manifest.json").write_text('{"completed_steps": 10}', encoding="utf-8")
    train_file = tmp_path / "train.jsonl"
    validation_file = tmp_path / "validation.jsonl"
    test_file = tmp_path / "test.jsonl"
    train_file.write_text('{"sku":"1"}\n', encoding="utf-8")
    validation_file.write_text('{"sku":"2"}\n', encoding="utf-8")
    test_file.write_text('{"sku":"3"}\n', encoding="utf-8")
    training_script = tmp_path / "train.py"
    evaluation_script = tmp_path / "eval.py"
    training_script.write_text("train", encoding="utf-8")
    evaluation_script.write_text("eval", encoding="utf-8")
    manifest_path = snapshot.freeze_snapshot(
        repo=tmp_path,
        model_path=model,
        training_output=output,
        train_file=train_file,
        validation_file=validation_file,
        test_file=test_file,
        training_script=training_script,
        evaluation_script=evaluation_script,
        snapshot_dir=tmp_path / "snapshots",
        snapshot_id="TEST",
        evaluation_policy="test",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["data"]["test"]["sha256"] == snapshot.sha256_file(test_file)
    assert manifest["training"]["adapter_config"]["r"] == 16
    assert manifest["training"]["completed_steps"] == 10
    assert manifest["evaluation"]["test_rows_expected"] == 1


def test_manual_review_queue_contains_every_test_row(tmp_path):
    queue = load_script("build_qwen_manual_review_queue.py")
    test_file = tmp_path / "test.jsonl"
    benchmark_file = tmp_path / "benchmark.json"
    output_file = tmp_path / "queue.jsonl"
    row = {
        "messages": [
            {"role": "user", "content": json.dumps({"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "", "description": "", "details": ""}, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "", "description": "", "details": ""}, ensure_ascii=False)},
        ],
        "metadata": {"sku": "1001"},
    }
    test_file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    benchmark_file.write_text(json.dumps({"models": [{"name": "current_combined_adapter", "issues": []}]}), encoding="utf-8")
    summary = queue.build_queue(test_file, benchmark_file, output_file)
    assert summary["rows"] == 1
    record = json.loads(output_file.read_text(encoding="utf-8"))
    assert record["status"] == "PENDING_MANUAL_REVIEW"
    assert record["sku"] == "1001"


def test_hard_test_excludes_all_frozen_training_and_eval_skus(tmp_path):
    hard = load_script("build_qwen_hard_test.py")

    def row(sku, text):
        source = {"name": text, "cat1": "Hogar", "cat2": "Cocina", "spec": "10 cm", "description": "Sin plástico", "details": "Cantidad: 2 piezas"}
        target = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "10厘米", "description": "不含塑料", "details": "数量：2件"}
        return {"messages": [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}, {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}], "metadata": {"sku": sku}}

    candidate = tmp_path / "candidate.jsonl"
    candidate.write_text("\n".join(json.dumps(row(str(i), f"Producto {i}"), ensure_ascii=False) for i in range(1, 6)) + "\n", encoding="utf-8")
    exclusion = tmp_path / "exclude.jsonl"
    exclusion.write_text(json.dumps(row("1", "Frozen"), ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "hard.jsonl"
    manifest = tmp_path / "hard.json"
    result = hard.build_hard_test(candidate_file=candidate, exclusion_files=[exclusion], output_file=output, manifest_file=manifest, limit=3)
    selected = [json.loads(line)["metadata"]["sku"] for line in output.read_text(encoding="utf-8").splitlines()]
    assert result["rows"] == 3
    assert "1" not in selected
    assert all(json.loads(line)["metadata"]["hard_test_status"] == "PENDING_MANUAL_REVIEW" for line in output.read_text(encoding="utf-8").splitlines())


def test_hard_test_excludes_same_source_with_a_different_sku(tmp_path):
    hard = load_script("build_qwen_hard_test.py")
    def row(sku):
        source = {"name": "Same source", "cat1": "Hogar", "cat2": "Cocina", "spec": "10 cm", "description": "Sin plástico", "details": ""}
        target = {"name": "相同来源", "cat1": "家居布置", "cat2": "厨房", "spec": "10厘米", "description": "不含塑料", "details": ""}
        return {"messages": [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}, {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}], "metadata": {"sku": sku, "source_hash": "same"}}
    candidate = tmp_path / "candidate.jsonl"; candidate.write_text(json.dumps(row("2"), ensure_ascii=False) + "\n", encoding="utf-8")
    exclusion = tmp_path / "exclude.jsonl"; exclusion.write_text(json.dumps(row("1"), ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HARD_TEST_INSUFFICIENT_ELIGIBLE"):
        hard.build_hard_test(candidate_file=candidate, exclusion_files=[exclusion], output_file=tmp_path / "out.jsonl", manifest_file=tmp_path / "out.json", limit=1)


def test_field_conditioned_dataset_preserves_splits_and_rejects_numeric_mismatch(tmp_path):
    field = load_script("build_field_conditioned_gold_dataset.py")

    def row(sku, number):
        source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": f"{number} cm", "description": "Sin plástico", "details": ""}
        target = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": f"{number}厘米", "description": "不含塑料", "details": ""}
        return {"messages": [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}, {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}], "metadata": {"sku": sku, "source_hash": sku}}

    train = tmp_path / "train.jsonl"; validation = tmp_path / "validation.jsonl"; test = tmp_path / "test.jsonl"
    train.write_text(json.dumps(row("1", "10"), ensure_ascii=False) + "\n", encoding="utf-8")
    validation.write_text(json.dumps(row("2", "20"), ensure_ascii=False) + "\n", encoding="utf-8")
    bad = row("3", "30"); bad["messages"][1]["content"] = bad["messages"][1]["content"].replace("30厘米", "31厘米")
    test.write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = field.build_dataset(train_file=train, validation_file=validation, test_file=test, output_dir=tmp_path / "out")
    assert manifest["overlap_check"] == "PASS"
    assert manifest["outputs"]["train"]["rows"] == 5
    assert manifest["outputs"]["validation"]["rows"] == 5
    assert manifest["outputs"]["test"]["rows"] == 4
    assert manifest["rejected"]["test:numeric_dropped_spec"] == 1


def test_error_taxonomy_maps_guard_reasons_and_keeps_manual_status(tmp_path):
    taxonomy = load_script("build_qwen_error_taxonomy.py")
    benchmark = tmp_path / "benchmark.json"
    output = tmp_path / "taxonomy.json"
    benchmark.write_text(json.dumps({"models": [{"name": "current_combined_adapter", "rows": 1, "hard_error_count": 1, "issues": [{"sku": "1", "field": "spec", "reasons": ["NUMERIC_DROPPED"]}]}]}), encoding="utf-8")
    result = taxonomy.build_report(benchmark, output)
    assert result["taxonomy_counts"] == {"NUMERIC_MISSING": 1}
    assert result["records"][0]["review_status"] == "PENDING_MANUAL_REVIEW"


def test_hard_example_expansion_is_review_only_and_binds_test_evidence(tmp_path):
    expand = load_script("build_qwen_hard_example_expansion.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "6x25,5 cm", "description": "", "details": ""}
    target = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "6×25.5厘米", "description": "", "details": ""}
    test = tmp_path / "test.jsonl"
    test.write_text(json.dumps({
        "messages": [
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
        ],
        "metadata": {"sku": "2529028", "field": "spec", "source_hash": "abc"},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(json.dumps({"models": [{
        "name": "field_conditioned_qlora",
        "issues": [{"sku": "2529028", "field": "spec", "reasons": ["NUMERIC_DROPPED", "NUMERIC_HALLUCINATED"], "prediction": "6×25、5cm", "missing_numbers": ["25.5"], "extra_numbers": ["25", "5"]}],
    }]}), encoding="utf-8")
    output = tmp_path / "hard_examples.jsonl"
    manifest = tmp_path / "hard_examples.manifest.json"
    result = expand.build_queue(benchmark_file=benchmark, test_file=test, output_file=output, manifest_file=manifest)
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert result["rows"] == 1
    assert record["review_id"] == "QWEN3_ACTION_FIELD_CONDITIONED_V1:2529028:spec"
    assert record["source_es"] == "6x25,5 cm"
    assert record["reference_zh"] == "6×25.5厘米"
    assert record["taxonomy"] == ["NUMERIC_ADDED", "NUMERIC_MISSING"]
    assert record["status"] == "PENDING_MANUAL_REVIEW"
    assert record["training_promotion"] == "FORBIDDEN_UNTIL_HUMAN_CONFIRMED"
    saved_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved_manifest["master_write"] == "FORBIDDEN"


def test_prediction_review_rows_keep_model_output_and_source_evidence():
    dump = load_script("dump_qwen_predictions_for_review.py")
    row = {
        "messages": [
            {"role": "user", "content": json.dumps({"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "", "description": "", "details": ""}, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "", "description": "", "details": ""}, ensure_ascii=False)},
        ],
        "metadata": {"sku": "1001"},
    }
    records = dump.build_review_rows([row], [{"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "", "description": "", "details": ""}])
    assert records[0]["status"] == "PENDING_MANUAL_REVIEW"
    assert records[0]["model_prediction"]["name"] == "商品"
    assert records[0]["source_es"]["name"] == "Producto"


def test_review_csv_preserves_three_values_per_field_and_blank_decision_columns(tmp_path):
    export = load_script("export_qwen_review_csv.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "10 cm", "description": "", "details": ""}
    reference = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "10厘米", "description": "", "details": ""}
    row = {"review_id": "R:1", "sku": "1", "status": "PENDING_MANUAL_REVIEW", "source_es": source, "model_prediction": reference, "reference_zh": reference, "automated_issues": []}
    input_file = tmp_path / "review.jsonl"; input_file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    output_file = tmp_path / "review.csv"
    summary = export.export_csv(input_file, output_file)
    with output_file.open(encoding="utf-8-sig", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert summary["rows"] == 1
    assert record["name_es"] == "Producto"
    assert record["name_model_zh"] == "商品"
    assert record["human_decision"] == ""


def test_review_csv_renders_field_conditioned_hard_example_scalars(tmp_path):
    export = load_script("export_qwen_review_csv.py")
    row = {
        "review_id": "Q:2529028:spec", "sku": "2529028", "field": "spec",
        "status": "PENDING_MANUAL_REVIEW", "source_es": "6x25,5 cm",
        "model_prediction": "6×25、5cm", "reference_zh": "6×25.5厘米",
        "guard_reasons": ["NUMERIC_DROPPED"], "taxonomy": ["NUMERIC_MISSING"],
        "training_promotion": "FORBIDDEN_UNTIL_HUMAN_CONFIRMED",
    }
    input_file = tmp_path / "hard.jsonl"
    input_file.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    output_file = tmp_path / "hard.csv"
    export.export_csv(input_file, output_file)
    with output_file.open(encoding="utf-8-sig", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["spec_es"] == "6x25,5 cm"
    assert record["spec_model_zh"] == "6×25、5cm"
    assert record["spec_reference_zh"] == "6×25.5厘米"
    assert record["guard_reasons"] == '["NUMERIC_DROPPED"]'


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
    assert result.field_reasons["description"] == ("NUMERIC_DROPPED", "UNIT_DROPPED")


def test_model_guard_accepts_explicit_spanish_single_container_as_chinese_one():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un bote de espray es suficiente para pintar de 1 a 1,5 m²."},
        {"description": "一罐喷漆足以喷涂1至1.5平方米。"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_does_not_treat_a_generic_spanish_article_as_one_item_fact():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un producto para el hogar."},
        {"description": "含1件家居用品。"},
        expected_fields=["description"],
    )
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_HALLUCINATED",)


def test_model_guard_requires_the_matching_chinese_container_for_source_single_quantity():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un bote de espray para superficies calientes."},
        {"description": "1件适用于受热表面的喷漆。"},
        expected_fields=["description"],
    )
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_HALLUCINATED",)


def test_stage4_metrics_use_same_explicit_container_quantity_semantics_as_guard():
    compare = load_script("compare_qwen_baselines.py")
    source = {"name": "Producto", "cat1": "Hogar", "cat2": "Cocina", "spec": "", "description": "Un bote de espray cubre de 1 a 1,5 m².", "details": ""}
    target = {"name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "", "description": "一罐喷漆可覆盖1至1.5平方米。", "details": ""}
    row = {"metadata": {"sku": "2562727"}, "messages": [
        {"role": "system", "content": ""},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
    ]}
    metrics = compare.aggregate([row], [target], "quantity-aware")
    assert metrics["hard_error_count"] == 0
    assert metrics["numeric_preservation_rate"] == 1
    assert metrics["numeric_hallucination_rate"] == 0


def test_model_guard_allows_confirmed_brand_but_rejects_untranslated_colour():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"name": "Paño de cocina La Sonata Antracita"}
    prediction = {"name": "La Sonata Antracita 厨房抹布"}
    result = validate_model_output(source, prediction, allowed_brand_phrases=["La Sonata"])
    assert not result.accepted
    assert result.field_reasons["name"] == ("SPANISH_RESIDUAL",)


def test_model_guard_rejects_untranslated_spanish_category_plural():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"cat2": "Juegos"},
        {"cat2": "Juegos"},
        expected_fields=["cat2"],
    )
    assert not result.accepted
    assert result.field_reasons["cat2"] == ("INVALID_CATEGORY", "SPANISH_RESIDUAL")


def test_model_guard_rejects_unit_substitution_even_when_number_is_preserved():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"spec": "4 litros"},
        {"spec": "4克"},
        expected_fields=["spec"],
    )
    assert not result.accepted
    assert set(result.field_reasons["spec"]) == {"UNIT_DROPPED", "UNIT_HALLUCINATED"}


def test_model_guard_accepts_equivalent_spanish_and_chinese_units():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"spec": "100 raciones | 300 gramos | 4 litros"},
        {"spec": "100份｜300克｜4升"},
        expected_fields=["spec"],
    )
    assert result.accepted


def test_model_guard_does_not_treat_chinese_words_or_spanish_suffixes_as_units():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Chocolate; incluye materiales de montaje; con mango(s)"},
        {"description": "巧克力；含安装材料；带手柄"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_rejects_dropped_and_hallucinated_technical_tokens():
    from action_tracker.translation.model_guard import validate_model_output

    dropped = validate_model_output(
        {"description": "Cable USB-C compatible con G12"},
        {"description": "兼容G12的充电线"},
        expected_fields=["description"],
    )
    assert not dropped.accepted
    assert "TECH_TOKEN_DROPPED" in dropped.field_reasons["description"]
    hallucinated = validate_model_output(
        {"description": "Bombilla regulable"},
        {"description": "可调光LED灯泡"},
        expected_fields=["description"],
    )
    assert not hallucinated.accepted
    assert "TECH_TOKEN_HALLUCINATED" in hallucinated.field_reasons["description"]


def test_model_guard_rejects_invalid_cat1_and_empty_required_output():
    from action_tracker.translation.model_guard import validate_model_output

    category = validate_model_output(
        {"cat1": "Hogar"},
        {"cat1": "家居"},
        expected_fields=["cat1"],
    )
    assert category.field_reasons["cat1"] == ("INVALID_CATEGORY",)
    empty = validate_model_output(
        {"description": "Sin plástico"},
        {"description": ""},
        expected_fields=["description"],
    )
    assert "EMPTY_REQUIRED_FIELD" in empty.field_reasons["description"]


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


def test_field_conditioned_evaluator_rejects_rows_without_target_field_metadata():
    evaluate = load_script("compare_qwen_field_conditioned.py")
    with pytest.raises(ValueError, match="FIELD_CONDITIONED_TEST_EMPTY"):
        evaluate.validate_field_rows([])
    with pytest.raises(ValueError, match="FIELD_CONDITIONED_FIELD_MISSING"):
        evaluate.validate_field_rows([{"metadata": {"sku": "1"}}])
