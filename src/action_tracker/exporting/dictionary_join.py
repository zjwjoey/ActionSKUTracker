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
from ..localization.policy import OMIT_BRAND_FROM_CHINESE_DISPLAY
from ..localization.formatter import format_spec
from .repair import ExportRepairEngine
from .repair_report import ExportRepairReport
from .repair_rules import (
    repair_description,
    repair_details,
    repair_details_with_context,
    repair_spec,
    repair_title,
    repair_title_with_context,
    repair_unit_price,
    repair_content_with_context,
)


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
    confirmed_brand_matchers: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = ()
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
        confirmed_brand_matchers=_confirmed_brand_matchers(brands),
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


def _confirmed_display_brand_tokens(context: DictionaryContext | None) -> set[str]:
    if context is None:
        return set()
    tokens: set[str] = set()
    for row in context.brand_by_id.values():
        if not is_confirmed_brand_record(row):
            continue
        for value in (row.get("canonical_name"), row.get("brand_id")):
            if value:
                tokens.add(str(value).strip())
        aliases = str(row.get("aliases_es") or "")
        tokens.update(piece.strip() for piece in re.split(r"[,;|]", aliases) if piece.strip())
    return tokens


def build_zh_rows(
    records: Iterable[dict[str, Any]], context: DictionaryContext, *,
    repair_report: ExportRepairReport | None = None,
    repair_overrides_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按冻结优先级生成中文导出行，并返回逐字段 fallback 统计。"""
    rows: list[dict[str, Any]] = []
    fallback_counts: dict[str, int] = {}
    repair_engine = ExportRepairEngine(
        report=repair_report, overrides_path=repair_overrides_path,
        excluded_display_tokens=_confirmed_display_brand_tokens(context),
    ) if repair_report is not None else None
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
        if not brand_row:
            brand_row = _brand_row_from_source_title(
                _text(record.get("name_es")), context.confirmed_brand_matchers,
            )
        if (
            not used_fallback
            and not _text(manual.get("name_zh_standard"))
            and brand_row
            and is_confirmed_brand_record(brand_row)
        ):
            brand_value = _text(brand_row.get("canonical_name")) or brand_id
            if OMIT_BRAND_FROM_CHINESE_DISPLAY:
                # Remove only the identified brand span.  Generic text such
                # as “扑克牌/行李牌” must remain untouched.
                title = re.sub(rf"(?i)(?<!\w){re.escape(brand_value)}(?:牌)?", "", title, count=1).strip()
            else:
                title = format_confirmed_brand_title(title, brand_value)
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

        # Historical formal snapshots use the file-dictionary path. Apply the
        # same source-bound export repairs as the SQLite PRIMARY path so a
        # dated on-sale export cannot regress after the current run path was
        # fixed.
        if repair_engine is not None:
            title = repair_engine.apply(
                sku=sku, field="name_zh", value=title, source_field="name_es",
                source=record.get("name_es"), source_hash=source_hash,
                rule="TITLE_SOURCE_TOKEN", repairer=lambda value, source: repair_title_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            title = repair_engine.apply_override(
                sku=sku, field="name_zh", value=title, record=record, source_field="name_es",
            )
        else:
            title = repair_title(title, _none_or_text(record.get("name_es")))
        # A current model/manual value is already a reviewed translation.  The
        # source formatter is only allowed to rebuild a stale/missing value;
        # otherwise a valid value such as “模型规格” gets replaced by the raw
        # quantity extracted from Spanish.
        if repair_engine is not None:
            spec = repair_engine.apply(
                sku=sku, field="spec_zh", value=spec, source_field="spec_es",
                source=record.get("spec_es"), source_hash=source_hash,
                rule="SPEC_SOURCE_FACTS", repairer=lambda value, source: repair_spec(
                    value, source, force_source_facts=spec_fallback,
                ),
            )
            spec = repair_engine.apply_override(
                sku=sku, field="spec_zh", value=spec, record=record, source_field="spec_es",
            )
            unit_price = repair_engine.apply(
                sku=sku, field="unit_price_zh", value=unit_price, source_field="unit_price",
                source=record.get("unit_price"), source_hash=source_hash,
                rule="UNIT_PRICE_NORMALIZE", repairer=repair_unit_price,
            )
            description = repair_engine.apply(
                sku=sku, field="desc_zh", value=description, source_field="desc_es",
                source=record.get("desc_es"), source_hash=source_hash,
                rule="DESCRIPTION_SOURCE_FACTS", repairer=lambda value, source: repair_content_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            description = repair_engine.apply_override(
                sku=sku, field="desc_zh", value=description, record=record, source_field="desc_es",
            )
            details = repair_engine.apply(
                sku=sku, field="details_zh", value=details, source_field="details_es",
                source=record.get("details_es"), source_hash=source_hash,
                rule="DETAILS_SOURCE_FACTS", repairer=lambda value, source: repair_details_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            details = repair_engine.apply_override(
                sku=sku, field="details_zh", value=details, record=record, source_field="details_es",
            )
        else:
            spec = repair_spec(spec, _none_or_text(record.get("spec_es")), force_source_facts=spec_fallback)
            unit_price = repair_unit_price(unit_price)
            description = repair_description(description, _none_or_text(record.get("desc_es")))
            details = repair_details(details, _none_or_text(record.get("details_es")))

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


def build_zh_rows_from_localized_source(
    records: Iterable[dict[str, Any]],
    *,
    category_context: DictionaryContext | None = None,
    repair_report: ExportRepairReport | None = None,
    repair_overrides_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
    repair_engine = ExportRepairEngine(
        report=repair_report, overrides_path=repair_overrides_path,
        excluded_display_tokens=_confirmed_display_brand_tokens(category_context),
    ) if repair_report is not None else None
    for record in sorted(records, key=_sku_sort_key):
        sku = _text(record.get("sku"))
        fallbacks: list[str] = []
        plan = engine.primary_export_plan(record)
        resolved: dict[str, Any] = {key: _none_or_text(field.value) for key, field in plan.fields.items()}
        if category_context is not None:
            # PRIMARY values are still the source of truth for approved
            # fields, but an untranslated/fallback category must use the
            # reviewed pair mapping at the export boundary.  This keeps the
            # SQLite path consistent with the file-dictionary path without
            # rejoining product names/specifications.
            mapped_cat1, mapped_cat1_fallback = _resolve_category_field(
                "cat1_zh", record, {}, {}, category_context, _fact_source_hash(record),
            )
            mapped_cat2, mapped_cat2_fallback = _resolve_category_field(
                "cat2_zh", record, {}, {}, category_context, _fact_source_hash(record),
            )
            if mapped_cat1 and not mapped_cat1_fallback:
                resolved["cat1_zh"] = mapped_cat1
            if mapped_cat2 and not mapped_cat2_fallback:
                resolved["cat2_zh"] = mapped_cat2
            # The SQLite PRIMARY path must apply the same confirmed-brand
            # display policy as the file-dictionary path.  Without this
            # boundary normalization, a stale localized title can reintroduce
            # brands that are intentionally omitted from Chinese display.
            product = category_context.product_by_sku.get(sku, {})
            brand_row = lookup_brand_row(
                category_context.brand_by_id, _text(product.get("brand_id"))
            )
            if not brand_row:
                brand_row = _brand_row_from_source_title(
                    _none_or_text(record.get("name_es")), category_context.confirmed_brand_matchers
                )
            if (
                brand_row
                and is_confirmed_brand_record(brand_row)
                and OMIT_BRAND_FROM_CHINESE_DISPLAY
            ):
                brand_value = _text(brand_row.get("canonical_name"))
                if brand_value:
                    resolved["name_zh"] = re.sub(
                        rf"(?i)(?<!\w){re.escape(brand_value)}(?:牌)?",
                        "",
                        _none_or_text(resolved.get("name_zh")),
                        count=1,
                    ).strip()
        # Normalize deterministic adapter regressions even when the stored
        # PRIMARY value predates the current formatter policy.
        source_hash = _fact_source_hash(record)
        if repair_engine is not None:
            resolved["unit_price_zh"] = repair_engine.apply(
                sku=sku, field="unit_price_zh", value=_none_or_text(record.get("unit_price_raw")) or resolved.get("unit_price_zh"),
                source_field="unit_price", source=record.get("unit_price"), source_hash=source_hash,
                rule="UNIT_PRICE_NORMALIZE", repairer=repair_unit_price,
            )
            resolved["name_zh"] = repair_engine.apply(
                sku=sku, field="name_zh", value=resolved.get("name_zh"), source_field="name_es",
                source=record.get("name_es"), source_hash=source_hash,
                rule="TITLE_SOURCE_TOKEN", repairer=lambda value, source: repair_title_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            resolved["name_zh"] = repair_engine.apply_override(
                sku=sku, field="name_zh", value=resolved.get("name_zh"), record=record, source_field="name_es",
            )
            resolved["spec_zh"] = repair_engine.apply(
                sku=sku, field="spec_zh", value=resolved.get("spec_zh"), source_field="spec_es",
                source=record.get("spec_es"), source_hash=source_hash,
                rule="SPEC_SOURCE_FACTS", repairer=lambda value, source: repair_spec(
                    value, source, force_source_facts=plan.fields["spec_zh"].status != "READY",
                ),
            )
            resolved["spec_zh"] = repair_engine.apply_override(
                sku=sku, field="spec_zh", value=resolved.get("spec_zh"), record=record, source_field="spec_es",
            )
            resolved["desc_zh"] = repair_engine.apply(
                sku=sku, field="desc_zh", value=resolved.get("desc_zh"), source_field="desc_es",
                source=record.get("desc_es"), source_hash=source_hash,
                rule="DESCRIPTION_SOURCE_FACTS", repairer=lambda value, source: repair_content_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            resolved["desc_zh"] = repair_engine.apply_override(
                sku=sku, field="desc_zh", value=resolved.get("desc_zh"), record=record, source_field="desc_es",
            )
            resolved["details_zh"] = repair_engine.apply(
                sku=sku, field="details_zh", value=resolved.get("details_zh"), source_field="details_es",
                source=record.get("details_es"), source_hash=source_hash,
                rule="DETAILS_SOURCE_FACTS", repairer=lambda value, source: repair_details_with_context(
                    value, source, excluded_tokens=repair_engine.excluded_display_tokens,
                ),
            )
            resolved["details_zh"] = repair_engine.apply_override(
                sku=sku, field="details_zh", value=resolved.get("details_zh"), record=record, source_field="details_es",
            )
        else:
            resolved["unit_price_zh"] = repair_unit_price(
                _none_or_text(record.get("unit_price_raw")) or resolved.get("unit_price_zh")
            )
            resolved["name_zh"] = repair_title(resolved.get("name_zh"), _none_or_text(record.get("name_es")))
            resolved["spec_zh"] = repair_spec(
                resolved.get("spec_zh"), _none_or_text(record.get("spec_es")),
                force_source_facts=plan.fields["spec_zh"].status != "READY",
            )
            resolved["desc_zh"] = repair_description(resolved.get("desc_zh"), _none_or_text(record.get("desc_es")))
            resolved["details_zh"] = repair_details(resolved.get("details_zh"), _none_or_text(record.get("details_es")))
        source_by_field = {
            "name_zh": "name_es", "cat1_zh": "cat1_es", "cat2_zh": "cat2_es",
            "spec_zh": "spec_es", "desc_zh": "desc_es", "details_zh": "details_es",
        }
        for target_field, source_field in source_by_field.items():
            if not _none_or_text(record.get(source_field)):
                resolved[target_field] = None
        labels = {"name_zh": "中文品名", "cat1_zh": "中文分类1", "cat2_zh": "中文分类2", "spec_zh": "中文规格", "unit_price_zh": "中文单价", "desc_zh": "中文描述", "details_zh": "中文产品详情"}
        source_by_field = {
            "name_zh": "name_es", "cat1_zh": "cat1_es", "cat2_zh": "cat2_es",
            "spec_zh": "spec_es", "desc_zh": "desc_es", "details_zh": "details_es",
        }
        for key, field in plan.fields.items():
            # A value that is already present, current, and source-bound is
            # usable in the formal export even when the legacy row still
            # carries the old PENDING review label.  Keep the marker only for
            # an actually empty or stale field; otherwise every historical
            # translation would block release forever without changing its
            # content.
            source_field = source_by_field.get(key)
            source_exists = True if source_field is None else bool(_none_or_text(record.get(source_field)))
            if field.status != "READY" and source_exists and not _none_or_text(resolved.get(key)):
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


def _brand_row_from_source_title(
    source_title: str,
    matchers: tuple[tuple[re.Pattern[str], dict[str, str]], ...],
) -> dict[str, str]:
    """Resolve a confirmed brand from the Spanish source title when the
    product dictionary has no brand_id (a common legacy Gold-row shape).

    This is deliberately dictionary-bound: it never infers a new brand from
    arbitrary title text and chooses the longest confirmed alias only.
    """
    source = _text(source_title).casefold()
    for pattern, row in matchers:
        if pattern.search(source):
            return row
    return {}


def _confirmed_brand_matchers(
    rows: Iterable[dict[str, str]],
) -> tuple[tuple[re.Pattern[str], dict[str, str]], ...]:
    """Compile confirmed brand aliases once, longest first."""
    candidates: list[tuple[int, str, dict[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not is_confirmed_brand_record(row):
            continue
        aliases = [_text(row.get("canonical_name"))]
        aliases.extend(part.strip() for part in _text(row.get("aliases_es")).split("|") if part.strip())
        for alias in aliases:
            folded = alias.casefold()
            identity = (_normalized_brand_key(row.get("brand_id")), folded)
            if len(folded) >= 3 and identity not in seen:
                seen.add(identity)
                candidates.append((len(folded), folded, row))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return tuple(
        (re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)"), row)
        for _, alias, row in candidates
    )


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
    # A missing Spanish source cannot own a translated target value. Keeping
    # it empty prevents category/detail facts from being copied into name or
    # specification merely to improve apparent coverage.
    if not fallback:
        return "", False
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
    localized_field = {
        "name_zh_standard": "name_zh",
        "spec_zh_standard": "spec_zh",
    }.get(field)
    source_bound_fields = set(record.get("_source_bound_localization_fields") or ())
    source_bound_value = _text(record.get(localized_field)) if localized_field else ""
    if localized_field in source_bound_fields and source_bound_value and _CJK_RE.search(source_bound_value):
        return source_bound_value, False
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
    cat1_key = normalize_category_key(record.get("cat1_es"))
    cat2_key = normalize_category_key(record.get("cat2_es"))
    mapped = context.category_by_pair.get((cat1_key, cat2_key)) or context.category_by_cat1.get(cat1_key) or {}
    mapped_value = _text(mapped.get(field))
    if mapped_value and is_valid_chinese_category_value(mapped_value):
        return mapped_value, False
    if field == "cat2_zh":
        # Reviewed pair-level fallbacks for legacy dictionary rows that only
        # carried the fixed first-level category.  These are source-bound
        # category names, never model guesses.
        pair_fallbacks = {
            ("hogar", "ventiladores"): "风扇",
            ("mascotas", "perro"): "狗用品",
            ("mascotas", "gato"): "猫用品",
            ("juguetes", "puzles"): "拼图",
            ("viajes", "articulos de acampada"): "露营用品",
            ("cuidado personal", "cuidados para el bebe"): "婴儿护理用品",
            ("juguetes", "juguetes para bebes"): "婴幼儿玩具",
            ("bricolaje", "bicicleta"): "自行车用品",
        }
        pair_value = pair_fallbacks.get((cat1_key, cat2_key), "")
        if pair_value:
            return pair_value, False
    # Product-level category text is a compatibility fallback. The reviewed
    # pair dictionary above is the controlled category vocabulary and must win
    # when both are available.
    product_value = _text(product.get(field))
    if (
        product_value
        and is_valid_chinese_category_value(product_value)
        and _text(product.get("source_hash")) == source_hash
    ):
        return product_value, False
    # ``Atrás`` is a navigation label, not a product category. Some legacy
    # detail pages shifted the product name into cat1 and this label into cat2;
    # keep both derived category fields empty for review instead of exporting
    # the contaminated Spanish text as a Chinese category.
    if cat2_key == "atras":
        return "", False
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
    fallback = _none_or_text(record.get(es_field))
    if not fallback:
        # Preserve source-empty semantics while still surfacing the missing
        # derived field for the review queue and export remarks.
        return None, True
    current = _none_or_text(record.get(zh_field))
    if current and _CJK_RE.search(current):
        return current, False
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
    raw_tags = _none_or_text(record.get("raw_tags"))
    if raw_tags:
        values.append(f"官网官方标签：{raw_tags}")
    values.extend(fallbacks)
    return "；".join(values)


def _repair_export_unit_price(value: str | None) -> str | None:
    text = _none_or_text(value)
    if not text:
        return text
    # Repair only the known legacy prefix corruption.  Do not translate
    # arbitrary prose in the unit-price field at export time.
    return re.sub(r"€/升av(?![A-Za-z])", "€/次洗涤", text, flags=re.I)


def _repair_export_title(value: str | None, source: str | None) -> str | None:
    """Keep high-confidence technical title tokens after translation.

    Brands are handled separately by the confirmed-brand policy. This helper
    only carries source-visible model/interface/format tokens that are useful
    research facts and safe to preserve verbatim.
    """
    text = _none_or_text(value)
    source_text = _none_or_text(source)
    if not text or not source_text:
        return text
    patterns = (
        r"(?<![A-Za-z0-9])(?:F\d{2,3}|H[47]|A[45]|B5|LR44|CR\d{4,5}|RAL\s*\d{3,4}|T\d{3,4}|GaN|mAh|LED|HSS|HDMI|USB(?:-[A-Z])?|MagSafe|Switch|PS4|PC|XL|XXL)(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])Series\s+\d+(?![A-Za-z0-9])",
    )
    tokens: list[str] = []
    for pattern in patterns:
        tokens.extend(re.findall(pattern, source_text, flags=re.I))
    for token in dict.fromkeys(tokens):
        if token.casefold() not in text.casefold():
            text = f"{text}｜{token}"
    # Numeric identity in a product name is source-bound.  Do not recover a
    # brand digit such as the leading ``7`` in ``7Up`` under the no-brand
    # display policy.
    if not re.search(r"(?<![A-Za-z0-9])7up(?![A-Za-z0-9])", source_text, flags=re.I):
        for number in _spec_numbers(source_text):
            # A leading number in an alphanumeric brand/model such as ``3M``
            # is not a standalone product quantity.  Appending the bare
            # number would create a false fact (``相框挂条｜3``).
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(number)}[A-Za-z](?![A-Za-z0-9])", source_text):
                continue
            if not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text):
                text = f"{text}｜{number}"
    for match in re.finditer(r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*mcg\b", source_text, flags=re.I):
        number = match.group(1).replace(",", ".")
        if re.search(rf"(?<!\d){re.escape(number)}\s*(?:微克|mcg)\b", text, flags=re.I):
            continue
        text = re.sub(rf"([｜|]\s*){re.escape(number)}\b", rf"\g<1>{number}微克", text, count=1)
    # High-confidence title fragments that are still present as Spanish in
    # legacy localized values.  These replacements are source-bound and do
    # not attempt to translate free-form prose.
    text = re.sub(r"(?i)\bcalentador\s+eléctrico\b", "电暖器", text)
    text = re.sub(r"(?i)\bhilo\s+de\s+tejer\b", "编织线", text)
    text = re.sub(r"(?i)\bcantimplora\s+lujosa\b", "豪华水壶", text)
    text = re.sub(r"(?i)\ball-in-1\b", "一体式", text)
    return text


def _repair_export_details(value: str | None, source: str | None) -> str | None:
    text = _none_or_text(value)
    source_text = (_none_or_text(source) or "").casefold()
    if not text:
        return text
    # These are source-bound, reviewed detail regressions.  They are applied
    # only when the Spanish key/phrase is present, so unrelated Chinese text
    # is not globally rewritten.
    if "número de turnos de limpieza" in source_text:
        text = text.replace("清洁档位数量", "洗涤次数").replace("清洁次数", "洗涤次数")
    if "polipropileno" in source_text:
        text = text.replace("聚丙烯(聚丙烯)", "聚丙烯（PP）").replace("聚丙烯（聚丙烯）", "聚丙烯（PP）")
    if "no desechable" in source_text:
        text = text.replace("否 一次性", "非一次性").replace("不可一次性", "非一次性")
    if "con líneas" in source_text:
        text = text.replace("条纹", "横线")
    if "aclarado" in source_text:
        text = text.replace("澄清", "冲洗").replace("是否提亮", "是否冲洗")
    if "cubierta blanda" in source_text:
        text = text.replace("软面", "平装").replace("软封面", "平装").replace("软皮封面", "平装")
    if "ave" in source_text:
        text = text.replace("禽肉", "禽类")
    if "lapicero" in source_text:
        text = text.replace("铅笔", "圆珠笔")
    if "fosa" in source_text:
        text = text.replace("坑式", "坑")
    if "no lavar" in source_text:
        text = text.replace("洗涤说明：否", "不可洗涤").replace("洗涤：否", "不可洗涤").replace("不可水洗", "不可洗涤")
    if "ficción" in source_text:
        text = text.replace("小说", "虚构类")
    if "refill" in source_text or "recargable" in source_text or "rellenable" in source_text:
        text = re.sub(r"refill", "补充装", text, flags=re.I)
        text = re.sub(r"dispenser", "分配器", text, flags=re.I)
    if "dispensador" in source_text:
        text = re.sub(r"dispens(?:er)?", "分配器", text, flags=re.I)
    if "sustancia" in source_text and "válido" in source_text:
        text = re.sub(r"(?:形态|成分)：[^；]+", "来源异常：官网字段Sustancia=Válido", text, count=1)
        if "来源异常：官网字段Sustancia=Válido" not in text:
            text += "；来源异常：官网字段Sustancia=Válido"
    if "incluye oído" in source_text:
        text = re.sub(r"(?:带把手|是否带耳)：(?:是|否)", "来源异常：官网字段Incluye oído", text)
        if "来源异常：官网字段Incluye oído" not in text:
            text += "；来源异常：官网字段Incluye oído"
    text = re.sub(r"\s*是[“\"]", "；", text)
    text = re.sub(r"选择\s*其中\s*[,，]\s*[,，]；的\s*的；", "多种款式可选；", text)
    text = re.sub(r"；{2,}", "；", text)
    return _repair_source_bound_content_facts(text, source, include_generic=False)


def _repair_export_spec(
    value: str | None,
    source: str | None,
    *,
    force_source_facts: bool = False,
) -> str | None:
    text = _none_or_text(value)
    source_value = _none_or_text(source) or ""
    source_text = source_value.casefold()
    if not text:
        return text
    source_numbers = _spec_numbers(source)
    target_numbers = _spec_numbers(text)
    rebuild_terms = (
        "gramos", "unidades", "piezas", "pares", "vatios", "lavados", "litro", "litros",
        "ml", "cm", "mm", "mAh", "mililitros", "milímetros", "centímetros", "metros", "pulgadas", "variantes",
        "números", "comprimidos", "tabletas", "hojas", "años", "lumen", "lúmenes",
    )
    # Existing PRIMARY specs can be stale or contaminated with facts from
    # descriptions/details.  For unit/quantity/dimension source text, use the
    # deterministic source formatter when the numeric multiset disagrees.
    # Product/platform-only specs stay on their existing value so their
    # reviewed technical phrasing is not replaced by raw Spanish.
    if (force_source_facts and source_numbers != target_numbers
            and (source_numbers or target_numbers)
            and any(term.casefold() in source_text for term in rebuild_terms)):
        text = format_spec(source_value)
    if "vatios" in source_text:
        text = re.sub(r"(?i)(?<=\d)\s*vatios\b", "W", text)
    if "lavados" in source_text:
        text = re.sub(r"(?i)(?<=\d)\s*lavados\b", "次洗涤", text)
    if re.search(r"\b\d+\s+en\s+\d+\b", source_text):
        text = re.sub(r"(?i)\b(\d+)\s+en\s+(\d+)\b", r"\1合\2", text)
    # A stale dictionary fallback is intentionally kept as the original
    # source value by the legacy export contract.  Do not rewrite that exact
    # source text here; the source-bound repair layer can still report it for
    # later review without changing the historical fallback output.
    spanish_fallback = force_source_facts and text.casefold().strip() == source_text.strip()
    if "números" in source_text or "pares" in source_text:
        text = re.sub(r"(?i)\bNúmeros\b", "尺码", text)
        text = re.sub(r"(?i)\bpares?\b", "双", text)
    text = re.sub(r"(?i)\bdiferentes\s+variantes?\b", "多种款式", text)
    text = re.sub(r"(?i)\bvarios\s+colores?\b", "多种颜色", text)
    text = re.sub(r"(?i)\b(\d+(?:[.,]\d+)?)\s+gramos?\b", r"\1克", text)
    text = re.sub(r"(?i)\b(\d+(?:[.,]\d+)?)\s+comprimidos?\b", r"\1片", text)
    text = re.sub(r"(?i)\b(\d+(?:[.,]\d+)?)\s+tabletas?\b", r"\1片", text)
    if not spanish_fallback:
        text = re.sub(r"(?i)\b(\d+(?:[.,]\d+)?)\s+unidades?\b", r"\1件", text)
    text = re.sub(r"(?i)\bvarios\s+tipos\b", "多种类型", text)
    if re.search(r"\bxl\b", source_text) and "×L" in text:
        text = text.replace("×L", "XL")
    if re.search(r"\bxxl\b", source_text) and "××L" in text:
        text = text.replace("××L", "XXL")
    for token in ("LEGO", "Switch", "PS4", "PC", "LED"):
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", source_value, re.I) and token.casefold() not in text.casefold():
            text = f"{text}｜{token}" if text else token
    return text


def _spec_numbers(value: str | None) -> tuple[str, ...]:
    numbers = []
    for token in re.findall(r"\d+(?:[.,]\d+)?", _none_or_text(value) or ""):
        normalized = token.replace(",", ".")
        if re.fullmatch(r"\d+[.]\d{3}", normalized):
            normalized = normalized.replace(".", "")
        elif normalized.endswith(".0"):
            normalized = normalized[:-2]
        numbers.append(normalized)
    return tuple(sorted(numbers))


def _repair_export_description(value: str | None, source: str | None = None) -> str | None:
    text = _none_or_text(value)
    source_value = _none_or_text(source) or ""
    source_text = source_value.casefold()
    if not text:
        return text
    # Preserve source certifications and material facts when a stale PRIMARY
    # translation dropped them. These repairs are source-bound and append or
    # replace only the known erroneous projection.
    if "fsc" in source_text and "fsc" not in text.casefold():
        text = f"{text}；FSC认证"
    if ("bci" in source_text or "better cotton" in source_text) and "bci" not in text.casefold() and "better cotton" not in text.casefold():
        text = f"{text}；BCI认证"
    if "mini eau de toilette" in source_text and "迷你淡香水" not in text:
        text = text.replace("洁面皂", "迷你淡香水")
        if "迷你淡香水" not in text:
            text = f"{text}；迷你淡香水"
    if "poliamida" in source_text and "elastano" in source_text:
        # The known bad projection used 聚酯纤维 for a source that says
        # poliamida + elastano. Correct it only when polyester is absent from
        # the source, keeping legitimate polyester products untouched.
        if "poliéster" not in source_text and "聚酯纤维" in text:
            text = text.replace("聚酯纤维", "锦纶和氨纶", 1)
        else:
            if "锦纶" not in text and "聚酰胺" not in text:
                text += "；锦纶"
            if "氨纶" not in text and "弹性纤维" not in text:
                text += "；氨纶"
    text = _repair_source_bound_content_facts(text, source_value, include_generic=True)
    text = re.sub(r"\s*是[“\"]", "；", text)
    text = re.sub(r"选择\s*其中\s*[,，]\s*[,，]；的\s*的；", "多种款式可选；", text)
    text = re.sub(r"；{2,}", "；", text)
    return text


def _repair_source_bound_content_facts(
    value: str | None, source: str | None, *, include_generic: bool = False
) -> str | None:
    """Restore high-confidence source facts lost by an old PRIMARY candidate.

    This is deliberately limited to description/details export projection. It
    preserves the source token or a direct Chinese equivalent and never
    imports facts from another field.  The database candidate remains
    immutable; the repair is applied at the same boundary as the other legacy
    projection repairs above.
    """
    text = _none_or_text(value)
    source_text = _none_or_text(source) or ""
    if not text or not source_text:
        return text
    source_lower = source_text.casefold()
    target_lower = text.casefold()
    # Article numbers belong to the structured details field.  Older
    # dictionary candidates occasionally copied them into descriptions;
    # remove that cross-field leakage when the Spanish description itself does
    # not contain an article-number fact.
    if not re.search(r"número\s+del\s+artículo|código\s+del\s+artículo", source_lower):
        text = re.sub(r"(?:商品|产品)编号\s*[:：]\s*\d+", "", text)
        text = re.sub(r"；{2,}", "；", text).strip("； ")
        target_lower = text.casefold()
    token_repairs = (
        (r"\bmdf\b", "MDF（中密度纤维板）", ("mdf", "中密度纤维板", "纤维板")),
        (r"\blego\b", "LEGO", ("lego", "乐高")),
        (r"\bgsm\b", "GSM（克重）", ("gsm", "克重")),
        (r"\bled\b", "LED", ("led", "发光二极管")),
        (r"\bpc\b", "PC", ("pc", "电脑")),
        (r"\bswitch\b", "Nintendo Switch", ("switch", "任天堂")),
        (r"\bxl\b", "XL", ("xl", "加大", "超大", "特大")),
    )
    for pattern, replacement, aliases in token_repairs:
        # The generic numeric/unit pass below renders ``550 GSM``.  Adding a
        # second standalone GSM marker here would create a duplicated
        # protected token in the final audit.
        if pattern == r"\bgsm\b" and re.search(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s*gsm\b", source_text, flags=re.I):
            continue
        if re.search(pattern, source_lower, flags=re.I) and not any(alias in target_lower for alias in aliases):
            text = f"{text}；{replacement}"
            target_lower = text.casefold()

    # Preserve explicit bundle/function statements such as ``2 en 1`` and
    # ``3 en 1``. A loose phrase such as “double experience” is not a reliable
    # substitute because it drops the source's second number.
    for match in re.finditer(r"(?<!\d)(\d+)\s+en\s+(\d+)(?!\d)", source_text, flags=re.I):
        left, right = match.groups()
        rendered = f"{left}合{right}"
        if rendered not in text and f"{left} 合 {right}" not in text:
            text = f"{text}；{rendered}"
            target_lower = text.casefold()

    # When the source states a total port count and the Chinese sentence only
    # lists the split inputs/outputs, retain the total explicitly as well.
    for match in re.finditer(r"(?<!\d)(\d+)\s+puertos?\b", source_text, flags=re.I):
        total = match.group(1)
        if not re.search(rf"(?<!\d){re.escape(total)}(?:\s*个)?(?:USB[-‑–— ]?[A-Z])?(?:接口|端口)", text, flags=re.I):
            text = f"{text}；接口总数：{total}个"

    # A source that explicitly offers two variants should retain that count
    # even when the translation names both alternatives in prose.
    if re.search(r"\b(?:dos|2)\s+variantes?\b", source_text, flags=re.I):
        if not re.search(r"(?:两|2)款", text):
            text = f"{text}；共两款"

    # Inches and GSM are frequent real omissions.  Keep the unit attached to
    # the number so the repair is auditable and cannot be mistaken for a new
    # quantity.  Other quantities are handled by the semantic QA rules.
    for match in re.finditer(r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*(?:[\"″]|pulgadas?)", source_text, flags=re.I):
        number = match.group(1).replace(",", ".")
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text) or re.search(rf"(?<!\d){re.escape(number.replace('.', ','))}(?!\d)", text):
            continue
        text = f"{text}；尺寸：{number}英寸"
    for match in re.finditer(r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*gsm\b", source_text, flags=re.I):
        number = match.group(1).replace(",", ".")
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text) and ("gsm" in target_lower or "克重" in target_lower or "克/平方米" in target_lower):
            continue
        text = f"{text}；克重：{number} GSM"
    if include_generic:
        generic_source = re.sub(r"(?<=\d)'(?=\d)", ".", source_text)
        for match in re.finditer(r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*cm\b", generic_source, flags=re.I):
            dimensions = "×".join(piece.replace(",", ".") for piece in match.groups() if piece)
            if not re.search(rf"{re.escape(dimensions)}\s*(?:厘米|cm)", text, flags=re.I):
                text = f"{text}；尺寸：{dimensions}厘米"
    # Preserve high-confidence numeric/unit facts in the field that owns the
    # source description/details.  This covers common omissions such as
    # ``2,7 kg``, ``360°``, ``50%``, ``565 kcal`` and ``12 MP`` without
    # importing values from other catalog fields.
    unit_aliases = {
        "kg": "kg", "g": "g", "mg": "mg", "ml": "ml", "cl": "cl",
        "l": "L", "mah": "mAh", "mp": "MP", "hz": "Hz", "kcal": "kcal",
        "w": "W", "v": "V", "%": "%", "°": "°", "m": "m", "cm": "cm",
        "mm": "mm", "km": "km", "ah": "Ah", "wh": "Wh", "kwh": "kWh",
        "mw": "mW", "kw": "kW", "db": "dB", "°c": "°C",
    }
    fact_pattern = re.compile(r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*(kWh|mAh|kW|mW|Ah|Wh|dB|°C|kg|mg|ml|cl|km|cm|mm|MP|Hz|kcal|W|V|g|l|m|%|°)(?![A-Za-z0-9])", re.I)
    generic_fact_source = re.sub(r"(?<=\d)[ .](?=\d{3}(?:\D|$))", "", source_text)
    for match in fact_pattern.finditer(generic_fact_source) if include_generic else ():
        number = match.group(1).replace(",", ".")
        unit = unit_aliases[match.group(2).casefold()]
        number_present = bool(re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text) or re.search(rf"(?<!\d){re.escape(number.replace('.', ','))}(?!\d)", text))
        aliases = {
            "kg": ("公斤", "千克"), "g": ("克",), "mg": ("毫克",), "ml": ("毫升",), "cl": ("厘升",),
            "l": ("升",), "mah": ("毫安时",), "mp": ("万像素", "万"), "hz": ("赫兹",), "kcal": ("千卡",),
            "w": ("瓦",), "v": ("伏",), "%": ("%",), "°": ("度",),
            "m": ("米",), "cm": ("厘米",), "mm": ("毫米",), "km": ("千米",),
            "ah": ("安时",), "wh": ("瓦时",), "kwh": ("千瓦时",),
            "mw": ("毫瓦",), "kw": ("千瓦",), "db": ("分贝",), "°c": ("摄氏度",),
        }.get(unit.casefold(), ())
        unit_present = unit.casefold() in text.casefold() or any(alias in text for alias in aliases)
        if unit.casefold() == "mp" and number_present and re.search(rf"(?<!\d){re.escape(str(int(float(number) * 100)))}万", text):
            unit_present = True
        if unit.casefold() == "mp" and re.search(rf"(?<!\d){re.escape(str(int(float(number) * 100)))}万", text):
            continue
        if number_present and unit_present:
            continue
        text = f"{text}；参数：{number}{unit}"
        target_lower = text.casefold()
    for match in re.finditer(r"(?<![A-Za-z0-9])(\d+)\s*(horas?|minutos?)\b", source_text, flags=re.I) if include_generic else ():
        number, unit = match.group(1), match.group(2).casefold()
        aliases = ("小时",) if unit.startswith("hora") else ("分钟",)
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text) and any(alias in text for alias in aliases):
            continue
        text = f"{text}；参数：{number}{aliases[0]}"
    if include_generic and not re.search(r"número\s+del\s+artículo|código\s+del\s+artículo", source_lower):
        for number in _spec_numbers(source_text):
            if not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text):
                text = f"{text}；参数：{number}"
    # Legacy candidates sometimes glue a translated noun to a percentage
    # (``modo95%``/``fibras100%``/``GSM100%``). Normalize the glue without
    # changing the percentage fact itself.
    text = re.sub(r"(?i)(?:gsm|modo|fibras)(\d+(?:[.,]\d+)?)\s*%", r"\1%", text)
    return text


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
