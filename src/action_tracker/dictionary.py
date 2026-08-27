"""Action 商品字典的本地、可审计基础层。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence


DICTIONARY_SCHEMA_VERSION = 2
DICTIONARY_STATUSES = (
    "UNTRANSLATED", "LEGACY_UNVERIFIED", "MODEL_TRANSLATED",
    "RULE_NORMALIZED", "NEEDS_REVIEW", "HUMAN_REVIEWED", "LOCKED",
)
_LEGACY_TRANSLATION_STATUS = {
    "OK": "LEGACY_UNVERIFIED", "FALLBACK_ES": "UNTRANSLATED",
    "NOT_CONFIGURED": "UNTRANSLATED", "STALE": "NEEDS_REVIEW",
}

PRODUCT_DICTIONARY_HEADERS = [
    "sku", "canonical_id", "name_es_raw", "name_zh_standard", "brand_id",
    "cat1_es", "cat2_es", "cat1_zh", "cat2_zh", "spec_es_raw",
    "spec_zh_standard", "source_hash", "translation_status", "review_status",
    "locked", "source_first_seen", "source_last_seen", "updated_at", "notes",
]
BRAND_DICTIONARY_HEADERS = [
    "brand_id", "canonical_name", "aliases_es", "keep_original",
    "is_action_brand", "confidence", "review_status", "notes",
]
CATEGORY_DICTIONARY_HEADERS = [
    "cat1_es", "cat2_es", "cat1_code", "cat1_zh", "cat2_zh",
    "review_status", "notes",
]
TERM_DICTIONARY_HEADERS = [
    "term_es", "term_zh", "term_type", "forbidden_zh", "keep_original",
    "review_status", "notes",
]
SOURCE_DAMAGE_HEADERS = ["sku", "damaged_fields", "status", "notes"]
DICTIONARY_BASELINE_FILENAMES = (
    "product_dictionary.csv", "brand_dictionary.csv", "category_dictionary.csv",
    "term_dictionary.csv", "manual_overrides.csv", "model_translation_overrides.csv",
    "source_damage_report.csv",
)
MODEL_TRANSLATION_HEADERS = [
    "sku", "source_hash", "name_zh_standard", "spec_zh_standard", "quality_status",
    "model", "updated_at", "notes",
]
OVERRIDE_HEADERS = [
    "scope", "key", "field", "value", "reason", "source", "locked", "updated_at",
]
PRODUCT_OVERRIDE_FIELDS = frozenset({
    "name_zh_standard", "brand_id", "cat1_zh", "cat2_zh", "spec_zh_standard",
    "translation_status", "review_status", "locked", "notes",
})


class DictionaryValidationError(ValueError):
    """字典结构或主键不合法，必须阻断写入。"""


class DictionaryCommitPending(RuntimeError):
    """目标 CSV 被 Excel 占用，已保留待提交文件。"""


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _is_locked(value: object) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "locked"}


def normalize_category_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", _text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def _row_key(row: Mapping[str, object], key_fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(_text(row.get(field)) for field in key_fields)


def validate_dictionary_rows(
    rows: Iterable[Mapping[str, object]], *, headers: Sequence[str], key_fields: Sequence[str],
) -> list[dict[str, str]]:
    """验证行与唯一键；任何异常都会阻断整次写入。"""
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, raw in enumerate(rows, start=2):
        row = {header: _text(raw.get(header)) for header in headers}
        key = _row_key(row, key_fields)
        # 二级类目可以为空，组合键仍由一级类目唯一识别；其余空键会在
        # 对应领域校验（如产品覆盖）中进一步拒绝。
        if not key_fields or not any(key):
            raise DictionaryValidationError(f"EMPTY_KEY at CSV row {index}: {key_fields}")
        if key in seen:
            raise DictionaryValidationError(f"DUPLICATE_KEY at CSV row {index}: {key}")
        seen.add(key)
        normalized.append(row)
    return normalized


def load_dictionary_rows(
    path: Path, *, headers: Sequence[str], key_fields: Sequence[str],
) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []
        missing = [header for header in headers if header not in reader.fieldnames]
        if missing:
            raise DictionaryValidationError(f"SCHEMA_MISMATCH {path.name}: missing {missing}")
        return validate_dictionary_rows(reader, headers=headers, key_fields=key_fields)


def load_dictionary_csv(path: Path, *, key_field: str) -> dict[str, dict[str, str]]:
    """兼容旧调用；仍拒绝重复主键，绝不静默覆盖。"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if key_field not in headers:
            raise DictionaryValidationError(f"SCHEMA_MISMATCH {path.name}: missing {key_field}")
        rows = validate_dictionary_rows(reader, headers=headers, key_fields=(key_field,))
    return {row[key_field]: row for row in rows}


