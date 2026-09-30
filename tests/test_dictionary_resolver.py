from pathlib import Path

from action_tracker.dictionary import product_source_hash
from action_tracker.dictionary_resolver import resolve_record
from action_tracker.exporting.dictionary_join import DictionaryContext


def _context(*, product=None, manual=None, model=None, category=None, category_pairs=None, quality=None, brands=None):
    return DictionaryContext(
        directory=Path("."),
        product_by_sku=product or {}, manual_by_sku=manual or {}, model_by_sku=model or {},
        brand_by_id=brands or {"Action": {"brand_id": "Action", "canonical_name": "Action"}},
        category_by_pair=category_pairs or {}, category_by_cat1=category or {}, terms=(), damage_by_sku={},
        brand_reference_keys=frozenset({"action"}), unresolved_brand_ids=frozenset(),
        content_hash="test", source_quality_by_sku=quality or {},
    )


def _record():
    return {"sku": "1001", "name_es": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es": "2 unidades"}


def test_resolver_manual_override_has_field_level_priority():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    context = _context(
        product={"1001": {"sku": "1001", "name_zh_standard": "商品字典名", "spec_zh_standard": "2件装", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED", "cat1_zh": "家务清洁"}},
        manual={"1001": {"name_zh_standard": "人工名"}},
    )
    result = resolve_record(record, context)
    assert result.fields["name"].value == "人工名"
    assert result.fields["name"].source == "manual_override"
    assert result.readiness == "AUTO_READY"


def test_resolver_keeps_confirmed_brand_separate_from_title_and_preserves_manual_title():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"sku": "1001", "name_zh_standard": "记号笔", "spec_zh_standard": "2件装", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED", "cat1_zh": "家务清洁", "brand_id": "Stanger"}}
    brands = {"Stanger": {"brand_id": "Stanger", "canonical_name": "Stanger", "confidence": "REFERENCE"}}
    result = resolve_record(record, _context(product=product, brands=brands))
    assert result.brand_classification == "CONFIRMED"
    assert result.fields["name"].value == "记号笔"

    manual = resolve_record(record, _context(product=product, brands=brands, manual={"1001": {"name_zh_standard": "人工记号笔"}}))
    assert manual.fields["name"].value == "人工记号笔"


def test_resolver_matches_confirmed_brand_case_insensitively():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Alargador de enchufes Pro-Max", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "3 tomas"})
    product = {"1001": {"sku": "1001", "name_zh_standard": "延长插座线", "spec_zh_standard": "3孔", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED", "cat1_zh": "家务清洁", "brand_id": "Pro-Max"}}
    brands = {"Pro-max": {"brand_id": "Pro-max", "canonical_name": "Pro-max", "confidence": "REFERENCE", "review_status": "HUMAN_REVIEWED"}}
    result = resolve_record({**record, "name_es": "Alargador de enchufes Pro-Max", "spec_es": "3 tomas"}, _context(product=product, brands=brands))
    assert result.brand_classification == "CONFIRMED"
    assert result.fields["name"].value == "延长插座线"
    assert result.fields["brand"].status == "READY"


def test_resolver_rejects_stale_model_and_marks_hash_change():
    record = _record()
    context = _context(model={"1001": {"name_zh_standard": "过期名", "source_hash": "old", "quality_status": "OK"}})
    result = resolve_record(record, context)
    assert result.fields["name"].source == "fallback"
    assert result.source_hash_status == "MISMATCH"
    assert result.readiness == "REVIEW_REQUIRED"
    assert "SOURCE_HASH_CHANGED" in result.review_reasons


def test_hash_matched_model_candidate_with_quality_ok_still_requires_owner_review():
    record = _record()
    source_hash = product_source_hash({
        "name_es_raw": record["name_es"], "cat1_es": record["cat1_es"],
        "cat2_es": record["cat2_es"], "spec_es_raw": record["spec_es"],
    })
    result = resolve_record(record, _context(model={"1001": {
        "name_zh_standard": "模型商品名", "spec_zh_standard": "模型规格",
        "source_hash": source_hash, "quality_status": "OK",
    }}))

    assert result.fields["name"].value == "模型商品名"
    assert result.fields["name"].source == "model_cache"
    assert result.fields["name"].status == "REVIEW"
    assert result.fields["name"].approval_status == "PENDING"
    assert result.fields["spec"].status == "REVIEW"
    assert result.readiness == "REVIEW_REQUIRED"
    assert "NAME_REVIEW" in result.review_reasons
    assert "SPEC_REVIEW" in result.review_reasons


def test_model_translated_product_row_must_have_explicit_review_approval():
    record = _record()
    source_hash = product_source_hash({
        "name_es_raw": record["name_es"], "cat1_es": record["cat1_es"],
        "cat2_es": record["cat2_es"], "spec_es_raw": record["spec_es"],
    })
    candidate = {
        "name_zh_standard": "模型商品名", "spec_zh_standard": "模型规格",
        "source_hash": source_hash, "translation_status": "MODEL_TRANSLATED",
        "review_status": "UNREVIEWED", "cat1_zh": "家务清洁",
    }

    result = resolve_record(record, _context(product={"1001": candidate}))

    assert result.fields["name"].value == "模型商品名"
    assert result.fields["name"].status == "REVIEW"
    assert result.fields["spec"].status == "REVIEW"
    assert result.fields["cat1"].value == "家务清洁"
    assert result.fields["cat1"].status == "REVIEW"
    assert result.readiness == "REVIEW_REQUIRED"


def test_unapproved_category_mapping_is_not_ready():
    record = {**_record(), "cat2_es": "Decoración"}
    result = resolve_record(record, _context(category_pairs={
        ("hogar", "decoracion"): {"cat2_zh": "家居装饰", "review_status": "UNREVIEWED"},
    }))

    assert result.fields["cat2"].value == "Decoración"
    assert result.fields["cat2"].status == "FALLBACK"
    assert result.readiness == "REVIEW_REQUIRED"
    assert "CATEGORY_REVIEW" in result.review_reasons


def test_resolver_source_damage_blocks_without_back_translation():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record(record, _context(product=product, quality={"1001": "SOURCE_POLLUTED"}))
    assert result.readiness == "SOURCE_BLOCKED"
    assert "SOURCE_POLLUTED" in result.review_reasons


def test_resolver_damaged_spec_does_not_fallback_to_ui_text():
    record = _record()
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "", "cat1_zh": "家务清洁", "source_hash": "stale", "translation_status": "NEEDS_REVIEW"}}
    context = DictionaryContext(
        directory=Path("."), product_by_sku=product, manual_by_sku={}, model_by_sku={},
        brand_by_id={"Action": {"brand_id": "Action", "canonical_name": "Action"}},
        category_by_pair={}, category_by_cat1={"hogar": {"cat1_zh": "家务清洁", "review_status": "CAT1_CONFIRMED"}}, terms=(),
        damage_by_sku={"1001": {"spec_es_raw"}}, brand_reference_keys=frozenset({"action"}),
        unresolved_brand_ids=frozenset(), content_hash="test", source_quality_by_sku={"1001": "SOURCE_POLLUTED"},
    )
    result = resolve_record({**record, "spec_es": "Añadir a tus favoritos"}, context)
    assert result.fields["spec"].value == ""
    assert result.fields["spec"].source == "source_damage"


def test_resolver_marks_unmapped_category_fallback_for_review():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "Ropa", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record({**record, "cat2_es": "Ropa"}, _context(product=product))
    assert result.fields["cat2"].status == "FALLBACK"
    assert "CATEGORY_REVIEW" in result.review_reasons


def test_resolver_replaces_spanish_product_category_with_dictionary_value():
    record = {**_record(), "cat2_es": "Decoración"}
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "Decoración", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "cat2_zh": "Decoración", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    context = _context(product=product, category_pairs={("hogar", "decoracion"): {"cat2_zh": "家居装饰", "review_status": "HUMAN_REVIEWED"}})
    result = resolve_record(record, context)
    assert result.fields["cat2"].value == "家居装饰"
    assert result.fields["cat2"].source == "category_dictionary"
    assert "CATEGORY_REVIEW" not in result.review_reasons


def test_resolver_detects_plain_spanish_residual_in_confirmed_value():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "Caja de plástico", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record(record, _context(product=product))
    assert "SPANISH_RESIDUAL" in result.review_reasons
    assert result.readiness == "REVIEW_REQUIRED"


def test_provisional_brand_is_explicit_and_policy_controls_promotion():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED", "brand_id": "NewBrand"}}
    provisional = {"NewBrand": {"brand_id": "NewBrand", "canonical_name": "NewBrand", "confidence": "PRODUCT_DICTIONARY_REFERENCE", "review_status": "NEEDS_HUMAN_REVIEW"}}
    context = DictionaryContext(
        directory=Path("."), product_by_sku=product, manual_by_sku={}, model_by_sku={}, brand_by_id=provisional,
        category_by_pair={}, category_by_cat1={"hogar": {"cat1_zh": "家务清洁", "review_status": "CAT1_CONFIRMED"}}, terms=(), damage_by_sku={},
        brand_reference_keys=frozenset({"newbrand"}), unresolved_brand_ids=frozenset(), content_hash="test",
        source_quality_by_sku={}, allow_provisional_brands=True,
    )
    result = resolve_record(record, context)
    assert result.brand_classification == "PROVISIONAL"
    assert result.fields["brand"].status == "READY"
    strict = context.__class__(**{**context.__dict__, "allow_provisional_brands": False})
    strict_result = resolve_record(record, strict)
    assert strict_result.fields["brand"].status == "REVIEW"
    assert "BRAND_CANDIDATE" in strict_result.review_reasons


def test_unknown_brand_cannot_be_auto_ready():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED", "brand_id": "UnknownBrand"}}
    result = resolve_record(record, _context(product=product))
    assert result.brand_classification == "UNKNOWN"
    assert result.fields["brand"].status == "REVIEW"
    assert result.readiness == "REVIEW_REQUIRED"


def test_unknown_source_quality_fails_closed_as_source_blocked():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record(record, _context(product=product, quality={"1001": "UNRECOGNISED"}))
    assert result.readiness == "SOURCE_BLOCKED"
    assert "SOURCE_UNTRUSTED" in result.review_reasons
