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


def test_model_guard_accepts_lexical_chinese_size_and_season_numbers():
    from action_tracker.translation.model_guard import validate_model_output

    cases = (
        ({"name": "Colchón de aire de 1 plaza"}, {"name": "单人充气床垫"}, ("name",)),
        ({"description": "Edredón para 2 personas"}, {"description": "双人被子"}, ("description",)),
        ({"name": "Edredón de 4 estaciones"}, {"name": "四季被"}, ("name",)),
    )
    for source, prediction, fields in cases:
        result = validate_model_output(source, prediction, expected_fields=fields)
        assert result.accepted, result.field_reasons

    inferred_season_count = validate_model_output(
        {"description": "Adecuado para cada estación del año"},
        {"description": "适合四季使用"},
        expected_fields=("description",),
    )
    assert inferred_season_count.accepted


def test_model_guard_aligns_gsm_with_grams_per_square_meter():
    from action_tracker.translation.model_guard import unit_tokens, validate_model_output

    assert unit_tokens("350 gsm") == ["gsm"]
    assert unit_tokens("350 克/平方米") == ["gsm"]
    source_key = "Funda para móvil (gramos por m²): 140 g"
    assert unit_tokens(source_key) == ["gsm"]
    assert unit_tokens("面料克重：140克/平方米") == ["gsm"]
    result = validate_model_output(
        {"description": source_key},
        {"description": "面料克重：140克/平方米"},
        expected_fields=["description"],
    )
    assert result.accepted, result.field_reasons


def test_model_guard_accepts_counted_lipsticks_and_universal_size_wording():
    from action_tracker.translation.model_guard import validate_model_output

    lipstick_count = validate_model_output(
        {"description": "Con este set de 7 brillos labiales"},
        {"description": "这套唇彩共七支装"},
        expected_fields=("description",),
    )
    universal_size = validate_model_output(
        {"description": "1 tamaño; vale para todos"},
        {"description": "均码"},
        expected_fields=("description",),
    )
    generic_article = validate_model_output(
        {"description": "Un cepillo de dientes y una taza de té"},
        {"description": "一支牙刷和一杯茶"},
        expected_fields=("description",),
    )
    assert lipstick_count.accepted, lipstick_count.field_reasons
    assert universal_size.accepted, universal_size.field_reasons
    assert generic_article.accepted, generic_article.field_reasons


def test_model_guard_does_not_treat_a_generic_spanish_article_as_one_item_fact():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un producto para el hogar."},
        {"description": "含1件家居用品。"},
        expected_fields=["description"],
    )
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_HALLUCINATED",)


def test_model_guard_aligns_un_tipo_with_one_kind_without_weakening_generic_article():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"details": "Surtidas: Sí, con un tipo; Número del artículo: 2558074"},
        {"details": "混合装：是，含一种类型；商品编号：2558074"},
        expected_fields=["details"],
    )
    assert result.accepted


def test_model_guard_treats_spanish_ordinal_degree_symbol_as_celsius():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"details": "Planchado a temperatura máxima de 110 º C"},
        {"details": "最高110°C熨烫"},
        expected_fields=["details"],
    )
    assert result.accepted


def test_model_guard_treats_unicode_celsius_sign_as_same_unit():
    from action_tracker.translation.model_guard import unit_tokens, validate_model_output

    assert unit_tokens("Lavado hasta 60 °C") == ["celsius"]
    assert unit_tokens("最高60℃洗涤") == ["celsius"]
    result = validate_model_output(
        {"details": "Instrucciones de lavado: lavado a máquina hasta 60 °C"},
        {"details": "洗涤说明：可机洗，水温不超过60℃"},
        expected_fields=("details",),
    )
    assert result.accepted, result.field_reasons


def test_model_guard_requires_the_matching_chinese_container_for_source_single_quantity():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un bote de espray para superficies calientes."},
        {"description": "1件适用于受热表面的喷漆。"},
        expected_fields=["description"],
    )
    assert not result.accepted
    assert result.field_reasons["description"] == ("NUMERIC_HALLUCINATED",)


def test_model_guard_accepts_chinese_compound_and_quantity_numerals():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {
            "description": "Fórmula 3 en 1; Juego de 2 bóxers; 1 hoja llena",
        },
        {
            "description": "三合一配方；两条装；一张满版贴纸",
        },
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_does_not_accept_unmatched_generic_chinese_numeral():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Disponible en varios modelos."},
        {"description": "含一件商品。"},
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


def test_model_guard_rejects_confirmed_brand_retention_and_untranslated_colour():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"name": "Paño de cocina La Sonata Antracita"}
    prediction = {"name": "La Sonata Antracita 厨房抹布"}
    result = validate_model_output(source, prediction, allowed_brand_phrases=["La Sonata"])
    assert not result.accepted
    assert set(result.field_reasons["name"]) == {"BRAND_RETAINED", "SPANISH_RESIDUAL"}


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


def test_model_guard_accepts_spelled_spanish_quantities_translated_to_digits():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Conjunto de dos piezas; cable de dos metros."},
        {"description": "2件套；2米电源线。"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_allows_repeated_source_number_to_be_stated_once():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "72 páginas llenas de dibujos. El libro contiene 72 en total."},
        {"description": "含72页涂色图案。"},
        expected_fields=("description",),
    )
    assert result.accepted, result.field_reasons

    changed_number = validate_model_output(
        {"description": "72 páginas. En este libro encontrarás nada menos que 72."},
        {"description": "含72页，但实际为73页。"},
        expected_fields=("description",),
    )
    assert not changed_number.accepted
    assert "NUMERIC_HALLUCINATED" in changed_number.field_reasons["description"]


def test_model_guard_accepts_spanish_indefinite_product_nouns_and_solo_juego():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Una camiseta; un solo juego."},
        {"description": "一件T恤；一套。"},
        expected_fields=["description"],
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


def test_model_guard_collapses_repeated_technical_mentions_but_not_unique_facts():
    from action_tracker.translation.model_guard import validate_model_output

    repeated = validate_model_output(
        {"description": "Cuaderno A4; papel FSC®. El cuaderno A4 tiene papel FSC®."},
        {"description": "A4格式笔记本，纸张通过FSC®认证"},
        expected_fields=("description",),
    )
    assert repeated.accepted, repeated.field_reasons

    omitted_unique = validate_model_output(
        {"description": "Cuaderno A4 con papel FSC® y cable USB-C"},
        {"description": "A4格式笔记本，FSC®认证纸张"},
        expected_fields=("description",),
    )
    assert "TECH_TOKEN_DROPPED" in omitted_unique.reasons


def test_model_guard_compares_area_energy_and_grammage_units_as_units_not_models():
    from action_tracker.translation.model_guard import numeric_tokens, technical_tokens, unit_tokens, validate_model_output

    source = "16 mm2; 25 kWh/1000h; 550 gr/m2"
    target = "16平方毫米；25千瓦时/1000小时；550克/平方米"
    assert unit_tokens(source) == unit_tokens(target)
    assert technical_tokens(source) == []
    result = validate_model_output(
        {"description": source}, {"description": target}, expected_fields=("description",),
    )
    assert result.accepted, result.field_reasons

    # Unit suffix parsing must not cross a line break and consume the next
    # numeric quantity (e.g. ``125 mm\n2 modelos``).
    assert unit_tokens("125 mm\n2 modelos por paquete") == ["mm"]
    assert numeric_tokens("125 mm\n2 modelos por paquete") == ["125", "2"]


