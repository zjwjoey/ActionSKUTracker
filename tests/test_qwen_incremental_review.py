from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_qwen_incremental_with_deepseek.py"
SPEC = importlib.util.spec_from_file_location("review_qwen_incremental", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def _six(**overrides: str) -> dict[str, str]:
    result = {field: "" for field in MODULE.FIELDS}
    result.update(overrides)
    return result


def test_duplicate_raw_numeric_facts_may_be_collapsed_but_values_cannot_change():
    source = _six(details="Potencia: 20; Potencia: 20 vatio")
    target = _six(details="功率：20瓦")
    remaining, exceptions = MODULE._guard_with_safe_normalizations(source, target, ())
    assert remaining == []
    assert exceptions == ["details:DUPLICATE_NUMERIC_COLLAPSED"]

    conflict_source = _six(details="Lumen: 3000")
    conflict_target = _six(details="流明：300")
    remaining, exceptions = MODULE._guard_with_safe_normalizations(conflict_source, conflict_target, ())
    assert "details:NUMERIC_DROPPED" in remaining
    assert "details:NUMERIC_HALLUCINATED" in remaining
    assert exceptions == []


def test_source_proper_name_is_not_treated_as_english_residual():
    source = _six(description="Para elegir entre Spidey, Hot Wheels y Winnie the Pooh")
    target = _six(description="可选Spidey、Hot Wheels和Winnie the Pooh主题")
    assert MODULE._source_proper_phrases(source, target) == ("Hot Wheels", "Winnie the Pooh")
    remaining, _ = MODULE._guard_with_safe_normalizations(source, target, ())
    assert remaining == []


def test_confirmed_brand_title_requires_brand_in_official_name_and_preserves_manual_override():
    context = SimpleNamespace(
        manual_by_sku={},
        product_by_sku={"1": {"brand_id": "Disney"}},
        brand_by_id={"Disney": {
            "brand_id": "Disney", "canonical_name": "Disney", "aliases_es": "Disney",
            "review_status": "HUMAN_REVIEWED", "confidence": "REFERENCE",
        }},
    )
    assert MODULE._normalize_confirmed_brand_name(context, "1", "文具盒", "Estuche Disney") == (
        "Disney牌文具盒", True,
    )
    assert MODULE._normalize_confirmed_brand_name(context, "1", "拼图", "Rompecabezas") == (
        "拼图", False,
    )

    context.manual_by_sku["1"] = {"name_zh_standard": "人工文具盒"}
    assert MODULE._normalize_confirmed_brand_name(context, "1", "文具盒", "Estuche Disney") == (
        "人工文具盒", True,
    )
