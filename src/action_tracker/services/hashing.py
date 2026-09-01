"""内容/价格哈希：用于"没变就不处理"的去重判断（规范 §30/§31）。"""
from __future__ import annotations

import hashlib
from typing import Any


def normalize_hash(value: Any) -> str | None:
    """Return one canonical representation for an optional hash value.

    Persistence boundaries receive values from CSV, SQLite and JSON, where an
    absent hash may be represented as ``None``, an empty string, or a textual
    null.  Those values all mean *unknown*, not a real digest.  Keeping this
    normalization in one place prevents false matches while allowing callers
    to distinguish an unknown hash (``None``) from a computed digest.
    """
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


def price_hash(rec: dict[str, Any]) -> str:
    """价格+促销+折扣哈希。不变则不生成价格任务。"""
    return _h(
        rec.get("current_price"),
        rec.get("original_price"),
        rec.get("unit_price"),
        rec.get("promotion_active"),
        rec.get("discount"),
    )
