import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_stage4_targeted_remediation_queue.py"
    spec = importlib.util.spec_from_file_location("stage4_remediation_queue", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_remediation_exclusions_cover_prediction_test_and_historical_corpora(tmp_path):
    module = _module()
    predictions = tmp_path / "predictions.jsonl"
    _write(predictions, {"sku": "prediction-sku", "source": {"cat1": "A", "cat2": "B"}})
    _write(tmp_path / "stage4_test_only_485.jsonl", {"metadata": {"sku": "frozen-test-sku"}})
    _write(tmp_path / "hard_test_v1_candidate/qwen_hard_test_v1_candidate.jsonl", {"metadata": {"sku": "hard-test-sku"}})
    _write(tmp_path / "qwen_incremental_candidate_10.jsonl", {"metadata": {"sku": "old-candidate-sku"}})
    _write(tmp_path / "combined_gold_incremental/qwen_combined_gold_incremental_train.jsonl", {"metadata": {"sku": "combined-train-sku"}})
    _write(tmp_path / "field_conditioned_v1/field_conditioned_test.jsonl", {"metadata": {"sku": "field-test-sku"}})

    frozen, historical, families = module.load_excluded(tmp_path, predictions)

    assert {"prediction-sku", "frozen-test-sku", "hard-test-sku"} <= frozen
    assert {"old-candidate-sku", "combined-train-sku", "field-test-sku"} <= historical
    assert families == set()


def test_remediation_queue_requires_all_six_official_source_fields():
    module = _module()
    assert module.has_complete_training_source("Nombre", "100 g", "Descripción", "Detalle", ("Cat 1", "Cat 2"))
    assert not module.has_complete_training_source("Nombre", "", "Descripción", "Detalle", ("Cat 1", "Cat 2"))


def test_replacement_quota_parser_rejects_unknown_or_nonpositive_values():
    module = _module()
    quotas = module.parse_class_quotas("BRAND_FACT_LOSS=40,TECH_TOKEN_LOSS=5", 10)
    assert quotas["BRAND_FACT_LOSS"] == 40
    assert quotas["NUMERIC_MISSING"] == 10
    try:
        module.parse_class_quotas("UNKNOWN=3", 10)
    except ValueError as exc:
        assert str(exc) == "INVALID_CLASS_QUOTAS"
    else:
        raise AssertionError("expected invalid quota failure")


def test_production_date_directory_scans_sibling_historical_splits(tmp_path):
    module = _module()
    root = tmp_path / "qwen3_8b"
    current = root / "20260911"
    current.mkdir(parents=True)
    _write(root / "20260908/qwen_field_train.jsonl", {"metadata": {"sku": "historical-sku"}})
    _write(current / "stage4_test_only_485.jsonl", {"metadata": {"sku": "frozen-sku"}})
    predictions = current / "predictions.jsonl"
    _write(predictions, {"sku": "prediction-sku"})
    frozen, split, _ = module.load_excluded(current, predictions)
    assert "frozen-sku" in frozen
    assert "historical-sku" in split