def test_model_guard_treats_trailing_decimal_zeroes_as_display_format_only():
    from action_tracker.translation.model_guard import numeric_fact_counters, numeric_tokens, validate_model_output

    assert numeric_tokens("4,0 x 40 mm") == ["4", "40"]
    assert numeric_tokens("4.00 × 40 mm") == ["4", "40"]
    assert numeric_tokens("4.05 × 40 mm") == ["4.05", "40"]

    expected, actual = numeric_fact_counters("4,0x40 mm", "4×40 mm")
    assert expected == actual
    result = validate_model_output(
        {"spec": "4,0x40 mm"}, {"spec": "4×40 mm"}, expected_fields=("spec",),
    )
    assert result.accepted, result.field_reasons


def test_model_guard_accepts_explicit_paired_variant_mentions():
    from action_tracker.translation.model_guard import numeric_fact_counters, validate_model_output

    source = "Juego de 2 bóxers: uno con diseño alegre y el otro liso"
    target = "两条装：一条印花款、一条纯色款"
    expected, actual = numeric_fact_counters(source, target)
    assert expected == actual
    result = validate_model_output(
        {"description": source}, {"description": target}, expected_fields=("description",),
    )
    assert result.accepted, result.field_reasons


def test_model_guard_accepts_spelled_spanish_temporal_quantity_as_digits():
    from action_tracker.translation.model_guard import numeric_fact_counters, validate_model_output

    source = "La batería dura hasta diez meses"
    target = "电池续航最长约10个月"
    expected, actual = numeric_fact_counters(source, target)
    assert expected == actual
    result = validate_model_output(
        {"description": source}, {"description": target}, expected_fields=("description",),
    )
    assert result.accepted, result.field_reasons


def test_title_guard_removes_commercial_brand_but_preserves_compatibility_platform():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"name": "Compatible con Nintendo Switch y Windows 11"},
        {"name": "兼容Switch和Windows 11"},
        expected_fields=["name"],
        allowed_brand_phrases=["Nintendo"],
    )
    assert result.accepted


def test_title_guard_rejects_dropped_or_invented_compatibility_platform():
    from action_tracker.translation.model_guard import validate_model_output

    dropped = validate_model_output(
        {"name": "Compatible con Nintendo Switch"},
        {"name": "兼容游戏机"},
        expected_fields=["name"],
    )
    assert not dropped.accepted
    assert "TECH_TOKEN_DROPPED" in dropped.field_reasons["name"]

    invented = validate_model_output(
        {"name": "Compatible con Nintendo Switch"},
        {"name": "兼容Switch和Xbox Series X"},
        expected_fields=["name"],
    )
    assert not invented.accepted
    assert "TECH_TOKEN_HALLUCINATED" in invented.field_reasons["name"]


def test_title_guard_accepts_known_platform_aliases_and_ignores_plain_switch_word():
    from action_tracker.translation.model_guard import technical_tokens, validate_model_output

    playstation_alias = validate_model_output(
        {"name": "Compatible con PlayStation 5"},
        {"name": "兼容PS5"},
        expected_fields=["name"],
    )
    android_alias = validate_model_output(
        {"name": "Compatible con Android"},
        {"name": "兼容安卓"},
        expected_fields=["name"],
    )
    assert playstation_alias.accepted
    assert android_alias.accepted
    assert "NINTENDO_SWITCH" not in technical_tokens("Turn the switch off")


def test_model_guard_accepts_standard_bluetooth_translation_and_enc_token():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Con micrófonos ENC y Bluetooth 5.4"},
        {"description": "内置ENC麦克风，支持蓝牙5.4"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_normalizes_plural_led_token_without_false_hallucination():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"spec": "Ø 45 cm | 1200 LEDs"},
        {"spec": "Ø 45 cm | 1200 个 LED"},
        expected_fields=["spec"],
    )
    assert result.accepted


def test_model_guard_aligns_explicit_animal_quantity_phrase():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Un animal distinto en cada caja"},
        {"description": "每盒一个独特的动物"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_aligns_explicit_doll_quantity_phrase():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Incluye una muñeca y 2 accesorios"},
        {"description": "包含一个玩偶和2个配件"},
        expected_fields=["description"],
    )
    assert result.accepted


def test_model_guard_aligns_explicit_device_quantity_phrase():
    from action_tracker.translation.model_guard import validate_model_output

    result = validate_model_output(
        {"description": "Conecta dos dispositivos al mismo tiempo"},
        {"description": "可同时连接两个设备"},
        expected_fields=["description"],
    )
    assert result.accepted


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
    prediction = {"name": "炭灰色厨房抹布", "spec": "50×50cm"}
    result = validate_model_output(source, prediction, allowed_brand_phrases=["La Sonata"])
    assert result.accepted


def test_model_guard_rejects_english_fallback_but_allows_acronyms():
    from action_tracker.translation.model_guard import validate_model_output

    source = {"description": "100 % libre de plásticos"}
    prediction = {"description": "100% free of plastics; PEFC"}
    result = validate_model_output(source, prediction)
    assert not result.accepted
    assert "ENGLISH_RESIDUAL" in result.reasons


def test_model_guard_recognizes_certification_marks_adjacent_to_chinese():
    from action_tracker.translation.model_guard import certification_tokens, validate_model_output

    assert certification_tokens("采用FSC®认证的纸张") == ["FSC"]
    assert certification_tokens("木材经PEFC认证") == ["PEFC"]
    assert certification_tokens("Action是良好棉花倡议（BCI）的参与方") == ["BCI"]
    assert certification_tokens("获得Fairtrade认证，Fairtrade体系") == ["FAIRTRADE"]
    assert certification_tokens("公平贸易认证") == ["FAIRTRADE"]
    assert certification_tokens("公平贸易") == []
    assert certification_tokens("FSCX认证产品") == []

    result = validate_model_output(
        {"description": "Papel con certificación FSC®"},
        {"description": "纸张经FSC®认证"},
        expected_fields=["description"],
    )
    assert result.accepted

    generic_trade = validate_model_output(
        {"description": "Comercio justo"},
        {"description": "公平贸易"},
        expected_fields=["description"],
    )
    assert generic_trade.accepted

    fairtrade_mark = validate_model_output(
        {"description": "Chocolate Fairtrade"},
        {"description": "公平贸易巧克力"},
        expected_fields=["description"],
    )
    assert fairtrade_mark.accepted


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


