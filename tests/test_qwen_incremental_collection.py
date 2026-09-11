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
