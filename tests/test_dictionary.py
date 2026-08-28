import csv

import pytest

from action_tracker.dictionary import (
    DictionaryValidationError,
    PRODUCT_DICTIONARY_HEADERS,
    build_product_dictionary,
    category_rows_from_products,
    format_confirmed_brand_title,
    index_product_overrides,
    is_confirmed_brand_record,
    load_dictionary_csv,
    normalize_category_key,
    reconcile_brand_rows,
    product_source_hash,
    write_dictionary_csv,
)
from action_tracker.dictionary_enrichment import processable_candidate_skus, select_candidates
from action_tracker.dictionary_enrichment import DictionaryEnrichmentError, _validate_formal_snapshot
from action_tracker.dictionary_sources import (
    is_polluted_source_field,
    load_clean_historical_spanish_reference,
    restore_spanish_facts,
)


def test_dictionary_keeps_stable_fields_only_and_builds_status():
    records = {
        "1001": {
            "sku": "1001", "canonical_id": "ACT0001001", "name_es": "Producto",
            "name_zh": "标准商品", "cat1_es": "Hogar", "cat1_zh": "家居",
            "spec_es": "2 piezas", "spec_zh": "2件", "current_price": 2.99,
            "translation_status": "HUMAN_REVIEWED", "first_seen": "2026-08-10",
            "last_seen": "2026-08-25",
        }
    }
    out = build_product_dictionary(records, updated_at="2026-08-25")
    assert len(out) == 1
    row = out[0]
    assert row["name_zh_standard"] == "标准商品"
    assert row["cat1_zh"] == "家居"
    assert row["translation_status"] == "HUMAN_REVIEWED"
    assert row["source_last_seen"] == "2026-08-25"
    assert "current_price" not in PRODUCT_DICTIONARY_HEADERS


def test_confirmed_brand_title_rule_adds_one_marker_without_guessing():
    confirmed = {"brand_id": "Stanger", "canonical_name": "Stanger", "confidence": "REFERENCE"}
    provisional = {"brand_id": "Stanger", "canonical_name": "Stanger", "review_status": "NEEDS_HUMAN_REVIEW"}
    assert is_confirmed_brand_record(confirmed) is True
    assert is_confirmed_brand_record(provisional) is False
    assert format_confirmed_brand_title("记号笔", "Stanger") == "Stanger牌记号笔"
    assert format_confirmed_brand_title("stanger 记号笔", "Stanger") == "Stanger牌记号笔"
    assert format_confirmed_brand_title("Stanger牌记号笔", "Stanger") == "Stanger牌记号笔"
    assert format_confirmed_brand_title("Stanger", "Stanger") == "Stanger"


def test_locked_dictionary_values_are_not_overwritten():
    records = {"1001": {"sku": "1001", "name_es": "Nuevo", "name_zh": "模型值", "cat1_zh": "新类目"}}
    existing = {"1001": {"sku": "1001", "name_zh_standard": "人工标准名", "cat1_zh": "家居",
                          "locked": "1", "translation_status": "LOCKED"}}
    row = build_product_dictionary(records, existing, updated_at="2026-08-25")[0]
    assert row["name_zh_standard"] == "人工标准名"
    assert row["cat1_zh"] == "家居"
    assert row["locked"] == "1"
    assert row["review_status"] == "HUMAN_REVIEWED"


def test_legacy_schema_does_not_downgrade_human_reviewed_row():
    existing = {"1001": {
        "sku": "1001", "name_zh_standard": "人工标准名", "translation_status": "HUMAN_REVIEWED",
        "review_status": "HUMAN_REVIEWED", "updated_at": "2026-08-01",
    }}
    row = build_product_dictionary({"1001": {"name_es": "Producto nuevo"}}, existing, updated_at="2026-08-25")[0]
    assert row["name_zh_standard"] == "人工标准名"
    assert row["translation_status"] == "HUMAN_REVIEWED"


def test_legacy_translation_status_is_normalized():
    row = build_product_dictionary(
        {"1001": {"sku": "1001", "name_zh": "标准商品", "translation_status": "OK"}},
        updated_at="2026-08-25",
    )[0]
    assert row["translation_status"] == "LEGACY_UNVERIFIED"


def test_category_seed_is_deduplicated():
    rows = category_rows_from_products([
        {"cat1_es": "Hogar", "cat2_es": "Limpieza"},
        {"cat1_es": "Hogar", "cat2_es": "Limpieza"},
        {"cat1_es": "Moda", "cat2_es": "Ropa"},
    ])
    assert len(rows) == 2
    assert rows[0]["review_status"] == "UNREVIEWED"


