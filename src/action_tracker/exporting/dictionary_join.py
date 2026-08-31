"""导出用的只读中文字典 Join；按字段执行，不会重建或写回字典。"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..dictionary import (
    BRAND_DICTIONARY_HEADERS,
    CATEGORY_DICTIONARY_HEADERS,
    MODEL_TRANSLATION_HEADERS,
    OVERRIDE_HEADERS,
    PRODUCT_DICTIONARY_HEADERS,
    SOURCE_DAMAGE_HEADERS,
    TERM_DICTIONARY_HEADERS,
    format_confirmed_brand_title,
    index_model_translations,
    index_product_overrides,
    is_confirmed_brand_record,
    load_dictionary_rows,
    normalize_category_key,
)
from ..services.normalization import parse_bool_zh, parse_price


class DictionaryJoinError(ValueError):
    """正式字典不可用或字段来源不符合契约。"""


_UNUSABLE_TRANSLATION_STATUSES = {"", "UNTRANSLATED", "NEEDS_REVIEW"}
_LATIN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class DictionaryContext:
    directory: Path
    product_by_sku: dict[str, dict[str, str]]
    manual_by_sku: dict[str, dict[str, str]]
    model_by_sku: dict[str, dict[str, str]]
    brand_by_id: dict[str, dict[str, str]]
    category_by_pair: dict[tuple[str, str], dict[str, str]]
    category_by_cat1: dict[str, dict[str, str]]
    terms: tuple[dict[str, str], ...]
    damage_by_sku: dict[str, set[str]]
    brand_reference_keys: frozenset[str]
    unresolved_brand_ids: frozenset[str]
    content_hash: str
    source_quality_by_sku: dict[str, str] = field(default_factory=dict)
    allow_provisional_brands: bool = True


def load_dictionary_context(cfg: dict[str, Any]) -> DictionaryContext:
    """优先读取通过审计的运行时字典；不可用时只读 Git 基线。"""
    runtime = Path(cfg["paths"]["dictionary"])
    baseline = Path(cfg["paths"]["dictionary_baseline"])
    directory = _select_dictionary_directory(runtime, baseline)
    products = load_dictionary_rows(
        directory / "product_dictionary.csv", headers=PRODUCT_DICTIONARY_HEADERS, key_fields=("sku",),
    )
    brands = load_dictionary_rows(
        directory / "brand_dictionary.csv", headers=BRAND_DICTIONARY_HEADERS, key_fields=("brand_id",),
    )
    categories = load_dictionary_rows(
        directory / "category_dictionary.csv", headers=CATEGORY_DICTIONARY_HEADERS, key_fields=("cat1_es", "cat2_es"),
    )
    terms = load_dictionary_rows(
        directory / "term_dictionary.csv", headers=TERM_DICTIONARY_HEADERS, key_fields=("term_es",),
    )
    overrides = load_dictionary_rows(
        directory / "manual_overrides.csv", headers=OVERRIDE_HEADERS, key_fields=("scope", "key", "field"),
    )
    models = load_dictionary_rows(
        directory / "model_translation_overrides.csv", headers=MODEL_TRANSLATION_HEADERS, key_fields=("sku",),
    )
    damage = load_dictionary_rows(
        directory / "source_damage_report.csv", headers=SOURCE_DAMAGE_HEADERS, key_fields=("sku",),
    )
    product_by_sku = {row["sku"]: row for row in products}
    brand_by_id = {row["brand_id"]: row for row in brands}
    brand_reference_keys = frozenset(_brand_reference_keys(brands))
    category_by_pair = {
        (normalize_category_key(row["cat1_es"]), normalize_category_key(row["cat2_es"])): row
        for row in categories
    }
    category_by_cat1 = {
        normalize_category_key(row["cat1_es"]): row
        for row in categories if not _text(row.get("cat2_es"))
    }
    term_rows = tuple(sorted(terms, key=lambda row: len(row["term_es"]), reverse=True))
    return DictionaryContext(
        directory=directory,
        product_by_sku=product_by_sku,
        manual_by_sku=index_product_overrides(overrides),
        model_by_sku=index_model_translations(models),
        brand_by_id=brand_by_id,
        category_by_pair=category_by_pair,
        category_by_cat1=category_by_cat1,
        terms=term_rows,
        damage_by_sku={
            row["sku"]: {piece.strip() for piece in row["damaged_fields"].split(",") if piece.strip()}
            for row in damage
        },
        brand_reference_keys=brand_reference_keys,
        unresolved_brand_ids=frozenset(
            row["brand_id"] for row in product_by_sku.values()
            if _text(row.get("brand_id")) and _normalized_brand_key(row["brand_id"]) not in brand_reference_keys
        ),
        content_hash=_dictionary_content_hash(directory),
        source_quality_by_sku={row["sku"]: _text(row.get("status")) for row in damage},
        allow_provisional_brands=_strict_bool_config(
            (cfg.get("dictionary_apply") or {}).get("allow_provisional_brands", True),
            name="dictionary_apply.allow_provisional_brands",
        ),
    )


def build_zh_rows(records: Iterable[dict[str, Any]], context: DictionaryContext) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按冻结优先级生成中文导出行，并返回逐字段 fallback 统计。"""
    rows: list[dict[str, Any]] = []
    fallback_counts: dict[str, int] = {}
    for record in sorted(records, key=_sku_sort_key):
        sku = _text(record.get("sku"))
        product = context.product_by_sku.get(sku, {})
        manual = context.manual_by_sku.get(sku, {})
        source_hash = _fact_source_hash(record)
        fallbacks: list[str] = []

        title, used_fallback = _resolve_product_field(
            "name_zh_standard", record, product, manual, context, source_hash, _text(record.get("name_es")),
        )
        if used_fallback:
            fallbacks.append("中文品名待审核")
        brand_id = _text(product.get("brand_id"))
        brand_row = lookup_brand_row(context.brand_by_id, brand_id)
        if (
            not used_fallback
            and not _text(manual.get("name_zh_standard"))
            and brand_row
            and is_confirmed_brand_record(brand_row)
        ):
            title = format_confirmed_brand_title(
                title, _text(brand_row.get("canonical_name")) or brand_id,
            )
        cat1, cat1_fallback = _resolve_category_field("cat1_zh", record, product, manual, context, source_hash)
        if cat1_fallback:
            fallbacks.append("中文分类1待审核")
        cat2, cat2_fallback = _resolve_category_field("cat2_zh", record, product, manual, context, source_hash)
        if cat2_fallback:
            fallbacks.append("中文分类2待审核")
        spec, spec_fallback = _resolve_product_field(
            "spec_zh_standard", record, product, manual, context, source_hash, _text(record.get("spec_es")),
        )
        if spec_fallback:
            fallbacks.append("中文规格待审核")
        unit_price, unit_fallback = _normalize_unit_price(_text(record.get("unit_price")), context.terms)
        if unit_fallback:
            fallbacks.append("中文单价待审核")
        description, description_fallback = _resolve_existing_chinese_field(
            record, "desc_zh", "desc_es", "中文描述", context.damage_by_sku.get(sku, set()), "desc_es",
        )
        if description_fallback:
            fallbacks.append("中文描述待审核")
        details, details_fallback = _resolve_existing_chinese_field(
            record, "details_zh", "details_es", "中文产品详情", context.damage_by_sku.get(sku, set()), "details_es",
        )
        if details_fallback:
            fallbacks.append("中文产品详情待审核")

        for item in fallbacks:
            fallback_counts[item] = fallback_counts.get(item, 0) + 1
        rows.append({
            "图片": None,
            "编号": sku,
            "标题": title,
            "分类1": cat1,
            "分类2": cat2,
            "规格": spec,
            "折后价": _required_price(record.get("current_price"), sku=sku),
            "原价": _display_original_price(record, sku=sku),
            "单价": unit_price,
            "描述": description,
            "产品详情": details,
            "图片链接": _none_or_text(record.get("image_url")),
            "商品链接": _text(record.get("product_url")),
            "备注": _zh_remarks(record, fallbacks),
        })
    return rows, fallback_counts