def write_dictionary_csv(
    path: Path, rows: Iterable[Mapping[str, object]], headers: list[str], *, key_fields: Sequence[str],
) -> bool:
    """先验证、写临时文件、复读校验，再原子替换；返回是否实际改变。"""
    materialized = validate_dictionary_rows(rows, headers=headers, key_fields=key_fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
    with staged.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)
        fh.flush()
        os.fsync(fh.fileno())
    load_dictionary_rows(staged, headers=headers, key_fields=key_fields)
    if path.exists() and path.read_bytes() == staged.read_bytes():
        staged.unlink()
        return False
    if path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(path, backup_dir / f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}.csv")
    try:
        os.replace(staged, path)
    except PermissionError as exc:
        pending = path.with_name(f"{path.stem}.pending-{datetime.now():%Y%m%d-%H%M%S}.csv")
        os.replace(staged, pending)
        raise DictionaryCommitPending(f"{path} is in use; staged update: {pending}") from exc
    return True


def product_source_hash(row: Mapping[str, object]) -> str:
    """计算商品官网事实字段的稳定哈希。

    仅包含品名、一级/二级类目和规格；价格、生命周期、中文派生字段都不
    属于字典失效条件。供增量标准化与字典重建共用，避免两套哈希口径漂移。
    """
    payload = "\x1f".join(_text(row.get(field)) for field in (
        "name_es_raw", "cat1_es", "cat2_es", "spec_es_raw",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 兼容内部旧调用；新代码应使用公开的 product_source_hash。
_source_hash = product_source_hash


def _row_signature(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(_text(row.get(header)) for header in PRODUCT_DICTIONARY_HEADERS if header != "updated_at")


def index_product_overrides(rows: Iterable[Mapping[str, object]]) -> dict[str, dict[str, str]]:
    """字段级人工覆盖：同 SKU 同字段不可重复，防止 CSV 行顺序改变结果。"""
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if _text(row.get("scope")).casefold() != "product":
            continue
        sku, field = _text(row.get("key")), _text(row.get("field"))
        if not sku or field not in PRODUCT_OVERRIDE_FIELDS:
            raise DictionaryValidationError(f"INVALID_PRODUCT_OVERRIDE: {sku}/{field}")
        if field in result.setdefault(sku, {}):
            raise DictionaryValidationError(f"DUPLICATE_PRODUCT_OVERRIDE: {sku}/{field}")
        result[sku][field] = _text(row.get("value"))
    return result


def index_model_translations(rows: Iterable[Mapping[str, object]]) -> dict[str, dict[str, str]]:
    """索引模型译文；来源哈希不一致时构建阶段不会套用过期结果。"""
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        sku, source_hash = _text(row.get("sku")), _text(row.get("source_hash"))
        if not sku or not source_hash:
            raise DictionaryValidationError(f"INVALID_MODEL_TRANSLATION: {sku}")
        if sku in result:
            raise DictionaryValidationError(f"DUPLICATE_MODEL_TRANSLATION: {sku}")
        result[sku] = {key: _text(value) for key, value in row.items()}
    return result


def _merged_skus(records: Mapping[str, Mapping[str, object]], existing: Mapping[str, Mapping[str, object]]) -> list[str]:
    return sorted(({_text(key) for key in records} | {_text(key) for key in existing}) - {""})


def build_product_dictionary(
    records: Mapping[str, Mapping[str, object]],
    existing: Mapping[str, Mapping[str, object]] | None = None,
    *, updated_at: str | None = None,
    category_mapping: Mapping[str, Mapping[str, object]] | None = None,
    product_overrides: Mapping[str, Mapping[str, object]] | None = None,
    standardized_seed: Mapping[str, Mapping[str, object]] | None = None,
    model_translations: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, str]]:
    """保留历史 SKU 与人工字段；源字段变动时标记需复核。"""
    existing, category_mapping, product_overrides, standardized_seed, model_translations = (
        existing or {}, category_mapping or {}, product_overrides or {}, standardized_seed or {}, model_translations or {},
    )
    stamp = updated_at or date.today().isoformat()
    result: list[dict[str, str]] = []
    for sku in _merged_skus(records, existing):
        rec, old = records.get(sku, {}), existing.get(sku, {})
        previous = {header: _text(old.get(header)) for header in PRODUCT_DICTIONARY_HEADERS}
        legacy_v1 = bool(old) and not previous["source_hash"]
        row = dict(previous)
        row["sku"] = sku
        for target, source in (
            ("canonical_id", "canonical_id"), ("name_es_raw", "name_es"),
            ("cat1_es", "cat1_es"), ("cat2_es", "cat2_es"),
            ("spec_es_raw", "spec_es"), ("source_first_seen", "first_seen"),
            ("source_last_seen", "last_seen"),
        ):
            value = _text(rec.get(source))
            if value:
                row[target] = value
        override_fields = set(product_overrides.get(sku, {}))
        # 旧版只有整行 locked/review_status，没有字段级覆盖记录；这类旧记录
        # 继续按整行保护。新记录只保护明确出现在 manual_overrides.csv 中的字段，
        # 防止人工改了中文品名后把类目、规格等无关字段一并冻结。
        legacy_row_protected = (
            not override_fields
            and (_is_locked(row.get("locked")) or _text(row.get("review_status")) == "HUMAN_REVIEWED")
        )
        locked = _is_locked(row.get("locked"))
        protected = legacy_row_protected
        if not protected:
            for target, source in (("name_zh_standard", "name_zh"), ("cat2_zh", "cat2_zh"), ("spec_zh_standard", "spec_zh")):
                value = _text(rec.get(source))
                if target not in override_fields and value:
                    row[target] = value
            # 早期归档中一级类目有时已是旧中文名、而西语类目为空；两者都只
            # 作为映射键，不把旧中文名直接当作新的固定类目写入。
            mapped = (
                category_mapping.get(normalize_category_key(row["cat1_es"]))
                or category_mapping.get(normalize_category_key(rec.get("cat1_zh")))
                or category_mapping.get(normalize_category_key(previous["cat1_zh"]))
                or {}
            )
            if "cat1_zh" not in override_fields:
                row["cat1_zh"] = _text(mapped.get("cat1_zh")) or _text(rec.get("cat1_zh")) or row["cat1_zh"]
            # 已有标准化表是中文字段的较高优先级来源；西语事实仍只来自官网/历史西语证据。
            seed = standardized_seed.get(sku, {})
            for target, source in (
                ("name_zh_standard", "name_zh"), ("brand_id", "brand_id"),
                ("cat2_zh", "cat2_zh"), ("spec_zh_standard", "spec_zh"),
            ):
                value = _text(seed.get(source))
                if target not in override_fields and value and value != "中文品名待人工核验":
                    row[target] = value
            if rec.get("_clear_spec_es") and "spec_zh_standard" not in override_fields:
                # 页面按钮/导航文字不是规格；清除旧版可能已经写入的派生规格，
                # 让本轮明确进入待复核，而不是继续展示过期译文。
                row["spec_es_raw"] = ""
                row["spec_zh_standard"] = ""
        row["source_hash"] = _source_hash(row)
        model = model_translations.get(sku, {})
        if not protected and _text(model.get("source_hash")) == row["source_hash"]:
            for field in ("name_zh_standard", "spec_zh_standard"):
                value = _text(model.get(field))
                if field not in override_fields and value:
                    row[field] = value
            row["translation_status"] = "MODEL_TRANSLATED"
            row["review_status"] = "NEEDS_REVIEW" if _text(model.get("quality_status")) == "NEEDS_REVIEW" else "UNREVIEWED"
        old_hash = previous["source_hash"]
        if old_hash and old_hash != row["source_hash"] and not protected:
            row["translation_status"] = row["review_status"] = "NEEDS_REVIEW"
        elif not _text(row.get("translation_status")) or (legacy_v1 and not protected):
            raw_status = _text(rec.get("translation_status"))
            row["translation_status"] = _LEGACY_TRANSLATION_STATUS.get(raw_status, raw_status)
            row["translation_status"] = row["translation_status"] or ("LEGACY_UNVERIFIED" if row["name_zh_standard"] else "UNTRANSLATED")
        if standardized_seed.get(sku) and not protected:
            seed_status = _text(standardized_seed[sku].get("seed_status"))
            if seed_status == "待人工核验":
                row["translation_status"] = row["review_status"] = "NEEDS_REVIEW"
            elif seed_status:
                row["translation_status"] = "MODEL_TRANSLATED"
        if locked:
            row["translation_status"], row["review_status"] = "LOCKED", "HUMAN_REVIEWED"
        row["locked"] = "1" if locked else "0"
        row["review_status"] = _text(row.get("review_status")) or "UNREVIEWED"
        for field, value in product_overrides.get(sku, {}).items():
            row[field] = _text(value)
        if product_overrides.get(sku):
            row["review_status"] = "HUMAN_REVIEWED"
            locked = _is_locked(row.get("locked"))
            if locked:
                row["translation_status"] = "LOCKED"
            elif "translation_status" not in product_overrides[sku]:
                row["translation_status"] = "HUMAN_REVIEWED"
            row["locked"] = "1" if locked else "0"
        row["updated_at"] = previous["updated_at"] if old and _row_signature(row) == _row_signature(previous) else stamp
        result.append({header: _text(row.get(header)) for header in PRODUCT_DICTIONARY_HEADERS})
    return result


def category_rows_from_products(
    products: Iterable[Mapping[str, object]], mapping: Mapping[str, Mapping[str, object]] | None = None,
    *, existing: Iterable[Mapping[str, object]] | None = None,
) -> list[dict[str, str]]:
    """合并新旧分类关系，重建时不清空人工二级分类、审核状态或备注。"""
    mapping = mapping or {}
    old_index = {(_text(row.get("cat1_es")), _text(row.get("cat2_es"))): dict(row) for row in (existing or [])}
    pairs = set(old_index)
    pairs.update((_text(row.get("cat1_es")), _text(row.get("cat2_es"))) for row in products if _text(row.get("cat1_es")) or _text(row.get("cat2_es")))
    result: list[dict[str, str]] = []
    for cat1, cat2 in sorted(pairs):
        row = {header: _text(old_index.get((cat1, cat2), {}).get(header)) for header in CATEGORY_DICTIONARY_HEADERS}
        row.update({"cat1_es": cat1, "cat2_es": cat2})
        mapped = mapping.get(normalize_category_key(cat1), {})
        if mapped:
            row["cat1_code"], row["cat1_zh"] = _text(mapped.get("cat1_code")), _text(mapped.get("cat1_zh"))
            row["review_status"] = row["review_status"] or "CAT1_CONFIRMED"
            row["notes"] = row["notes"] or "15 类固定映射"
        else:
            row["review_status"] = row["review_status"] or "UNREVIEWED"
            row["notes"] = row["notes"] or "首次由 Master 事实记录发现；等待分类字典确认"
        result.append(row)
    return result


def reconcile_brand_rows(
    products: Iterable[Mapping[str, object]], existing: Mapping[str, Mapping[str, object]],
) -> list[dict[str, str]]:
    """补齐商品字典已引用、但品牌字典尚无记录的品牌。

    这不是人工确认：新增项保留原品牌拼写，并明确标为待人工复核，防止
    `product_dictionary.brand_id` 形成悬空引用。
    """
    def identity_keys(row: Mapping[str, object]) -> set[str]:
        values = [_text(row.get("brand_id")), _text(row.get("canonical_name"))]
        values.extend(part.strip() for part in _text(row.get("aliases_es")).split("|") if part.strip())
        return {" ".join(value.casefold().split()) for value in values if value}

    def is_auto_reference(row: Mapping[str, object]) -> bool:
        return _text(row.get("confidence")) == "PRODUCT_DICTIONARY_REFERENCE"

    rows: dict[str, dict[str, str]] = {}
    seen_identities: set[str] = set()
    # 重跑构建时也要清掉上一轮自动补入的别名重复项。保留原有/人工记录，
    # 而不是保留自动补入的占位行。
    for brand_id, raw in sorted(existing.items(), key=lambda item: (is_auto_reference(item[1]), item[0])):
        if not _text(brand_id):
            continue
        row = {header: _text(raw.get(header)) for header in BRAND_DICTIONARY_HEADERS}
        keys = identity_keys(row)
        if keys & seen_identities and is_auto_reference(row):
            continue
        rows[_text(brand_id)] = row
        seen_identities.update(keys)
    for product in products:
        brand_id = _text(product.get("brand_id"))
        normalized_brand = " ".join(brand_id.casefold().split())
        if not brand_id or normalized_brand in seen_identities:
            continue
        row = {
            "brand_id": brand_id,
            "canonical_name": brand_id,
            "aliases_es": brand_id,
            "keep_original": "1",
            "is_action_brand": "0",
            "confidence": "PRODUCT_DICTIONARY_REFERENCE",
            "review_status": "NEEDS_HUMAN_REVIEW",
            "notes": "商品字典已引用；自动补齐以消除悬空品牌引用，待人工抽检。",
        }
        rows[brand_id] = row
        seen_identities.update(identity_keys(row))
    return [rows[brand_id] for brand_id in sorted(rows)]


def write_manifest(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)
