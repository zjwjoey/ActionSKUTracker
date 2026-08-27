from pathlib import Path

from action_tracker.dictionary import product_source_hash
from action_tracker.dictionary_resolver import resolve_record
from action_tracker.exporting.dictionary_join import DictionaryContext


def _context(*, product=None, manual=None, model=None, category=None, quality=None):
    return DictionaryContext(
        directory=Path("."),
        product_by_sku=product or {}, manual_by_sku=manual or {}, model_by_sku=model or {},
        brand_by_id={"Action": {"brand_id": "Action", "canonical_name": "Action"}},
        category_by_pair={}, category_by_cat1=category or {}, terms=(), damage_by_sku={},
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


def test_resolver_rejects_stale_model_and_marks_hash_change():
    record = _record()
    context = _context(model={"1001": {"name_zh_standard": "过期名", "source_hash": "old", "quality_status": "OK"}})
    result = resolve_record(record, context)
    assert result.fields["name"].source == "fallback"
    assert result.source_hash_status == "MISMATCH"
    assert result.readiness == "REVIEW_REQUIRED"
    assert "SOURCE_HASH_CHANGED" in result.review_reasons


def test_resolver_source_damage_blocks_without_back_translation():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "盒子", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record(record, _context(product=product, quality={"1001": "SOURCE_POLLUTED"}))
    assert result.readiness == "SOURCE_BLOCKED"
    assert "SOURCE_POLLUTED" in result.review_reasons


def test_resolver_detects_plain_spanish_residual_in_confirmed_value():
    record = _record()
    source_hash = product_source_hash({"name_es_raw": "Caja", "cat1_es": "Hogar", "cat2_es": "", "spec_es_raw": "2 unidades"})
    product = {"1001": {"name_zh_standard": "Caja de plástico", "spec_zh_standard": "2件", "cat1_zh": "家务清洁", "source_hash": source_hash, "translation_status": "HUMAN_REVIEWED"}}
    result = resolve_record(record, _context(product=product))
    assert "SPANISH_RESIDUAL" in result.review_reasons
    assert result.readiness == "REVIEW_REQUIRED"
