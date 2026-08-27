import pytest

from action_tracker.dictionary import DictionaryValidationError
from scripts.audit_dictionary import effective_product_view


def test_effective_product_view_applies_field_level_overrides_to_audit_fields():
    products = [{
        "sku": "1001",
        "name_zh_standard": "中文品名待人工核验",
        "cat1_zh": "",
        "translation_status": "NEEDS_REVIEW",
    }]
    manual = [
        {"scope": "product", "key": "1001", "field": "name_zh_standard", "value": "标准商品"},
        {"scope": "product", "key": "1001", "field": "cat1_zh", "value": "家居布置"},
    ]

    effective, by_product = effective_product_view(products, manual)

    assert effective[0]["name_zh_standard"] == "标准商品"
    assert effective[0]["cat1_zh"] == "家居布置"
    assert effective[0]["translation_status"] == "HUMAN_REVIEWED"
    assert by_product["1001"] == {
        "name_zh_standard": "标准商品",
        "cat1_zh": "家居布置",
    }


def test_effective_product_view_rejects_duplicate_product_overrides():
    with pytest.raises(DictionaryValidationError, match="DUPLICATE_PRODUCT_OVERRIDE"):
        effective_product_view(
            [{"sku": "1001", "name_zh_standard": "旧名"}],
            [
                {"scope": "product", "key": "1001", "field": "name_zh_standard", "value": "甲"},
                {"scope": "product", "key": "1001", "field": "name_zh_standard", "value": "乙"},
            ],
        )
