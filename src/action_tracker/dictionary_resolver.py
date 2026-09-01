"""确定性的字典解析器：返回字段来源、状态和 SKU 级 readiness。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .dictionary import format_confirmed_brand_title, is_confirmed_brand_record
from .exporting.dictionary_join import (
    DictionaryContext,
    _fact_source_hash,
    _normalize_unit_price,
    _resolve_category_field,
    _resolve_existing_chinese_field,
    _resolve_product_field,
)


FIXED_CAT1 = frozenset({
    "DIY五金", "办公文具", "宠物用品", "厨房餐具", "服饰鞋包", "个人美容", "家居布置",
    "家务清洁", "旅行用品", "食品饮料", "数码影音", "玩具", "兴趣手作", "园艺户外", "运动用品",
})
FIELD_LABELS = {
    "name": "中文品名", "cat1": "中文分类1", "cat2": "中文分类2", "spec": "中文规格",
    "unit_price": "中文单价", "description": "中文描述", "details": "中文产品详情", "brand": "品牌",
}
_SPANISH_TOKEN_RE = re.compile(r"\b(?:de|del|la|el|los|las|para|con|sin|una|uno|un|y|en|por|más|color|colores|tamaño|unidades|piezas|pack|set)\b", re.IGNORECASE)
_SPANISH_ACCENT_RE = re.compile(r"[áéíóúüñÁÉÍÓÚÜÑ]")


def contains_unallowed_spanish(value: object) -> bool:
    """检测中文派生字段中的普通西语残留；品牌/型号等纯拉丁保留不误报。

    只有带西语重音字母，或命中明确西语功能词且没有中文字符时才判定残留。
    这是一条保守质量门，不负责翻译，也不把未知大写词自动当品牌。
    """
    text = str(value or "").strip()
    if not text or re.search(r"[\u3400-\u9fff]", text):
        return False
    return bool(_SPANISH_ACCENT_RE.search(text) or _SPANISH_TOKEN_RE.search(text))


@dataclass(frozen=True)
class FieldResolution:
    value: str
    source: str
    status: str


@dataclass(frozen=True)
class RecordResolution:
    sku: str
    fields: dict[str, FieldResolution]
    source_hash_status: str
    source_quality_status: str
    readiness: str
    review_reasons: tuple[str, ...]
    brand_classification: str = "NONE"


def resolve_record(record: dict[str, Any], context: DictionaryContext) -> RecordResolution:
    # Once the versioned Localization Intelligence dictionaries are available,
    # production callers use the single semantic Planner/Validator path.  The
    # Production dictionaries use the V1 semantic planner as soon as the
    # versioned files are present.  The compact implementation below remains
    # a compatibility adapter for pre-V1 external dictionaries; it is not
    # used by the V1 audit or PRIMARY export paths.
    if all((context.directory / filename).exists() for filename in (
        "product_type_dictionary.csv", "detail_key_dictionary.csv",
        "tech_token_dictionary.csv", "phrase_dictionary.csv",
    )):
        return _resolve_record_v1(record, context)
    sku = str(record.get("sku") or "").strip()
    product = context.product_by_sku.get(sku, {})
    manual = context.manual_by_sku.get(sku, {})
    source_hash = _fact_source_hash(record)
    model = context.model_by_sku.get(sku, {})
    fields: dict[str, FieldResolution] = {}

    fields["name"] = _product_field("name_zh_standard", record, product, manual, model, context, source_hash, "name_es")
    fields["spec"] = _product_field("spec_zh_standard", record, product, manual, model, context, source_hash, "spec_es")
    fields["cat1"] = _category_field("cat1_zh", record, product, manual, context, source_hash)
    fields["cat2"] = _category_field("cat2_zh", record, product, manual, context, source_hash)
    unit, unit_fallback = _normalize_unit_price(str(record.get("unit_price") or "").strip(), context.terms)
    fields["unit_price"] = FieldResolution(unit, "term_dictionary" if unit and not unit_fallback else "fallback", "READY" if unit and not unit_fallback else ("FALLBACK" if unit else "MISSING"))
    for key, zh_field, es_field in (("description", "desc_zh", "desc_es"), ("details", "details_zh", "details_es")):
        value, fallback = _resolve_existing_chinese_field(record, zh_field, es_field, FIELD_LABELS[key], context.damage_by_sku.get(sku, set()), es_field)
        fields[key] = FieldResolution(str(value or ""), "master_zh" if value and not fallback else ("fallback" if value else "missing"), "READY" if value and not fallback else ("FALLBACK" if value else "MISSING"))

    brand_id = str(product.get("brand_id") or "").strip()
    brand_row = context.brand_by_id.get(brand_id, {})
    brand_value = str(brand_row.get("canonical_name") or brand_id).strip()
    brand_classification = "NONE"
    if not brand_id:
        # The source does not provide a brand for many Action private-label or
        # generic items.  That is not an unknown-brand defect; only a
        # non-empty, unrecognised brand identifier needs review.
        fields["brand"] = FieldResolution("", "none", "READY")
    elif brand_id not in context.brand_by_id:
        brand_classification = "UNKNOWN"
        fields["brand"] = FieldResolution(brand_value, "brand_dictionary", "REVIEW")
    else:
        if is_confirmed_brand_record(brand_row):
            brand_classification = "CONFIRMED"
            fields["brand"] = FieldResolution(brand_value, "brand_dictionary", "READY")
        else:
            brand_classification = "PROVISIONAL"
            status = "READY" if context.allow_provisional_brands else "REVIEW"
            source = "brand_dictionary_provisional" if status == "READY" else "brand_dictionary"
            fields["brand"] = FieldResolution(brand_value, source, status)

    # Chinese display titles may add the brand marker only after the brand is
    # confirmed.  Manual title overrides remain field-level authority and are
    # never reformatted automatically.
    if (
        brand_classification == "CONFIRMED"
        and fields["name"].status == "READY"
        and fields["name"].source != "manual_override"
    ):
        name = fields["name"]
        fields["name"] = FieldResolution(
            format_confirmed_brand_title(name.value, brand_value), name.source, name.status,
        )

    raw_source_quality = context.source_quality_by_sku.get(sku, "") or "OK"
    source_quality = raw_source_quality if raw_source_quality in {"OK", "SOURCE_DAMAGED", "SOURCE_POLLUTED"} else "SOURCE_UNTRUSTED"
    source_hash_status = "MATCH" if str(product.get("source_hash") or "") == source_hash and source_hash else "MISMATCH"
    reasons: list[str] = []
    if source_hash_status != "MATCH":
        reasons.append("SOURCE_HASH_CHANGED")
    if source_quality in {"SOURCE_DAMAGED", "SOURCE_POLLUTED", "SOURCE_UNTRUSTED"}:
        reasons.append(source_quality)
    if str(product.get("review_status") or "").strip() == "NEEDS_REVIEW" or str(product.get("translation_status") or "").strip() == "NEEDS_REVIEW":
        reasons.append("NEEDS_REVIEW")
    if str(product.get("translation_status") or "").strip() in {"", "UNTRANSLATED", "LEGACY_UNVERIFIED"} and not manual.get("name_zh_standard"):
        reasons.append("UNCONFIRMED_PRODUCT_DICTIONARY")
    cat1 = fields["cat1"].value
    if fields["cat1"].status == "MISSING" or cat1 not in FIXED_CAT1:
        reasons.append("CATEGORY_REVIEW")
    if fields["name"].status in {"FALLBACK", "MISSING"}:
        reasons.append("NAME_REVIEW")
    if fields["spec"].status in {"FALLBACK", "MISSING"}:
        reasons.append("SPEC_REVIEW")
    if fields["brand"].status in {"MISSING", "REVIEW"}:
        reasons.append("BRAND_CANDIDATE")
    if fields["unit_price"].status == "FALLBACK":
        reasons.append("TERM_REVIEW")
    # 描述/详情目前允许保留官网西语 fallback，并以字段状态标记待补；
    # readiness 只在关键中文派生字段（品名、规格）出现普通西语残留时阻断。
    if any(contains_unallowed_spanish(fields[key].value) for key in ("name", "spec")):
        reasons.append("SPANISH_RESIDUAL")
    if source_quality in {"SOURCE_DAMAGED", "SOURCE_POLLUTED", "SOURCE_UNTRUSTED"}:
        readiness = "SOURCE_BLOCKED"
    else:
        mandatory_ok = (
            source_hash_status == "MATCH"
            and fields["name"].status == "READY"
            and fields["cat1"].status == "READY"
            and cat1 in FIXED_CAT1
            and fields["spec"].status in {"READY", "MISSING"}
            and fields["cat2"].status in {"READY", "MISSING"}
            and fields["brand"].status == "READY"
            and not any(reason in reasons for reason in ("NEEDS_REVIEW", "UNCONFIRMED_PRODUCT_DICTIONARY", "SPANISH_RESIDUAL"))
        )
        readiness = "AUTO_READY" if mandatory_ok else "REVIEW_REQUIRED"
    return RecordResolution(sku, fields, source_hash_status, source_quality, readiness, tuple(dict.fromkeys(reasons)), brand_classification)


def _resolve_record_v1(record: dict[str, Any], context: DictionaryContext) -> RecordResolution:
    """Adapt the V1 LocalizationPlan to the frozen legacy API shape."""
    from .localization.engine import LocalizationEngine
    from .localization.knowledge import KnowledgeLoader

    knowledge = dict(KnowledgeLoader(context.directory).load())
    product = context.product_by_sku.get(str(record.get("sku") or "").strip(), {})
    manual = context.manual_by_sku.get(str(record.get("sku") or "").strip(), {})
    knowledge.update({
        "name_zh": manual.get("name_zh_standard") or product.get("name_zh_standard") or "",
        "spec_zh": manual.get("spec_zh_standard") or product.get("spec_zh_standard") or "",
        "cat1_zh": manual.get("cat1_zh") or product.get("cat1_zh") or "",
        "cat2_zh": manual.get("cat2_zh") or product.get("cat2_zh") or "",
        "cat1_map": {key[0]: value.get("cat1_zh", "") for key, value in context.category_by_pair.items()},
        "cat2_map": {key[1]: value.get("cat2_zh", "") for key, value in context.category_by_pair.items() if key[1]},
    })
    engine = LocalizationEngine(knowledge=knowledge)
    existing = {"source_hash": product.get("source_hash") or ""}
    for field, legacy in (("name_zh", "name"), ("spec_zh", "spec"), ("cat1_zh", "cat1"), ("cat2_zh", "cat2")):
        existing[field] = manual.get(field.replace("_zh", "_zh_standard")) or product.get(field.replace("_zh", "_zh_standard")) or ""
    plan = engine.resolve(record, existing=existing if existing["source_hash"] else None)
    validation = engine.validate(record, plan)
    fields: dict[str, FieldResolution] = {}
    from .localization.contracts import ZH_TO_CANONICAL
    for key, field in plan.fields.items():
        legacy = ZH_TO_CANONICAL[key]
        status = "READY" if field.status == "READY" else ("MISSING" if not field.value else ("FALLBACK" if field.status == "FALLBACK" else "REVIEW"))
        fields[legacy] = FieldResolution(field.value, field.source, status)
    brands = [fact.value for fact in plan.semantic_facts if fact.semantic_type == "BRAND"]
    if brands:
        fields["brand"] = FieldResolution(brands[0], "brand_dictionary", "READY")
        brand_classification = "CONFIRMED"
    else:
        fields["brand"] = FieldResolution("", "none", "READY")
        brand_classification = "NONE"
    quality = context.source_quality_by_sku.get(str(record.get("sku") or ""), "") or "OK"
    source_quality = quality if quality in {"OK", "SOURCE_DAMAGED", "SOURCE_POLLUTED"} else "SOURCE_UNTRUSTED"
    reasons = list(validation.reasons)
    if source_quality != "OK": reasons.append(source_quality)
    readiness = "SOURCE_BLOCKED" if source_quality != "OK" else ("AUTO_READY" if validation.ok else "REVIEW_REQUIRED")
    return RecordResolution(str(record.get("sku") or "").strip(), fields,
                            "MATCH" if product.get("source_hash") == plan.source_hash and product.get("source_hash") else "MISMATCH",
                            source_quality, readiness, tuple(dict.fromkeys(reasons)), brand_classification)


def resolve_localization(record: dict[str, Any], context: DictionaryContext):
    """Compatibility entry point backed by the V1 Localization Engine.

    Existing callers can continue using ``resolve_record`` during migration;
    new export/audit code should call this function so there is one semantic
    planner and one token-level validator.
    """
    from .localization.engine import LocalizationEngine
    from .localization.knowledge import KnowledgeLoader
    knowledge = KnowledgeLoader(context.directory).load()
    product = context.product_by_sku.get(str(record.get("sku") or ""), {})
    knowledge.update({
        "cat1_map": {k[0]: v.get("cat1_zh", "") for k, v in context.category_by_pair.items()},
        "cat2_map": {k[1]: v.get("cat2_zh", "") for k, v in context.category_by_pair.items() if k[1]},
        "name_zh": product.get("name_zh_standard", ""), "spec_zh": product.get("spec_zh_standard", ""),
    })
    engine = LocalizationEngine(knowledge=knowledge)
    plan = engine.resolve(record)
    return plan, engine.validate(record, plan)


def _product_field(field: str, record: dict[str, Any], product: dict[str, str], manual: dict[str, str], model: dict[str, str], context: DictionaryContext, source_hash: str, fallback_field: str) -> FieldResolution:
    manual_value = str(manual.get(field) or "").strip()
    if manual_value:
        return FieldResolution(manual_value, "manual_override", "READY")
    product_value = str(product.get(field) or "").strip()
    confirmed = str(product.get("translation_status") or "").strip() not in {"", "UNTRANSLATED", "NEEDS_REVIEW", "LEGACY_UNVERIFIED"}
    if product_value and str(product.get("source_hash") or "") == source_hash and confirmed:
        return FieldResolution(product_value, "product_dictionary", "READY")
    model_value = str(model.get(field) or "").strip()
    if model_value and str(model.get("source_hash") or "") == source_hash and str(model.get("quality_status") or "").upper() == "OK":
        return FieldResolution(model_value, "model_cache", "READY")
    fallback = str(record.get(fallback_field) or "").strip()
    return FieldResolution(fallback, "fallback", "FALLBACK" if fallback else "MISSING")


def _category_field(field: str, record: dict[str, Any], product: dict[str, str], manual: dict[str, str], context: DictionaryContext, source_hash: str) -> FieldResolution:
    manual_value = str(manual.get(field) or "").strip()
    if manual_value:
        return FieldResolution(manual_value, "manual_override", "READY")
    product_value = str(product.get(field) or "").strip()
    if product_value and str(product.get("source_hash") or "") == source_hash:
        return FieldResolution(product_value, "product_dictionary", "READY")
    value, fallback = _resolve_category_field(field, record, product, manual, context, source_hash)
    value = str(value or "").strip()
    return FieldResolution(value, "category_dictionary" if value and not fallback else "fallback", "READY" if value and not fallback else ("FALLBACK" if value else "MISSING"))