def test_category_mapping_uses_fixed_fifteen_category_name():
    mapping = {normalize_category_key("Cuidado personal"): {"cat1_code": "C06", "cat1_zh": "个人美容"}}
    rows = category_rows_from_products([{"cat1_es": "Cuidado personal", "cat2_es": "Uñas"}], mapping)
    assert rows[0]["cat1_code"] == "C06"
    assert rows[0]["cat1_zh"] == "个人美容"
    assert rows[0]["review_status"] == "CAT1_CONFIRMED"


def test_legacy_chinese_category_alias_is_normalized_to_fixed_fifteen_categories():
    mapping = {normalize_category_key("家居维修"): {"cat1_code": "C01", "cat1_zh": "DIY五金"}}
    row = build_product_dictionary({"1001": {"cat1_es": "", "cat1_zh": "家居维修"}},
                                   category_mapping=mapping, updated_at="2026-08-25")[0]
    assert row["cat1_zh"] == "DIY五金"


def test_rebuild_retains_historical_sku_and_stable_timestamp():
    existing = {
        "900": {
            "sku": "900", "name_es_raw": "Producto histórico", "name_zh_standard": "历史商品",
            "source_hash": "old", "translation_status": "HUMAN_REVIEWED",
            "review_status": "HUMAN_REVIEWED", "updated_at": "2026-08-01",
        }
    }
    rows = build_product_dictionary({}, existing, updated_at="2026-08-25")
    assert rows[0]["sku"] == "900"
    assert rows[0]["name_zh_standard"] == "历史商品"
    assert rows[0]["updated_at"] == "2026-08-25"  # source_hash 迁移需要留下审计痕迹


def test_category_rebuild_keeps_manual_second_level_and_notes():
    old = [{
        "cat1_es": "Hogar", "cat2_es": "Limpieza", "cat1_code": "C08", "cat1_zh": "家务清洁",
        "cat2_zh": "清洁用品", "review_status": "HUMAN_REVIEWED", "notes": "人工确认",
    }]
    rows = category_rows_from_products([], existing=old)
    assert rows == old


def test_brand_reconciliation_adds_unreferenced_product_brand_as_reviewable_original_name():
    rows = reconcile_brand_rows(
        [{"brand_id": "Troppie"}],
        {"Known": {"brand_id": "Known", "canonical_name": "Known", "keep_original": "1"}},
    )
    by_id = {row["brand_id"]: row for row in rows}
    assert by_id["Known"]["canonical_name"] == "Known"
    assert by_id["Troppie"] == {
        "brand_id": "Troppie", "canonical_name": "Troppie", "aliases_es": "Troppie",
        "keep_original": "1", "is_action_brand": "0", "confidence": "PRODUCT_DICTIONARY_REFERENCE",
        "review_status": "NEEDS_HUMAN_REVIEW", "notes": "商品字典已引用；自动补齐以消除悬空品牌引用，待人工抽检。",
    }


def test_brand_reconciliation_reuses_existing_brand_alias_instead_of_adding_duplicate():
    rows = reconcile_brand_rows(
        [{"brand_id": "troppie"}],
        {"Troppie": {"brand_id": "Troppie", "canonical_name": "Troppie", "aliases_es": "Troppie"}},
    )
    assert [row["brand_id"] for row in rows] == ["Troppie"]


def test_product_override_is_field_level_not_whole_row_lock():
    overrides = index_product_overrides([{
        "scope": "product", "key": "1001", "field": "name_zh_standard", "value": "人工商品名",
    }])
    row = build_product_dictionary({"1001": {"name_es": "Producto", "cat1_es": "Hogar", "name_zh": "模型商品"}},
                                   category_mapping={normalize_category_key("Hogar"): {"cat1_zh": "家务清洁"}},
                                   product_overrides=overrides, updated_at="2026-08-25")[0]
    assert row["name_zh_standard"] == "人工商品名"
    assert row["cat1_zh"] == "家务清洁"
    assert row["review_status"] == "HUMAN_REVIEWED"


def test_product_override_lock_is_not_reset_after_application():
    overrides = index_product_overrides([{
        "scope": "product", "key": "1001", "field": "name_zh_standard", "value": "人工商品名",
    }, {
        "scope": "product", "key": "1001", "field": "locked", "value": "1",
    }])
    row = build_product_dictionary({"1001": {"name_es": "Producto"}}, product_overrides=overrides)[0]
    assert row["name_zh_standard"] == "人工商品名"
    assert row["locked"] == "1"
    assert row["translation_status"] == "LOCKED"


