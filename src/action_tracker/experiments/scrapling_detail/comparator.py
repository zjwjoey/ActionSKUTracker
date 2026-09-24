"""Auditable field-level shadow comparison for Legacy and Scrapling results."""
from __future__ import annotations

import unicodedata
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


COMPARE_FIELDS = (
    "sku", "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es",
    "details_es", "product_url", "image_url",
)
_SPACE = re.compile(r"\s+")


def _normalized(value: Any, field: str) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\xa0", " ")
    text = _SPACE.sub(" ", text).strip().casefold()
    if field in {"product_url", "image_url"} and text:
        parts = urlsplit(text)
        text = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, ""))
    return text


def compare_fields(
    legacy: dict[str, Any],
    scrapling: dict[str, Any],
    *,
    legacy_valid: bool,
    scrapling_valid: bool,
    legacy_invalid_fields: set[str] | None = None,
    scrapling_invalid_fields: set[str] | None = None,
    global_invalid: bool = False,
) -> list[dict[str, Any]]:
    """Return one comparison record per field; never coerce one parser's value."""
    legacy_invalid_fields = legacy_invalid_fields or set()
    scrapling_invalid_fields = scrapling_invalid_fields or set()
    rows: list[dict[str, Any]] = []
    for field in COMPARE_FIELDS:
        left, right = legacy.get(field), scrapling.get(field)
        if global_invalid or field in legacy_invalid_fields or field in scrapling_invalid_fields:
            result = "INVALID"
        elif not left and not right:
            result = "EXACT_MATCH"
        elif not left:
            result = "SCRAPLING_ONLY"
        elif not right:
            result = "LEGACY_ONLY"
        elif left == right:
            result = "EXACT_MATCH"
        elif _normalized(left, field) == _normalized(right, field):
            result = "NORMALIZED_MATCH"
        else:
            result = "DIFFERENT"
        rows.append({
            "field": field,
            "legacy_value": left or "",
            "scrapling_value": right or "",
            "result": result,
            "legacy_valid": bool(legacy_valid),
            "scrapling_valid": bool(scrapling_valid),
        })
    return rows


def final_verdict(comparisons: list[dict[str, Any]], *, legacy_valid: bool, scrapling_valid: bool) -> str:
    if not legacy_valid or not scrapling_valid or any(row["result"] == "INVALID" for row in comparisons):
        return "INVALID"
    if any(row["result"] in {"DIFFERENT", "LEGACY_ONLY", "SCRAPLING_ONLY"} for row in comparisons):
        return "DIFFERENT"
    if any(row["result"] == "NORMALIZED_MATCH" for row in comparisons):
        return "NORMALIZED_MATCH"
    return "EXACT_MATCH"
