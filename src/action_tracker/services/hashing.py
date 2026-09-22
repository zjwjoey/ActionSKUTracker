"""内容/价格哈希：用于"没变就不处理"的去重判断（规范 §30/§31）。"""
from __future__ import annotations

import hashlib
from typing import Any


def normalize_hash(value: Any) -> str | None:
    """Normalize optional persisted hashes without treating blank as a digest."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"none", "null", "nan", "n/a"}:
        return None
    return text


def _h(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"\x00")
        else:
            h.update(str(p).strip().encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()


def content_hash(rec: dict[str, Any]) -> str:
    """基于西语事实字段的内容哈希。

    只包含西语/事实信息与稳定标识；中文与价格属于派生/变化字段，不参与。
    变更这些字段即代表商品内容确实变化。
    """
    return _h(
        rec.get("name_es"),
        rec.get("cat1_es"),
        rec.get("cat2_es"),
        rec.get("spec_es"),
        rec.get("desc_es"),
        rec.get("details_es"),
        rec.get("product_url"),
        rec.get("image_url"),
    )


def localization_source_hash(rec: dict[str, Any]) -> str:
    """Canonical hash for the six Spanish localization fact fields.

    This is deliberately separate from ``content_hash``: the latter is the
    product/update hash and also includes stable URLs, while this hash binds
    only the source text that can invalidate a Chinese localization.
    """
    return _h(
        rec.get("name_es"), rec.get("cat1_es"), rec.get("cat2_es"),
        rec.get("spec_es"), rec.get("desc_es"), rec.get("details_es"),
    )


_LOCALIZATION_FIELD_TO_SOURCE = {
    "name": "name_es",
    "cat1": "cat1_es",
    "cat2": "cat2_es",
    "spec": "spec_es",
    "description": "desc_es",
    "details": "details_es",
}


def _field_source_value(rec: dict[str, Any], field: str) -> Any:
    try:
        source_key = _LOCALIZATION_FIELD_TO_SOURCE[field]
    except KeyError as exc:
        raise ValueError(f"UNKNOWN_LOCALIZATION_FIELD:{field}") from exc
    if source_key in rec:
        return rec.get(source_key)
    if field == "description" and "description_es" in rec:
        return rec.get("description_es")
    return rec.get(field)


def localization_field_source_hash(rec: dict[str, Any], field: str) -> str:
    """Hash only the Spanish source owned by one localized field.

    The legacy six-field hash remains available for old records and patch
    identity. New field provenance must use this narrower hash.
    """
    return _h(_field_source_value(rec, field))


def localization_field_source_hashes(rec: dict[str, Any]) -> dict[str, str]:
    return {field: localization_field_source_hash(rec, field) for field in _LOCALIZATION_FIELD_TO_SOURCE}


def field_source_hash(rec: dict[str, Any], field: str) -> str:
    return localization_field_source_hash(rec, field)


def price_hash(rec: dict[str, Any]) -> str:
    """价格+促销+折扣哈希。不变则不生成价格任务。"""
    return _h(
        rec.get("current_price"),
        rec.get("original_price"),
        rec.get("unit_price"),
        rec.get("promotion_active"),
        rec.get("discount"),
    )
