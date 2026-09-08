"""Hard release gate for SQLite-backed ES/ZH exports.

The gate is intentionally independent from workbook formatting.  It compares
the exported projection with the same source records and checks field-level
localization provenance before a PRIMARY database export is published.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable


APPROVED_ZH_STATUSES = frozenset({
    "APPROVED", "HUMAN_APPROVED", "CONFIRMED", "LOCKED", "HUMAN_REVIEWED",
})
_SPANISH_RESIDUAL_RE = re.compile(
    r"\b(?:para|con|del|de|la|el|los|las|una|uno|producto|descripción|descripcion|detalles|categoría|categoria)\b",
    re.IGNORECASE,
)
_HTML_RE = re.compile(r"<\/?[a-z][^>]*>", re.IGNORECASE)


class ReleaseGateError(ValueError):
    """A formal release has one or more blocking gate failures."""


def evaluate_release_gate(
    records: Iterable[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
    *,
    language: str,
    strict: bool,
    explicit_exceptions: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Return machine-readable gate counters; optionally raise on any failure."""
    source_by_sku = {str(row.get("sku") or "").strip(): row for row in records}
    output_by_sku = {str(row.get("编号") or "").strip(): row for row in rows}
    issues: list[dict[str, Any]] = []
    _issue_if(issues, "SKU_SET_MISMATCH", set(source_by_sku) != set(output_by_sku),
              expected=len(source_by_sku), actual=len(output_by_sku))

    for sku, source in source_by_sku.items():
        out = output_by_sku.get(sku)
        if out is None:
            continue
        _compare_shared_fact(issues, sku, source, out)
        if language == "es":
            _check_spanish_fields(issues, sku, out)
        elif language == "zh":
            _check_zh_provenance(issues, sku, source, out)

    exceptions = {
        (str(item.get("sku") or ""), str(item.get("field") or ""), str(item.get("code") or ""))
        for item in explicit_exceptions
        if str(item.get("status") or "") == "EXPLICIT_EXCEPTION"
    }
    remaining = [item for item in issues if (str(item.get("sku") or ""), str(item.get("field") or ""), str(item.get("code") or "")) not in exceptions]
    counts = Counter(str(item["code"]) for item in remaining)
    result = {
        "strict": bool(strict),
        "passed": not remaining,
        "issues": remaining,
        "explicit_exception_count": len(issues) - len(remaining),
        "counts": {
            "SKU_SET_MISMATCH": counts.get("SKU_SET_MISMATCH", 0),
            "FACT_MISMATCH": counts.get("FACT_MISMATCH", 0),
            "UNDECLARED_DISPLAY_MISMATCH": counts.get("UNDECLARED_DISPLAY_MISMATCH", 0),
            "UNAPPROVED_ZH": counts.get("UNAPPROVED_ZH", 0),
            "STALE_ZH": counts.get("STALE_ZH", 0),
            "SPANISH_RESIDUAL": counts.get("SPANISH_RESIDUAL", 0),
            "SOURCE_HASH_MISMATCH": counts.get("SOURCE_HASH_MISMATCH", 0),
        },
    }
    if strict and remaining:
        codes = ",".join(f"{code}={count}" for code, count in sorted(counts.items()))
        raise ReleaseGateError(f"EXPORT_RELEASE_GATE_BLOCKED:{codes}")
    return result


def _compare_shared_fact(issues: list[dict[str, Any]], sku: str, source: dict[str, Any], out: dict[str, Any]) -> None:
    checks = (
        ("折后价", source.get("current_price"), out.get("折后价"), "FACT_MISMATCH"),
        ("图片链接", _text(source.get("image_url")), _text(out.get("图片链接")), "FACT_MISMATCH"),
        ("商品链接", _text(source.get("product_url")), _text(out.get("商品链接")), "FACT_MISMATCH"),
    )
    for field, expected, actual, code in checks:
        if expected != actual:
            _issue(issues, code, sku, field, expected=expected, actual=actual)
    original = _number(source.get("original_price"))
    expected_original = original if original is not None and original > (_number(source.get("current_price")) or 0) else None
    actual_original = _number(out.get("原价"))
    if expected_original != actual_original:
        _issue(issues, "UNDECLARED_DISPLAY_MISMATCH", sku, "原价", expected=expected_original, actual=actual_original)


def _check_spanish_fields(issues: list[dict[str, Any]], sku: str, out: dict[str, Any]) -> None:
    for field in ("标题", "分类1", "分类2", "规格", "单价", "描述", "产品详情", "备注"):
        value = _text(out.get(field))
        if not value:
            continue
        if "null" in value.casefold() or "undefined" in value.casefold() or _HTML_RE.search(value):
            _issue(issues, "SPANISH_RESIDUAL", sku, field, actual=value[:160])


def _check_zh_provenance(issues: list[dict[str, Any]], sku: str, source: dict[str, Any], out: dict[str, Any]) -> None:
    field_map = {"标题": "name", "分类1": "cat1", "分类2": "cat2", "规格": "spec", "描述": "description", "产品详情": "details"}
    provenance = source.get("_localization_provenance") or {}
    source_hash = _text(source.get("source_hash"))
    for output_field, field_name in field_map.items():
        meta = provenance.get(field_name) or {}
        status = _text(meta.get("review_status")).upper()
        value = _text(out.get(output_field))
        if status not in APPROVED_ZH_STATUSES:
            _issue(issues, "UNAPPROVED_ZH", sku, field_name, status=status or "MISSING")
        field_hash = _text(meta.get("source_hash"))
        if not field_hash or (source_hash and field_hash != source_hash):
            _issue(issues, "STALE_ZH", sku, field_name, source_hash=source_hash, field_hash=field_hash)
            _issue(issues, "SOURCE_HASH_MISMATCH", sku, field_name, source_hash=source_hash, field_hash=field_hash)
        if value and _SPANISH_RESIDUAL_RE.search(value):
            _issue(issues, "SPANISH_RESIDUAL", sku, field_name, actual=value[:160])


def _issue_if(issues: list[dict[str, Any]], code: str, condition: bool, **payload: Any) -> None:
    if condition:
        _issue(issues, code, "", "", **payload)


def _issue(issues: list[dict[str, Any]], code: str, sku: str, field: str, **payload: Any) -> None:
    issues.append({"code": code, "sku": sku, "field": field, **payload})


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
