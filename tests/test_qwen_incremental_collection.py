import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "collect_qwen_incremental_candidates.py"
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_balanced_selection_prefers_human_reviewed_within_each_category():
    module = _load()
    rows = [
        {"sku": "2", "label_tier": "DICTIONARY_OR_MODEL", "target": {"cat1": "玩具"}},
        {"sku": "1", "label_tier": "HUMAN_REVIEWED", "target": {"cat1": "玩具"}},
        {"sku": "3", "label_tier": "DICTIONARY_OR_MODEL", "target": {"cat1": "厨房餐具"}},
    ]
    assert [item["sku"] for item in module._balanced_select(rows, 3)] == ["3", "1", "2"]


def test_unsafe_source_rejects_html_ui_copy_and_cjk():
    module = _load()
    assert module._unsafe_source("<p>producto</p>")
    assert module._unsafe_source("Añadir a tus favoritos")
    assert module._unsafe_source("中文污染")
    assert not module._unsafe_source("Caja de almacenaje")


def test_current_field_holdout_skus_are_excluded_from_incremental_pool(tmp_path):
    module = _load()
    field_dir = tmp_path / "field_conditioned_v1"
    field_dir.mkdir()
    row = '{"metadata":{"sku":"3221933"}}\n'
    (field_dir / "field_conditioned_test.jsonl").write_text(row, encoding="utf-8")
    assert module._current_field_holdout_skus(tmp_path) == {"3221933"}


def test_historical_training_or_reviewed_skus_excludes_splits_and_prior_approved_batches(tmp_path):
    module = _load()
    train = tmp_path / "20260911" / "field_conditioned_v1" / "field_conditioned_train.jsonl"
    approved = tmp_path / "20260910" / "qwen_incremental_approved_500.jsonl"
    candidate = tmp_path / "20260911" / "qwen_incremental_candidate_500.jsonl"
    for path, sku in ((train, "1"), (approved, "2"), (candidate, "3")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"metadata":{"sku":"' + sku + '"}}\n', encoding="utf-8")
    assert module._historical_training_or_reviewed_skus(tmp_path) == {"1", "2"}


def test_artifact_prefix_keeps_legacy_names_and_validates_labels():
    module = _load()
    assert module._artifact_prefix("") == "qwen_incremental_"
    assert module._artifact_prefix("fresh_v2") == "qwen_incremental_fresh_v2_"
    try:
        module._artifact_prefix("Fresh V2")
    except ValueError as exc:
        assert str(exc) == "INVALID_ARTIFACT_LABEL"
    else:
        raise AssertionError("expected invalid label to be rejected")