def test_model_guard_recognizes_common_chinese_negation_paraphrases_without_material_false_positive():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.translation.model_guard import validate_model_output

    dropped = validate_model_output(
        {"description": "Suelos lisos sin dejar marcas"},
        {"description": "光滑地面，不留痕迹"},
        expected_fields=("description",),
    )
    low_lint = validate_model_output(
        {"description": "No sueltan pelusas"},
        {"description": "不易掉絮"},
        expected_fields=("description",),
    )
    material = validate_model_output(
        {"description": "Acero inoxidable"},
        {"description": "不锈钢"},
        expected_fields=("description",),
    )
    invented = validate_model_output(
        {"description": "Muy absorbentes"},
        {"description": "吸水性强，不易掉絮"},
        expected_fields=("description",),
    )
    nonwoven = validate_model_output(
        {"description": "Mopa no tejida"},
        {"description": "无纺布拖把"},
        expected_fields=("description",),
    )
    no_drilling = validate_model_output(
        {"description": "No es necesario taladrar"},
        {"description": "无需钻孔"},
        expected_fields=("description",),
    )
    no_marks = validate_model_output(
        {"description": "Resultados sin marcas"},
        {"description": "无水痕效果"},
        expected_fields=("description",),
    )
    idiom = validate_model_output(
        {"description": "Fácil de aplicar sin esfuerzo y sin complicaciones"},
        {"description": "使用方便，轻松无忧"},
        expected_fields=("description",),
    )
    rhetorical = validate_model_output(
        {"description": "¿No puedes parar de disfrutar de estos caramelos?"},
        {"description": "让人一颗接一颗"},
        expected_fields=("description",),
    )
    nonerasable = validate_model_output(
        {"description": "Capacidad de borrado de tinta: Inborrable"},
        {"description": "墨水可擦性：不可擦除"},
        expected_fields=("description",),
    )
    unnecessary = validate_model_output(
        {"description": "Hace innecesaria la perforación"},
        {"description": "无需钻孔"},
        expected_fields=("description",),
    )
    no_longer = validate_model_output(
        {"description": "Se acabó el frío en las manos"},
        {"description": "双手不再寒冷"},
        expected_fields=("description",),
    )
    prevention = validate_model_output(
        {"description": "Evita la condensación"},
        {"description": "防止冷凝"},
        expected_fields=("description",),
    )
    positive_idiom = validate_model_output(
        {"description": "Un producto imprescindible"},
        {"description": "一款必不可少的产品"},
        expected_fields=("description",),
    )
    protective = validate_model_output(
        {"description": "Protege contra la suciedad"},
        {"description": "防止污垢"},
        expected_fields=("description",),
    )
    nonstick = validate_model_output(
        {"description": "Con revestimiento antiadherente"},
        {"description": "带不粘涂层"},
        expected_fields=("description",),
    )

    assert "NEGATION_DROPPED" not in dropped.reasons
    assert "NEGATION_DROPPED" not in low_lint.reasons
    assert material.accepted
    assert "NEGATION_HALLUCINATED" in invented.reasons
    assert nonwoven.accepted
    assert no_drilling.accepted
    assert no_marks.accepted
    assert "NEGATION_DROPPED" not in idiom.reasons
    assert "NEGATION_DROPPED" not in rhetorical.reasons
    assert nonerasable.accepted
    assert unnecessary.accepted
    assert no_longer.accepted
    assert prevention.accepted
    assert positive_idiom.accepted
    assert protective.accepted
    assert nonstick.accepted


