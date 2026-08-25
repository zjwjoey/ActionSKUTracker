import importlib.util
from pathlib import Path


def _load_refiner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refine_dictionary_translations.py"
    spec = importlib.util.spec_from_file_location("refine_dictionary_translations", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reviewed_brand_and_series_candidates_are_excluded(tmp_path):
    path = tmp_path / "brand_review_decisions_20260825.csv"
    path.write_text(
        "sku,source_hash,decision\n"
        "1001,hash-a,CONFIRMED_BRAND_OR_IP\n"
        "1002,hash-b,PRODUCT_SERIES_OR_STYLE\n"
        "1003,hash-c,NON_BRAND_TERM\n",
        encoding="utf-8",
    )
    refiner = _load_refiner()
    assert refiner._reviewed_non_translation_keys(tmp_path) == {
        ("1001", "hash-a"),
        ("1002", "hash-b"),
    }