def build_zh_rows_from_localized_source(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build Chinese rows from SQLite ``product_localizations`` values.

    This is the PRIMARY export path: localization values have already passed
    the dictionary/apply gate and are therefore not re-joined against the
    file-based dictionary.  Missing derived fields remain visible as review
    fallbacks rather than dropping the SKU.
    """
    from ..localization.engine import LocalizationEngine
    engine = LocalizationEngine()
    rows: list[dict[str, Any]] = []
    fallback_counts: dict[str, int] = {}
    for record in sorted(records, key=_sku_sort_key):
        sku = _text(record.get("sku"))
        fallbacks: list[str] = []
        plan = engine.primary_export_plan(record)
        resolved: dict[str, Any] = {key: _none_or_text(field.value) for key, field in plan.fields.items()}
        labels = {"name_zh": "中文品名", "cat1_zh": "中文分类1", "cat2_zh": "中文分类2", "spec_zh": "中文规格", "unit_price_zh": "中文单价", "desc_zh": "中文描述", "details_zh": "中文产品详情"}
        for key, field in plan.fields.items():
            if field.status != "READY":
                fallbacks.append(labels[key] + "待审核")
        for item in fallbacks:
            fallback_counts[item] = fallback_counts.get(item, 0) + 1
        rows.append({
            "图片": None, "编号": sku, "标题": resolved["name_zh"], "分类1": resolved["cat1_zh"],
            "分类2": resolved["cat2_zh"], "规格": resolved["spec_zh"],
            "折后价": _required_price(record.get("current_price"), sku=sku),
            "原价": _display_original_price(record, sku=sku), "单价": resolved["unit_price_zh"],
            "描述": resolved["desc_zh"], "产品详情": resolved["details_zh"],
            "图片链接": _none_or_text(record.get("image_url")), "商品链接": _text(record.get("product_url")),
            "备注": _zh_remarks(record, fallbacks),
        })
    return rows, fallback_counts


def _select_dictionary_directory(runtime: Path, baseline: Path) -> Path:
    required = {
        "product_dictionary.csv", "brand_dictionary.csv", "category_dictionary.csv", "term_dictionary.csv",
        "manual_overrides.csv", "model_translation_overrides.csv", "source_damage_report.csv",
    }
    for directory in (runtime, baseline):
        if all((directory / filename).exists() for filename in required):
            if directory == runtime:
                # Runtime files are provisional.  If the audit is missing,
                # failed, or no longer matches the published file hashes,
                # fall back to the immutable baseline instead of exporting
                # an unaudited dictionary.
                if not _runtime_dictionary_is_usable(directory, baseline, required):
                    continue
            return directory
    raise DictionaryJoinError("FORMAL_DICTIONARY_MISSING")


def _runtime_dictionary_is_usable(directory: Path, baseline: Path, required: set[str]) -> bool:
    audit_files = sorted(directory.glob("audit_report_*.json"), reverse=True)
    if not audit_files:
        return False
    try:
        audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(audit, dict):
        return False
    try:
        fail_count = int((audit.get("summary") or {}).get("fail") or 0)
    except (AttributeError, TypeError, ValueError):
        return False
    if fail_count != 0:
        return False
    # The published manifest is the binding file-version evidence.  The
    # audit script may classify this check as a warning, but Export must not
    # silently consume files changed after that audit.
    manifest_path = baseline / "baseline_manifest.json"
    if not manifest_path.exists():
        return True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest.get("files") or {}
    except (OSError, json.JSONDecodeError):
        return False
    if set(entries) != required:
        return False
    for filename in required:
        expected = str((entries.get(filename) or {}).get("sha256") or "")
        if not expected:
            return False
        if _sha256_path(directory / filename) != expected:
            return False
    return True


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unresolved_brand_ids_for_records(records: Iterable[dict[str, Any]], context: DictionaryContext) -> list[str]:
    """品牌还未入正式表不能阻断无品牌列的 v1 导出，但必须写入 manifest。"""
    used = {
        _text(context.product_by_sku.get(_text(record.get("sku")), {}).get("brand_id"))
        for record in records
    }
    return sorted(brand_id for brand_id in used if brand_id and brand_id in context.unresolved_brand_ids)


def _brand_reference_keys(rows: Iterable[dict[str, str]]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        values = [_text(row.get("brand_id")), _text(row.get("canonical_name"))]
        values.extend(part.strip() for part in _text(row.get("aliases_es")).split("|") if part.strip())
        keys.update(_normalized_brand_key(value) for value in values if value)
    return keys


def _normalized_brand_key(value: object) -> str:
    return " ".join(_text(value).casefold().split())


def is_valid_chinese_category_value(value: object) -> bool:
    """分类派生值必须包含中文，避免历史西语值被当作已确认结果。"""
    return bool(_CJK_RE.search(_text(value)))


def lookup_brand_row(brand_by_id: dict[str, dict[str, str]], brand_id: object) -> dict[str, str]:
    """按品牌 ID 查找记录，兼容官网大小写差异但不放宽别名匹配。"""
    value = _text(brand_id)
    if not value:
        return {}
    exact = brand_by_id.get(value)
    if exact is not None:
        return exact
    normalized = _normalized_brand_key(value)
    for key, row in brand_by_id.items():
        if _normalized_brand_key(key) == normalized:
            return row
    return {}


def _resolve_product_field(
    field: str,
    record: dict[str, Any],
    product: dict[str, str],
    manual: dict[str, str],
    context: DictionaryContext,
    source_hash: str,
    fallback: str,
) -> tuple[str, bool]:
    manual_value = _text(manual.get(field))
    if manual_value:
        return manual_value, False
    product_value = _text(product.get(field))
    product_hash_matches = _text(product.get("source_hash")) == source_hash
    product_status = _text(product.get("translation_status"))
    if product_value and product_hash_matches and product_status not in _UNUSABLE_TRANSLATION_STATUSES:
        return product_value, False
    model = context.model_by_sku.get(_text(record.get("sku")), {})
    model_value = _text(model.get(field))
    if (model_value and _text(model.get("source_hash")) == source_hash
            and _text(model.get("quality_status")).upper() == "OK"):
        return model_value, False
    # Do not expose a known-polluted Spanish fact as a Chinese fallback.  A
    # manual/model value above may still be used, but absent trusted evidence
    # the field remains blank and is marked for review.
    sku = _text(record.get("sku"))
    damage_key = "spec_es_raw" if field == "spec_zh_standard" else ("name_es_raw" if field == "name_zh_standard" else "")
    if damage_key and damage_key in context.damage_by_sku.get(sku, set()):
        return "", True
    return fallback, True


def _resolve_category_field(
    field: str,
    record: dict[str, Any],
    product: dict[str, str],
    manual: dict[str, str],
    context: DictionaryContext,
    source_hash: str,
) -> tuple[str, bool]:
    manual_value = _text(manual.get(field))
    if manual_value and is_valid_chinese_category_value(manual_value):
        return manual_value, False
    product_value = _text(product.get(field))
    if (
        product_value
        and is_valid_chinese_category_value(product_value)
        and _text(product.get("source_hash")) == source_hash
    ):
        return product_value, False
    cat1_key = normalize_category_key(record.get("cat1_es"))
    cat2_key = normalize_category_key(record.get("cat2_es"))
    mapped = context.category_by_pair.get((cat1_key, cat2_key)) or context.category_by_cat1.get(cat1_key) or {}
    mapped_value = _text(mapped.get(field))
    if mapped_value and is_valid_chinese_category_value(mapped_value):
        return mapped_value, False
    fallback = _text(record.get("cat1_es" if field == "cat1_zh" else "cat2_es"))
    return fallback, True


def _normalize_unit_price(value: str, terms: tuple[dict[str, str], ...]) -> tuple[str, bool]:
    if not value:
        return "", False
    result = value
    for term in terms:
        source, target = _text(term.get("term_es")), _text(term.get("term_zh"))
        if not source or not target or _text(term.get("keep_original")) in {"1", "true", "yes"}:
            continue
        result = re.sub(re.escape(source), target, result, flags=re.IGNORECASE)
    return result, bool(_LATIN_RE.search(result))


def _resolve_existing_chinese_field(
    record: dict[str, Any],
    zh_field: str,
    es_field: str,
    label: str,
    damaged_fields: set[str],
    damage_key: str,
) -> tuple[str | None, bool]:
    current = _none_or_text(record.get(zh_field))
    if current and _CJK_RE.search(current):
        return current, False
    fallback = _none_or_text(record.get(es_field))
    if damage_key in damaged_fields:
        return None, True
    if current and not _CJK_RE.search(current):
        return fallback or current, True
    return fallback, True


def _zh_remarks(record: dict[str, Any], fallbacks: list[str]) -> str:
    values = ["在售状态：在售"]
    if parse_bool_zh(record.get("is_new_badge")):
        values.append("新品")
    if parse_bool_zh(record.get("promotion")):
        values.append("促销")
    if parse_bool_zh(record.get("sustainable")):
        values.append("可持续")
    discount = parse_price(record.get("discount"))
    if discount is not None:
        values.append(f"折扣：{float(discount):g}")
    values.extend(fallbacks)
    return "；".join(values)


def _fact_source_hash(record: dict[str, Any]) -> str:
    payload = "\x1f".join(_text(record.get(field)) for field in ("name_es", "cat1_es", "cat2_es", "spec_es"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _display_original_price(record: dict[str, Any], *, sku: str) -> float | None:
    current = _required_price(record.get("current_price"), sku=sku)
    original = _optional_price(record.get("original_price"), sku=sku)
    return original if original is not None and original > current else None


def _required_price(value: Any, *, sku: str) -> float:
    parsed = _optional_price(value, sku=sku)
    if parsed is None:
        raise DictionaryJoinError(f"DICTIONARY_JOIN_BAD_PRICE: {sku}")
    return parsed


def _optional_price(value: Any, *, sku: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = parse_price(value)
    except (TypeError, ValueError) as exc:
        raise DictionaryJoinError(f"DICTIONARY_JOIN_BAD_PRICE: {sku}") from exc
    return None if parsed is None else float(parsed)


def _dictionary_content_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for filename in (
        "product_dictionary.csv", "brand_dictionary.csv", "category_dictionary.csv", "term_dictionary.csv",
        "manual_overrides.csv", "model_translation_overrides.csv", "source_damage_report.csv",
    ):
        digest.update(filename.encode("utf-8"))
        digest.update((directory / filename).read_bytes())
    return digest.hexdigest()


def _sku_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    sku = _text(record.get("sku"))
    return (0, int(sku), sku) if sku.isdigit() else (1, 0, sku)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _strict_bool_config(value: Any, *, name: str) -> bool:
    """配置只能用 YAML 布尔值，避免字符串 ``false`` 被 Python 当成真值。"""
    if isinstance(value, bool):
        return value
    raise DictionaryJoinError(f"INVALID_BOOLEAN_CONFIG: {name}")


def _none_or_text(value: Any) -> str | None:
    text = _text(value)
    return text or None