def test_model_guard_accepts_source_bound_slip_stick_and_seam_paraphrases():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from action_tracker.translation.model_guard import validate_model_output

    cases = (
        ({"description": "La ropa no resbala de la percha"}, {"description": "衣物不易滑落"}),
        ({"description": "No se pega"}, {"description": "不粘胶"}),
        ({"description": "Sin costuras en la entrepierna"}, {"description": "裆部无接缝"}),
        ({"description": "Papel sin madera"}, {"description": "无木浆纸张"}),
        ({"description": "Sin dejar marcas"}, {"description": "不易留下痕迹"}),
        ({"description": "No pueden faltar en tu caja de herramientas"}, {"description": "工具箱常备"}),
        ({"description": "No amarillea"}, {"description": "不易黄变"}),
        ({"description": "La plastilina no se seca"}, {"description": "不易干裂"}),
        ({"description": "Sin granos"}, {"description": "无谷物"}),
        ({"description": "Sin conservantes añadidos"}, {"description": "不添加防腐剂"}),
        ({"description": "No te costará nada diferenciarlos"}, {"description": "便于区分"}),
        ({"description": "Colores brillantes y opacos"}, {"description": "色彩鲜艳且不透明"}),
    )

    for source, target in cases:
        result = validate_model_output(source, target, expected_fields=("description",))
        assert "NEGATION_DROPPED" not in result.reasons, (source, target, result.reasons)

    invented = validate_model_output(
        {"description": "Transparente"},
        {"description": "不透明"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in invented.reasons


def test_model_guard_handles_common_negation_aliases_and_nonnegative_idioms():
    from action_tracker.translation.model_guard import validate_model_output

    faithful = (
        ({"description": "No se decolora"}, {"description": "不易变色"}),
        ({"description": "No amarillea"}, {"description": "不易变黄"}),
        ({"description": "Guantes sin costuras"}, {"description": "无缝手套"}),
        ({"description": "Sin colorantes añadidos"}, {"description": "不额外添加色素"}),
        ({"description": "No deja restos de cola"}, {"description": "撕除后不易留胶"}),
        ({"description": "No se arrugan"}, {"description": "不起皱"}),
        ({"description": "No pueden llegar los palillos ordinarios"}, {"description": "普通牙签难以触及"}),
        ({"description": "Sin perfume"}, {"description": "无香"}),
        ({"description": "Vela inodora"}, {"description": "无香型蜡烛"}),
        ({"description": "Sin estampado"}, {"description": "纯色款"}),
        ({"description": "Sin tener que ensuciarse las manos"}, {"description": "不易弄脏双手"}),
        ({"description": "Piel suave y sin irritaciones"}, {"description": "肌肤柔软，帮助减少刺激"}),
        ({"description": "Papel sin grasa"}, {"description": "防油纸"}),
        ({"description": "Almacena las cosas para que no cojan polvo"}, {"description": "收纳物品，防尘"}),
        ({"description": "Los calcetines no sobresalen de la zapatilla"}, {"description": "袜子穿在鞋内不易露出"}),
        ({"description": "Los calcetines no sobresalen del calzado"}, {"description": "袜口穿鞋后不易露出"}),
        ({"description": "Los calcetines no sobresalen por encima de los zapatos"}, {"description": "袜子穿鞋后不易露出"}),
        ({"description": "Calcetines que no se ven en el zapato"}, {"description": "穿鞋后不易露出"}),
        ({"description": "Calcetines que no se verán con tus zapatos"}, {"description": "穿鞋后不易露出"}),
        ({"description": "Calcetines invisibles con las zapatillas"}, {"description": "穿运动鞋时不易露出"}),
        ({"description": "El papel es resistente sin comprometer la calidad"}, {"description": "纸张强韧，在不影响品质的前提下使用"}),
        ({"description": "Gel sin fosfatos"}, {"description": "无磷酸盐凝胶"}),
        ({"description": "No tengas charcos en la encimera"}, {"description": "避免台面积水"}),
        ({"description": "No te quedes sin asientos"}, {"description": "可轻松增加座位"}),
        ({"description": "Asiento de inodoro universal"}, {"description": "通用马桶座圈"}),
        ({"description": "Harina sin grumos"}, {"description": "筛除面粉结块"}),
        ({"description": "Ya no se desplazarán las cosas en los armarios"}, {"description": "防止橱柜内物品滑动"}),
        ({"description": "No solo es funcional, sino también decorativo"}, {"description": "不仅实用，还具有装饰性"}),
        ({"description": "Sin importar la estación"}, {"description": "无论哪个季节"}),
        ({"description": "Hay nada menos que 72 páginas"}, {"description": "多达72页"}),
    )
    for source, target in faithful:
        result = validate_model_output(source, target, expected_fields=("description",))
        assert result.accepted, (source, target, result.field_reasons)

    source_contains_another_negation = validate_model_output(
        {"description": "No solo es práctico, sino también sin cables"},
        {"description": "不仅实用，而且无需线缆"},
        expected_fields=("description",),
    )
    assert source_contains_another_negation.accepted, source_contains_another_negation.field_reasons

    difficult_access_is_not_a_negative_claim = validate_model_output(
        {"description": "Ideal para limpiar zonas de difícil acceso"},
        {"description": "适合清洁难以触及的位置"},
        expected_fields=("description",),
    )
    irresistible_is_not_a_negative_claim = validate_model_output(
        {"description": "Un sabor irresistible"},
        {"description": "令人无法抗拒的口味"},
        expected_fields=("description",),
    )
    protective_is_not_a_negative_claim = validate_model_output(
        {"description": "Protege de la pintura, la humedad y la suciedad"},
        {"description": "防油漆、防潮、防污"},
        expected_fields=("description",),
    )
    unsupported_seam_claim = validate_model_output(
        {"description": "Invisibles debajo de la ropa"},
        {"description": "无缝设计，穿在衣物下不易显痕"},
        expected_fields=("description",),
    )
    assert difficult_access_is_not_a_negative_claim.accepted, difficult_access_is_not_a_negative_claim.field_reasons
    assert irresistible_is_not_a_negative_claim.accepted, irresistible_is_not_a_negative_claim.field_reasons
    assert protective_is_not_a_negative_claim.accepted, protective_is_not_a_negative_claim.field_reasons
    assert "NEGATION_HALLUCINATED" in unsupported_seam_claim.reasons


def test_model_guard_recognizes_chinese_never_again_and_contextual_irritation_paraphrases():
    from action_tracker.translation.model_guard import validate_model_output

    faithful_never_again = (
        ("No llegues tarde nunca más", "再也不迟到"),
        ("Nunca más volverás a tener los pies fríos", "再也不怕脚冷"),
        ("Nunca querrás volver a quitártelo", "穿上后再也不想脱下来"),
        ("Nunca volverás a quedarte sin batería", "从此不怕没电"),
        ("No volverás a preocuparte por quedarte sin asientos", "不必再担心没有座位"),
    )
    for source, target in faithful_never_again:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",),
        )
        assert result.accepted, (source, target, result.field_reasons)

    faithful_irritation = validate_model_output(
        {"description": "Piel suave y sin irritaciones"},
        {"description": "肌肤柔软，帮助减少剃须后的刺激"},
        expected_fields=("description",),
    )
    assert faithful_irritation.accepted, faithful_irritation.field_reasons

    weakened_never_again = validate_model_output(
        {"description": "Nunca más perderás la maleta"},
        {"description": "帮助减少行李丢失"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in weakened_never_again.reasons

    unsupported_negation = validate_model_output(
        {"description": "Mantén tus pertenencias localizadas"},
        {"description": "再也不丢失物品"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_negation.reasons

    positive_idiom = validate_model_output(
        {"description": "Nunca se tienen suficientes horquillas"},
        {"description": "发夹永远不嫌多"},
        expected_fields=("description",),
    )
    assert positive_idiom.accepted, positive_idiom.field_reasons


def test_model_guard_rejects_sulfate_free_claim_without_same_field_source_evidence():
    from action_tracker.translation.model_guard import validate_model_output

    unsupported = validate_model_output(
        {"description": "Fórmula vegana"},
        {"description": "纯素配方，无硫酸盐基底"},
        expected_fields=("description",),
    )
    assert "UNSUPPORTED_NEGATIVE_ATTRIBUTE" in unsupported.reasons

    for source in (
        "Fórmula vegana sin sulfatos",
        "Fórmula vegana libre de sulfatos",
        "Fórmula vegana que no contiene SLS",
    ):
        supported = validate_model_output(
            {"description": source},
            {"description": "纯素配方，不含硫酸盐"},
            expected_fields=("description",),
        )
        assert "UNSUPPORTED_NEGATIVE_ATTRIBUTE" not in supported.reasons, (source, supported.field_reasons)


def test_model_guard_aligns_explicit_exclusions_zero_alcohol_and_positive_idioms():
    from action_tracker.translation.model_guard import validate_model_output

    faithful = (
        ({"description": "Excl. cable de carga USB-C"}, {"description": "不含USB-C充电线"}),
        ({"description": "Toallitas completamente libres de plástico"}, {"description": "湿巾不含塑料"}),
        ({"description": "Elimina la necesidad de utilizar rizadores calientes"}, {"description": "无需使用高温卷发器"}),
        ({"description": "Así te ahorras utilizar un vaso medidor"}, {"description": "无需量杯"}),
        ({"description": "Se acabaron los pies fríos"}, {"description": "再也不怕脚冷"}),
        ({"description": "Se acabaron las superficies duras y las noches incómodas."}, {"description": "告别坚硬表面和不舒适的夜晚。"}),
        ({"description": "Se acabaron los malos olores en los zapatos."}, {"description": "让鞋子异味消失！"}),
        ({"description": "Sin aditivos artificiales."}, {"description": "无人工添加剂。"}),
        ({"description": "Estas bandas te dejan las piernas sin vello."}, {"description": "这些脱毛蜡条让双腿无毛。"}),
        ({"description": "No desprende olor."}, {"description": "无明显气味。"}),
        ({"description": "Sin sal."}, {"description": "不加盐。"}),
        ({"description": "Nueces sin tostar."}, {"description": "未经烘烤的坚果。"}),
        ({"description": "Frutas sin procesar (crudas)."}, {"description": "未经加工的生坚果。"}),
        ({"description": "Sin calorías."}, {"description": "零热量。"}),
        ({"description": "Sin arañazos."}, {"description": "表面无划痕。"}),
        ({"description": "Rulos sin calor."}, {"description": "免加热卷发器。"}),
        ({"description": "La fórmula no deja sensación grasa."}, {"description": "配方不油腻。"}),
        ({"description": "No deja marcas blancas en la ropa."}, {"description": "不易在衣物上留下白痕。"}),
        ({"description": "No deja pasar la humedad."}, {"description": "防潮。"}),
        ({"description": "No deja pasar ni una gota."}, {"description": "防水。"}),
        ({"description": "Así no se verá afectado por el aire y el agua."}, {"description": "可隔绝空气和水分。"}),
        ({"description": "No deja espuma."}, {"description": "无泡配方。"}),
        ({"description": "No gotea."}, {"description": "不滴落。"}),
        ({"description": "Nada se te resbalará de las manos."}, {"description": "物品不易从手中滑落。"}),
        ({"description": "Nada se pega a la sartén."}, {"description": "食物不易粘锅。"}),
        ({"description": "El tejido se adhiere menos a la piel."}, {"description": "面料不易粘附皮肤。"}),
        ({"description": "No quieres tenerlo a la vista."}, {"description": "不希望外露。"}),
        ({"description": "Esta bolsa evitará el uso innecesario de bolsas de plástico."}, {"description": "这款购物袋可减少一次性塑料袋的使用。"}),
        ({"description": "En nada de tiempo tendrás una bebida deliciosa."}, {"description": "很快就能得到一杯美味饮品。"}),
        ({"description": "Nada más llegar a casa, puedes usarlo."}, {"description": "到家后即可使用。"}),
        ({"description": "Pilas alcalinas con tecnología antigoteo."}, {"description": "采用防漏液技术的碱性电池。"}),
        ({"description": "Pegamento sin disolvente."}, {"description": "无溶剂胶水。"}),
        ({"description": "Gomas para el pelo sin metal."}, {"description": "无金属发圈。"}),
        ({"description": "Sujetador sin aros."}, {"description": "无钢圈文胸。"}),
        ({"description": "Sin banda ceñida en la cintura."}, {"description": "腰部无紧束带。"}),
        ({"description": "Sin PVC y pintable."}, {"description": "无PVC且可涂刷。"}),
        ({"description": "El bloqueo RFID impide el robo sin contacto."}, {"description": "RFID屏蔽功能可防止非接触式盗刷。"}),
        ({"description": "Sin duda, es la favorita de tu perro."}, {"description": "无疑是狗狗最喜欢的款式。"}),
        ({"description": "Pilas AA no incluidas."}, {"description": "需另配 AA 电池。"}),
        ({"description": "Bolsas para cubos con o sin pedal."}, {"description": "适用于脚踏桶或普通垃圾桶。"}),
        ({"description": "No se decolora."}, {"description": "不易褪色。"}),
        ({"description": "No se desliza."}, {"description": "不滑动。"}),
        ({"description": "Guárdalo sin que ocupe mucho espacio."}, {"description": "收纳时不占空间。"}),
        (
            {"description": "Nunca tendrás los pies demasiado fríos ni demasiado calientes."},
            {"description": "温度调节，保持双脚舒适。"},
        ),
        ({"description": "Sin fragancia y sin microplásticos."}, {"description": "无香精、无微塑料。"}),
        ({"description": "Sin preocupaciones."}, {"description": "轻松无忧。"}),
        ({"description": "Una mezcla para compartir, o a lo mejor no."}, {"description": "美味混合糖果，适合分享或自己享用。"}),
        ({"description": "No te preocupes."}, {"description": "别担心。"}),
        ({"description": "No te preocupes por la limpieza."}, {"description": "无需担心清洁问题。"}),
        ({"description": "No tengas que preocuparte por el riesgo de incendio."}, {"description": "无需担心火灾风险。"}),
        ({"description": "No te olvides de retirar el plástico protector antes de usarlo."}, {"description": "使用前记得撕下保护膜。"}),
        ({"description": "Y no lo olvides: ¡huele a frescura!"}, {"description": "别忘了，这款产品气味清新。"}),
        ({"description": "Ya no tienes por qué aburrirte en las fiestas."}, {"description": "这款游戏让派对不再无聊。"}),
        ({"description": "Hidrata el cabello y hace que brille como nunca."}, {"description": "滋润头发并提升光泽。"}),
        ({"description": "No importa qué más te pongas."}, {"description": "无论再搭配什么。"}),
        ({"description": "El techo tampoco se escapará de la limpieza."}, {"description": "天花板也能清洁到位。"}),
        ({"description": "Fuerte y, sin embargo, fácil de rasgar."}, {"description": "粘性强，但仍易于手撕。"}),
        ({"description": "¿Lavar los platos es aburrido? ¡De ninguna manera!"}, {"description": "洗碗很无聊？绝不！"}),
        ({"description": "Nunca ha sido tan fácil."}, {"description": "从未如此简单。"}),
        ({"description": "No son solo prácticos para comer."}, {"description": "不只是适合进食，也可作为装饰围嘴。"}),
        ({"description": "Diversión sin fin."}, {"description": "无尽乐趣。"}),
        (
            {"description": "Esta toalla no tiene nada que envidiar a las de los hoteles."},
            {"description": "这条毛巾品质不输酒店用巾。"},
        ),
        (
            {"description": "No hay nada mejor que una tarjeta escrita a mano."},
            {"description": "没有什么比手写卡片更好的了。"},
        ),
        (
            {"description": "Compresas suaves e inoloras."},
            {"description": "卫生巾柔软无香。"},
        ),
        ({"description": "No pasa nada si se asoman los calcetines."}, {"description": "袜子稍微露出也没关系。"}),
        ({"description": "Carreteras sin asfaltar."}, {"description": "未铺装道路。"}),
        ({"description": "Apaga el soporte cuando no lo utilices."}, {"description": "不使用时放下支架。"}),
        ({"description": "No te pierdas esta variedad."}, {"description": "多种款式可选。"}),
        ({"description": "¿No te apetece lavar platos ni cubiertos?"}, {"description": "适合聚会的一次性纸杯。"}),
        ({"description": "Limpiar la bandeja no sea tu tarea favorita."}, {"description": "让清理猫砂盆更轻松。"}),
        ({"description": "¿No sabes qué hacer con tu cabello, pero quieres que quede bonito?"}, {"description": "可打造漂亮发型。"}),
        ({"description": "Después no ya podrás parar de sonreír."}, {"description": "让牙齿更白更自然。"}),
        ({"description": "Por un café Lungo, no te importará salir de la cama."}, {"description": "会心甘情愿起床品尝一杯Lungo。"}),
        ({"description": "Nunca perderás nada y lo tendrás todo a mano."}, {"description": "透明收纳，物品一目了然，取用方便。"}),
        ({"description": "Unos calcetines sin desorden."}, {"description": "收纳整洁有序的袜子。"}),
        ({"description": "Hermético al aire y los olores."}, {"description": "防漏气、防串味。"}),
        ({"description": "Pintura adecuada para metales no ferrosos."}, {"description": "适用于有色金属。"}),
        ({"description": "¡Dosificar nunca había sido tan fácil!"}, {"description": "独立凝珠便于准确取量。"}),
        ({"description": "Cortinas para tu hogar sin que falte la luz."}, {"description": "窗帘不遮挡光线。"}),
        ({"description": "El caramelo es blando y sin menta."}, {"description": "软糖，无薄荷。"}),
        ({"description": "No debería faltar en tu colección."}, {"description": "收藏中当然少不了。"}),
        ({"description": "Fabricada sin sustancias nocivas."}, {"description": "制造过程中不使用有害物质。"}),
        ({"description": "No hay nada más molesto que se bajen los calcetines."}, {"description": "袜子不易下滑。"}),
        ({"description": "No deja el cabello graso."}, {"description": "不使头发油腻。"}),
        ({"description": "El adhesivo no hace ruidos molestos."}, {"description": "胶带放卷无恼人噪音。"}),
        ({"description": "Cabello seco y sin vitalidad."}, {"description": "适合干燥、缺乏活力的头发。"}),
        ({"description": "Dosificador en cápsulas: nunca había sido tan fácil."}, {"description": "凝珠让取量从未如此简单。"}),
        ({"description": "Por supuesto, las fiestas incluyen un árbol de Navidad."}, {"description": "圣诞节当然少不了圣诞树。"}),
        ({"description": "No te aburrirás nunca con este libro."}, {"description": "这本书能带来有趣的涂色体验。"}),
        ({"description": "No querrás quitártelos nunca."}, {"description": "穿着舒适，让人舍不得脱下。"}),
        ({"description": "El marco hace destacar la foto sin que te distraiga."}, {"description": "简洁设计，可突出照片内容。"}),
        ({"description": "Sin tener que meter el teléfono en los bolsillos."}, {"description": "腾出双手，让手机随手可用。"}),
        ({"description": "No dudes en elegir varios."}, {"description": "不妨多选几件。"}),
        ({"description": "Con tecnología antiadherente para que no se queme."}, {"description": "采用防粘防焦技术。"}),
        ({"description": "Sin mangas."}, {"description": "无袖。"}),
        ({"description": "Anticarreras gracias a las fibras resistentes."}, {"description": "采用耐用纤维，具有防勾丝功能。"}),
        ({"description": "Medias antirrotura."}, {"description": "防勾丝丝袜。"}),
        ({"description": "Sin talón."}, {"description": "无后跟款式。"}),
        ({"description": "Esta cesta plegable es práctica en casa."}, {"description": "这款收纳篮可折叠，不用时可折叠收起。"}),
        ({"description": "Se mantienen en su sitio durante su uso."}, {"description": "穿着时不易滑落。"}),
        ({"description": "¿A que mola? Crea una lamparita."}, {"description": "是不是很酷？打造小台灯。"}),
        ({"description": "Compatible con todos los mangos, excepto Simply Venus."}, {"description": "兼容所有手柄，但不包括 Simply Venus。"}),
        (
            {"description": "Alfombrilla para recortar autocicatrizante."},
            {"description": "自修复切割垫，不易留下明显刀痕。"},
        ),
        ({"description": "Sin complicaciones para afeitarse"}, {"description": "剃须过程无需复杂操作"}),
        ({"description": "Una creación con la que todo es posible"}, {"description": "用它创作，无所不能"}),
    )
    for source, target in faithful:
        result = validate_model_output(source, target, expected_fields=("description",))
        assert result.accepted, (source, target, result.field_reasons)

    no_worries_is_name = validate_model_output(
        {"name": "Salvaslips Femapure No Worries Normal"},
        {"name": "日用护垫"},
        expected_fields=("name",),
    )
    assert "NEGATION_DROPPED" not in no_worries_is_name.reasons

    unsupported_stays_in_place_claim = validate_model_output(
        {"description": "Cómodos y elásticos."},
        {"description": "穿着时不易滑落。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_stays_in_place_claim.reasons

    unsupported_slip_prevention = validate_model_output(
        {"description": "Guantes cómodos y resistentes."},
        {"description": "物品不易从手中滑落。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_slip_prevention.reasons

    unsupported_nonstick_claim = validate_model_output(
        {"description": "Sartén elegante con acabado de mármol."},
        {"description": "食物不易粘锅。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_nonstick_claim.reasons

    unsupported_reduced_adherence = validate_model_output(
        {"description": "Tejido fresco de algodón."},
        {"description": "面料不易粘附皮肤。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_reduced_adherence.reasons

    unsupported_plastic_bag_reduction = validate_model_output(
        {"description": "Bolsa plegable y reutilizable."},
        {"description": "可减少一次性塑料袋的使用。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_plastic_bag_reduction.reasons

    unsupported_fire_safety_reassurance = validate_model_output(
        {"description": "Práctico y fácil de usar."},
        {"description": "无需担心火灾风险。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_fire_safety_reassurance.reasons

    dropped_salt_claim = validate_model_output(
        {"description": "Sin sal."},
        {"description": "健康零食。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in dropped_salt_claim.reasons

    omitted_battery_exclusion = validate_model_output(
        {"description": "Pilas AA no incluidas."},
        {"description": "使用 AA 电池。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in omitted_battery_exclusion.reasons

    omitted_pedal_alternative = validate_model_output(
        {"description": "Bolsas para cubos con o sin pedal."},
        {"description": "适用于脚踏桶。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in omitted_pedal_alternative.reasons

    strapless_mistranslated_as_sleeveless = validate_model_output(
        {"description": "Camisa sin tirantes."},
        {"description": "无袖上衣。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in strapless_mistranslated_as_sleeveless.reasons

    omitted_low_cut_sock_claim = validate_model_output(
        {"description": "Estos calcetines cortos llegan hasta el tobillo, por lo que no sobresalen de la zapatilla."},
        {"description": "适合搭配运动鞋；尺码 35/38 和 39/42。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in omitted_low_cut_sock_claim.reasons

    weakened_no_marks_claim = validate_model_output(
        {"description": "El lavavajillas líquido limpia la vajilla sin dejar marcas."},
        {"description": "可将餐具洗净并减少水痕。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in weakened_no_marks_claim.reasons

    omitted_no_neck_pain_claim = validate_model_output(
        {"description": "Con esta almohada cervical no te dolerá el cuello después de hacer una siesta."},
        {"description": "可在休息时支撑颈部。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" in omitted_no_neck_pain_claim.reasons

    omitted_seamless_name_claim = validate_model_output(
        {"name": "Bóxers sin costuras Second Skin"},
        {"name": "女士平角内裤"},
        expected_fields=("name",),
    )
    assert "NEGATION_DROPPED" in omitted_seamless_name_claim.reasons

    callus_negation_preserved = validate_model_output(
        {"description": "Para pies suaves y sin durezas."},
        {"description": "让双脚柔软无老茧。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" not in callus_negation_preserved.reasons

    unsupported_callus_negation = validate_model_output(
        {"description": "Para pies suaves."},
        {"description": "让双脚柔软无老茧。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_callus_negation.reasons

    bubble_negation_preserved = validate_model_output(
        {"description": "Papel pintado sin burbujas ni marcas de cola."},
        {"description": "墙纸施工无气泡、无胶痕。"},
        expected_fields=("description",),
    )
    assert "NEGATION_DROPPED" not in bubble_negation_preserved.reasons

    unsupported_bubble_negation = validate_model_output(
        {"description": "Papel pintado con acabado resistente."},
        {"description": "墙纸施工无气泡。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_bubble_negation.reasons

    negation_equivalences = (
        ("Interruptores sin cable.", "无线开关。"),
        ("Asegúrate de que la superficie esté seca y sin polvo.", "施工前确保表面干燥无尘。"),
        ("Aspirador sin bolsa.", "无尘袋吸尘器。"),
        ("Sin cinta de ajuste en la cintura.", "腰部无调节带。"),
        ("Gomas para el pelo sin que se deshagan.", "发圈固定细辫，不易松散。"),
        ("Marcadores que escriben sin que se borre.", "记号笔书写后不易擦除。"),
        ("La arena para gatos es sin polvo.", "这款猫砂不易扬尘。"),
        ("Bate sin salpicar.", "搅打不飞溅。"),
        ("Delineador para una línea precisa sin dejar manchas.", "眼线精准，不易晕染。"),
        ("Botella térmica 100 % antigoteo.", "保温瓶100%防漏。"),
        ("La botella no gotea.", "瓶子防漏。"),
        ("Con protección contra las fugas.", "带防漏保护。"),
        ("Cierre a prueba de fugas.", "瓶盖防漏。"),
        ("Tapa antiderrames.", "配防漏盖。"),
        ("Sistema de recogida de material: Sin bolsa.", "集尘方式：无袋。"),
        ("No deja ningún residuo.", "不留残留。"),
        ("Imagen nítida y sin huellas dactilares.", "画面清晰且无指纹。"),
        ("Carga tu smartphone sin cables.", "手机免插线充电。"),
        ("No hay problema.", "没问题。"),
        ("No quieres que te caigan mechones en la cara.", "可防止碎发掉到脸上。"),
        ("No hay nada más molesto que se bajen los calcetines durante el deporte.", "运动时袜子不易下滑。"),
        ("Una camiseta lisa.", "防止碎发掉到脸上。"),
        ("Nada te distraerá de tu entrenamiento.", "袜子不易在活动中下滑。"),
        ("La cinta no hace mucho ruido.", "胶带放卷声音较小。"),
        ("Escribe sin que nadie pueda verlos.", "使用隐形墨水书写。"),
        ("Protege el vidrio para que no coja olores indeseados.", "保护玻璃，避免沾染异味。"),
        ("Estas gomas no dañan el cabello.", "这些发圈不易损伤头发。"),
        ("Su fórmula suave limpia las manos sin resecarlas.", "温和配方清洁双手，不会使手部干燥。"),
        ("No se rompen en caso de caída.", "即使跌落也不易破损。"),
        ("Coloca comida encima para que el flujo de aire no lo levante.", "放置足够食物，避免烘焙纸被气流吹起。"),
        ("Se mantienen bien sujetos durante el movimiento y se mantienen en su lugar.", "运动时贴合稳固，不易移位。"),
        ("Permanece durante horas sin dejar marca.", "长时间穿着也不易勒痕。"),
        ("Gracias a la pulsera, no perderás tus llaves tan fácilmente.", "配有腕带，方便抓取并减少钥匙丢失。"),
        ("Con estos cordones no volverás a perder el móvil.", "有了这款挂绳，不易丢失手机。"),
        ("Serás menos propenso a perderlo.", "手机不易丢失。"),
        ("Rizador de pelo sin calor.", "无热卷发器。"),
        ("Para que tu coche no se caliente demasiado.", "让汽车在高温天气下不易过热。"),
        ("Sin tener que pasarte horas inflándolos a mano.", "可快速为气球充气。"),
        ("Nunca te los cepillarás demasiado tiempo.", "内置计时器，避免刷牙时间过长。"),
        ("Disfruta de hasta 8 horas de lectura sin interrupciones.", "享受长达8小时不间断阅读。"),
        ("Camisa sin tirantes.", "无肩带上衣。"),
        ("Elimina los malos olores del hogar.", "可快速去除家中异味。"),
        ("Elimina de manera rápida y sencilla los malos olores del hogar.", "可快速去除家中异味。"),
        ("Previene los malos olores en el contenedor.", "有助于防止垃圾桶异味。"),
        ("Refresca el inodoro tras cada uso para prevenir los malos olores.", "每次使用后清新马桶，帮助防止异味。"),
        ("Combate los malos olores.", "帮助抑制异味。"),
        ("Absorbe, elimina y neutraliza los malos olores.", "吸收并中和异味。"),
        ("Te protege de la sudoración y los malos olores.", "可帮助防止出汗和异味。"),
        ("Separa los alimentos sin mezclarlos.", "内置隔层，可将不同食物分开存放。"),
        ("Sin humedad con este antihumedad.", "使用这款吸湿器，帮助保持环境干燥。"),
        ("Aplica la mascarilla sin ensuciarlo todo.", "棒状设计，使用时不易弄脏。"),
        ("Pinta sin preocuparte por las manchas de pintura en el suelo.", "铺上保护膜，防止油漆污染地板。"),
        ("El pegamento no se apelmaza ni mancha.", "胶水不结块，也不会染色。"),
        (
            "El adaptador USB-C es compatible con móviles y tabletas modernos que no tienen la toma de auriculares de 3,5 mm.",
            "附 USB-C 转接器，兼容带 3.5 毫米接口或仅有 USB-C 接口的手机和平板。",
        ),
    )
    for source, target in negation_equivalences:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",)
        )
        assert "NEGATION_DROPPED" not in result.reasons, (source, target, result.reasons)

    odor_claim_strength_pairs = (
        ("Elimina los malos olores del hogar.", "可快速去除家中异味。", False),
        ("Elimina los malos olores del hogar.", "可快速减少家中异味。", True),
        ("Previene los malos olores del contenedor.", "帮助防止垃圾桶异味。", False),
        ("Previene los malos olores del contenedor.", "帮助减少垃圾桶异味。", True),
        ("No elimina los malos olores.", "去除异味。", False),
    )
    for source, target, is_weakened in odor_claim_strength_pairs:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",)
        )
        if is_weakened:
            assert "NEGATION_DROPPED" in result.reasons, (source, target, result.reasons)
        elif source.startswith("No "):
            assert "NEGATION_HALLUCINATED" in result.reasons, (source, target, result.reasons)
        else:
            assert "NEGATION_DROPPED" not in result.reasons, (source, target, result.reasons)

    rhetorical_questions_are_not_product_negation = (
        ("¿No te gusta hacer agujeros en la pared?", "不喜欢在墙上打孔吗？"),
        ("¿No sabes lo que quieres hacer con tu cabello, pero quieres que quede bonito?", "多种造型可选，轻松打造好看发型。"),
        ("La toalla no tiene nada que enviar a las de los hoteles.", "浴巾柔软亲肤，适合在家享受舒适体验。"),
        ("Este producto de los tiempos antiguos no es gloria del pasado.", "这款产品至今依然实用。"),
        ("Su capacidad es perfecta por si no te apetece estar rellenándola constantemente.", "容量充足，减少频繁添水。"),
        ("Procura no comerte todas las galletas de una sentada.", "饼干香脆可口，适合随时享用。"),
        ("No son imágenes impresionantes, sino imágenes de píxeles como en la vieja escuela.", "内置复古像素风游戏，带来怀旧体验。"),
        ("No sabes qué regalar. El té siempre es una buena idea.", "不知道送什么时，茶总是不错的选择。"),
        ("No te pueden faltar estas prácticas bolsas organizadoras.", "这款实用收纳袋适合整理旅行用品。"),
        ("No tardarás nada en secar la vajilla limpia con estos paños.", "这几条厨房布能快速擦干洗净的餐具。"),
        ("No crees que esta sombra también te quedaría de maravilla cerca de los lagrimales.", "这款眼影也很适合用在内眼角。"),
        ("Disfruta sin límite con estas pompas de jabón.", "用这款泡泡玩具尽情玩耍。"),
        ("La forma ideal de mimar tu piel sin tener que salir de casa.", "在家也能享受舒适的护肤体验。"),
    )
    for source, target in rhetorical_questions_are_not_product_negation:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",)
        )
        assert "NEGATION_DROPPED" not in result.reasons, (source, target, result.reasons)

    unclassified_details = validate_model_output(
        {"details": "Origen del té: Sri Lanka - sin clasificación"},
        {"details": "茶叶产地：斯里兰卡—未分级"},
        expected_fields=("details",),
    )
    assert "NEGATION_DROPPED" not in unclassified_details.reasons

    unsupported_unclassified_details = validate_model_output(
        {"details": "Origen del té: Sri Lanka - clasificación estándar"},
        {"details": "茶叶产地：斯里兰卡—未分级"},
        expected_fields=("details",),
    )
    assert "NEGATION_HALLUCINATED" in unsupported_unclassified_details.reasons

    source_bound_paraphrase_mismatches = (
        ("El aparato no produce un ruido alto.", "Bajo ruido."),
        ("El texto no se puede distinguir.", "Tinta invisible."),
        ("No maltrata el cabello.", "Gomas para el cabello resistentes."),
        ("El vaso no se rompe al caer.", "Vaso resistente."),
    )
    for source, target in source_bound_paraphrase_mismatches:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",)
        )
        assert "NEGATION_DROPPED" in result.reasons, (source, target, result.reasons)

    unsupported_negation_equivalences = (
        ("Interruptores con cable.", "无线开关。"),
        ("Asegúrate de que la superficie esté seca.", "施工前确保表面干燥无尘。"),
        ("Aspirador con bolsa.", "无尘袋吸尘器。"),
        ("Cinta de ajuste en la cintura.", "腰部无调节带。"),
        ("Gomas resistentes para el pelo.", "发圈固定细辫，不易松散。"),
        ("Marcadores de tinta permanente.", "记号笔书写后不易擦除。"),
        ("Arena para gatos de arcilla natural.", "这款猫砂不易扬尘。"),
        ("Prepara batidos en casa.", "搅打不飞溅。"),
        ("Delineador líquido negro.", "眼线不易晕染。"),
        ("Botella térmica de acero.", "保温瓶100%防漏。"),
        ("Botella térmica de acero.", "带防漏保护。"),
        ("Sistema de recogida de material: Con bolsa.", "集尘方式：无袋。"),
        ("Un adhesivo resistente.", "不留残留。"),
        ("Una pantalla de alta resolución.", "画面清晰且无指纹。"),
        ("Carga tu smartphone con cable.", "手机免插线充电。"),
        ("Un llavero práctico con pulsera.", "方便抓取并减少钥匙丢失。"),
        ("Un ambientador con aroma a lavanda.", "可帮助减少家中异味。"),
        ("Una camiseta de manga corta.", "无肩带上衣。"),
        ("Una lámpara de lectura recargable.", "享受长达8小时不间断阅读。"),
        ("Cortinas de tejido translúcido.", "窗帘不遮挡光线。"),
        ("Dulce de caramelo blando.", "软糖，无薄荷。"),
        ("Un utensilio de cocina.", "制造过程中不使用有害物质。"),
        ("Cabello largo y brillante.", "适合干燥、缺乏活力的头发。"),
        ("Un marco de madera.", "简洁设计，可突出照片内容。"),
        ("Lleva un moño liso.", "防止碎发掉到脸上。"),
        ("Calcetines cómodos para deporte.", "袜子不易在活动中下滑。"),
        ("La cinta adhesiva genera mucho ruido.", "不会发出噪音。"),
    )
    for source, target in unsupported_negation_equivalences:
        result = validate_model_output(
            {"description": source}, {"description": target}, expected_fields=("description",)
        )
        assert "NEGATION_HALLUCINATED" in result.reasons, (source, target, result.reasons)

    positive_wireless_adjective = validate_model_output(
        {"name": "Ratón inalámbrico silencioso."},
        {"name": "无线静音鼠标。"},
        expected_fields=("name",),
    )
    assert "NEGATION_HALLUCINATED" not in positive_wireless_adjective.reasons

    supported_wireless_stereo_standard = validate_model_output(
        {"description": "Empareja dos altavoces mediante la tecnología True Wireless Stereo."},
        {"description": "支持True Wireless Stereo（真无线立体声），可将两个音箱配对。"},
        expected_fields=("description",),
    )
    assert "NEGATION_HALLUCINATED" not in supported_wireless_stereo_standard.reasons

    zero_alcohol = validate_model_output(
        {"description": "Con un 0% de alcohol y 5 vitaminas"},
        {"description": "不含酒精，含5种维生素"},
        expected_fields=("description",),
    )
    assert zero_alcohol.accepted, zero_alcohol.field_reasons

    zero_alcohol_numeric_preserved = validate_model_output(
        {"description": "Con un 0% de alcohol y 5 vitaminas"},
        {"description": "0%酒精，含5种维生素"},
        expected_fields=("description",),
    )
    assert zero_alcohol_numeric_preserved.accepted, zero_alcohol_numeric_preserved.field_reasons

    zero_alcohol_phrase_and_numeric = validate_model_output(
        {"description": "Con un 0% de alcohol y 5 vitaminas"},
        {"description": "不含酒精（0%），含5种维生素"},
        expected_fields=("description",),
    )
    assert zero_alcohol_phrase_and_numeric.accepted, zero_alcohol_phrase_and_numeric.field_reasons

    official_inoloro_field = validate_model_output(
        {"details": "Inoloro: Sí"},
        {"details": "无异味：是"},
        expected_fields=("details",),
    )
    assert official_inoloro_field.accepted, official_inoloro_field.field_reasons

    faithful_detail_negation_aliases = (
        ("Tipo de cierre: Sin cierre", "闭合类型：无闭合"),
        ("Ocasión específica: Ninguna ocasión", "适用场合：无特定场合"),
        ("Sin silicona: Sí", "无硅油：是"),
        ("Sin ácidos: Sí", "无酸：是"),
    )
    for spanish, chinese in faithful_detail_negation_aliases:
        result = validate_model_output(
            {"details": spanish}, {"details": chinese}, expected_fields=("details",)
        )
        assert result.accepted, (spanish, chinese, result.field_reasons)

    official_inoloro_alias = validate_model_output(
        {"details": "Inoloro: Sí"},
        {"details": "无味：是"},
        expected_fields=("details",),
    )
    assert official_inoloro_alias.accepted, official_inoloro_alias.field_reasons

    inodoro_is_not_inoloro = validate_model_output(
        {"details": "Inodoro: Sí"},
        {"details": "无异味：是"},
        expected_fields=("details",),
    )
    assert "NEGATION_HALLUCINATED" in inodoro_is_not_inoloro.reasons

    changed_other_fact = validate_model_output(
        {"description": "Con un 0% de alcohol y 5 vitaminas"},
        {"description": "不含酒精，含4种维生素"},
        expected_fields=("description",),
    )
    assert "NUMERIC_DROPPED" in changed_other_fact.reasons