def test_field_override_does_not_freeze_unrelated_derived_fields_on_rebuild():
    overrides = index_product_overrides([{
        "scope": "product", "key": "1001", "field": "name_zh_standard", "value": "人工商品名",
    }, {
        "scope": "product", "key": "1001", "field": "locked", "value": "1",
    }])
    mapping = {
        normalize_category_key("Hogar"): {"cat1_zh": "家务清洁"},
        normalize_category_key("Moda"): {"cat1_zh": "服饰鞋包"},
    }
    first = build_product_dictionary(
        {"1001": {"name_es": "Producto", "cat1_es": "Hogar", "spec_zh": "旧规格"}},
        category_mapping=mapping, product_overrides=overrides, updated_at="2026-08-25",
    )[0]
    second = build_product_dictionary(
        {"1001": {"name_es": "Producto", "cat1_es": "Moda", "spec_zh": "新规格"}},
        {"1001": first}, category_mapping=mapping, product_overrides=overrides, updated_at="2026-08-26",
    )[0]
    assert second["name_zh_standard"] == "人工商品名"
    assert second["cat1_zh"] == "服饰鞋包"
    assert second["spec_zh_standard"] == "新规格"
    assert second["locked"] == "1"


def test_standardized_seed_overrides_only_derived_fields():
    row = build_product_dictionary(
        {"1001": {"name_es": "Spanish source", "name_zh": "旧中文", "spec_es": "2 unidades", "spec_zh": "2件"}},
        standardized_seed={"1001": {"name_zh": "标准中文名", "brand_id": "品牌A", "spec_zh": "2件装", "seed_status": "品名优化完成"}},
        updated_at="2026-08-25",
    )[0]
    assert row["name_es_raw"] == "Spanish source"
    assert row["name_zh_standard"] == "标准中文名"
    assert row["brand_id"] == "品牌A"
    assert row["spec_zh_standard"] == "2件装"
    assert row["translation_status"] == "MODEL_TRANSLATED"


def test_historical_reference_restores_only_polluted_spanish_fields():
    records = {"1001": {"name_es": "中文品名", "spec_es": "2 件", "cat1_es": "家居"}}
    recovered = restore_spanish_facts(records, {"1001": {
        "name_es": "Nombre español", "spec_es": "2 unidades", "cat1_es": "Hogar",
    }})
    assert recovered == 3
    assert records["1001"] == {"name_es": "Nombre español", "spec_es": "2 unidades", "cat1_es": "Hogar"}


def test_historical_reference_selects_clean_fields_independently(tmp_path):
    path = tmp_path / "reference.csv"
    path.write_text(
        "sku,date,name_es,spec_es,category_es,source_file\n"
        "1001,20260109,Nombre antiguo,中文规格,Hogar,old.xlsx\n"
        "1001,20260405,中文品名,2 unidades,中文类目,new.xlsx\n",
        encoding="utf-8",
    )
    reference = load_clean_historical_spanish_reference(path)
    assert reference["1001"]["name_es"] == "Nombre antiguo"
    assert reference["1001"]["spec_es"] == "2 unidades"
    assert reference["1001"]["cat1_es"] == "Hogar"


def test_model_translation_applies_only_when_source_hash_matches():
    base = build_product_dictionary({"1001": {"name_es": "Producto", "spec_es": "2 unidades"}}, updated_at="2026-08-25")[0]
    model = {"1001": {
        "source_hash": base["source_hash"], "name_zh_standard": "标准商品", "spec_zh_standard": "2件装",
        "quality_status": "OK",
    }}
    applied = build_product_dictionary({"1001": {"name_es": "Producto", "spec_es": "2 unidades"}},
                                       {"1001": base}, model_translations=model, updated_at="2026-08-25")[0]
    stale = build_product_dictionary({"1001": {"name_es": "Producto 新版", "spec_es": "2 unidades"}},
                                     {"1001": base}, model_translations=model, updated_at="2026-08-25")[0]
    assert applied["name_zh_standard"] == "标准商品"
    assert stale["name_zh_standard"] != "标准商品"


def test_source_change_marks_non_manual_translation_for_review():
    first = build_product_dictionary({"1001": {"name_es": "Producto A", "name_zh": "商品A"}}, updated_at="2026-08-24")[0]
    second = build_product_dictionary({"1001": {"name_es": "Producto B", "name_zh": "商品B"}}, {"1001": first}, updated_at="2026-08-25")[0]
    assert second["translation_status"] == "NEEDS_REVIEW"
    assert second["review_status"] == "NEEDS_REVIEW"


def test_incremental_candidate_selection_ignores_unchanged_old_sku():
    record = {"sku": "1001", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Limpieza", "spec_es": "2 unidades"}
    source_hash = product_source_hash({
        "name_es_raw": record["name_es"], "cat1_es": record["cat1_es"],
        "cat2_es": record["cat2_es"], "spec_es_raw": record["spec_es"],
    })
    assert select_candidates({"1001": record}, [], [{"sku": "1001", "source_hash": source_hash, "review_status": "UNREVIEWED"}]) == {}


def test_incremental_candidate_selection_does_not_treat_missing_listing_field_as_fact_change():
    record = {"sku": "1001", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "", "spec_es": ""}
    source_hash = product_source_hash({
        "name_es_raw": "Producto", "cat1_es": "Hogar", "cat2_es": "Limpieza", "spec_es_raw": "2 unidades",
    })
    product = {
        "sku": "1001", "source_hash": source_hash, "name_es_raw": "Producto", "cat1_es": "Hogar",
        "cat2_es": "Limpieza", "spec_es_raw": "2 unidades", "review_status": "UNREVIEWED",
    }
    assert select_candidates({"1001": record}, [], [product]) == {}


def test_incremental_candidate_selection_is_limited_to_new_changed_or_review():
    records = {
        "1001": {"sku": "1001", "name_es": "A", "cat1_es": "Hogar", "cat2_es": "", "spec_es": ""},
        "1002": {"sku": "1002", "name_es": "B", "cat1_es": "Hogar", "cat2_es": "", "spec_es": ""},
        "1003": {"sku": "1003", "name_es": "C", "cat1_es": "Hogar", "cat2_es": "", "spec_es": ""},
    }
    products = [
        {"sku": "1001", "source_hash": "old", "review_status": "UNREVIEWED", "translation_status": "MODEL_TRANSLATED"},
        {"sku": "1002", "source_hash": product_source_hash({"name_es_raw": "B", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": ""}), "review_status": "NEEDS_REVIEW", "translation_status": "NEEDS_REVIEW"},
    ]
    selected = select_candidates(records, [{"sku": "1003", "status": "NEW"}], products)
    assert selected == {"1001": {"SOURCE_HASH_CHANGED"}, "1002": {"NEEDS_REVIEW"}, "1003": {"NEW"}}


def test_incremental_new_existing_sku_is_audited_but_not_rewritten():
    record = {"sku": "1001", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "", "spec_es": ""}
    source_hash = product_source_hash({
        "name_es_raw": "Producto", "cat1_es": "Hogar", "cat2_es": "Limpieza", "spec_es_raw": "2 unidades",
    })
    existing = {"1001": {
        "sku": "1001", "source_hash": source_hash, "name_es_raw": "Producto", "cat1_es": "Hogar",
        "cat2_es": "Limpieza", "spec_es_raw": "2 unidades", "name_zh_standard": "已有中文",
    }}
    selected = {"1001": {"NEW"}}
    assert processable_candidate_skus(selected, existing, {"1001": record}, {}) == set()


def test_incremental_enrichment_rejects_dry_run_snapshot(tmp_path):
    (tmp_path / "run_report.json").write_text(
        '{"run_id":"r1","dry_run":true,"commit_status":"FULL_COMMIT"}', encoding="utf-8",
    )
    (tmp_path / "qa_report.json").write_text('{"passed":true,"state":"PASS"}', encoding="utf-8")
    with pytest.raises(DictionaryEnrichmentError, match="DRY_RUN_NOT_ALLOWED"):
        _validate_formal_snapshot(tmp_path, "r1")


def test_duplicate_csv_key_is_rejected(tmp_path):
    path = tmp_path / "product_dictionary.csv"
    path.write_text("sku,name_es_raw\n1001,A\n1001,B\n", encoding="utf-8")
    with pytest.raises(DictionaryValidationError, match="DUPLICATE_KEY"):
        load_dictionary_csv(path, key_field="sku")


def test_invalid_write_leaves_previous_file_untouched(tmp_path):
    path = tmp_path / "dictionary.csv"
    valid = [{"sku": "1"}]
    write_dictionary_csv(path, valid, ["sku"], key_fields=("sku",))
    original = path.read_bytes()
    with pytest.raises(DictionaryValidationError):
        write_dictionary_csv(path, [{"sku": "1"}, {"sku": "1"}], ["sku"], key_fields=("sku",))
    assert path.read_bytes() == original


def test_known_web_ui_copy_is_not_treated_as_product_spec():
    assert is_polluted_source_field("spec_es", "Añadir a tus favoritos")
    assert is_polluted_source_field("spec_es", "Todo de C&C")
    assert not is_polluted_source_field("name_es", "Añadir a tus favoritos")
    assert not is_polluted_source_field("spec_es", "24 unidades")


def test_polluted_spec_clears_old_spanish_and_derived_values():
    old = {
        "sku": "1001", "name_es_raw": "Producto", "spec_es_raw": "Añadir a tus favoritos",
        "spec_zh_standard": "旧规格", "source_hash": "old", "translation_status": "MODEL_TRANSLATED",
        "review_status": "UNREVIEWED",
    }
    row = build_product_dictionary(
        {"1001": {"sku": "1001", "name_es": "Producto", "spec_es": "", "_clear_spec_es": True}},
        {"1001": old}, updated_at="2026-08-25",
    )[0]
    assert row["spec_es_raw"] == ""
    assert row["spec_zh_standard"] == ""
    assert row["review_status"] == "NEEDS_REVIEW"
