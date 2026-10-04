"""Deterministic source completeness, cleaning and re-audit gates."""
from __future__ import annotations

import hashlib
import html
import re
from typing import Any, Iterable, Mapping

from ..services.hashing import localization_source_hash

REQUIRED_FIELDS = ("sku", "canonical_id", "name_es", "cat1_es", "product_url", "current_price", "status", "presence_source")
OPTIONAL_SOURCE_FIELDS = ("cat2_es", "spec_es", "desc_es", "details_es", "image_url")
TEXT_FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _protected_tokens(value: Any) -> tuple[str, ...]:
    return tuple(_NUMBER_RE.findall(str(value or "")))


def clean_source_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply only transport/UI cleanup; never infer or move facts."""
    result = dict(record)
    audits: list[dict[str, Any]] = []
    for field in TEXT_FIELDS:
        raw = result.get(field)
        if raw is None:
            continue
        text = html.unescape(str(raw)).replace("\r\n", "\n").replace("\r", "\n")
        cleaned = _CONTROL_RE.sub("", _TAG_RE.sub("", text))
        cleaned = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in cleaned.split("\n"))
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        changed = cleaned != str(raw)
        rules = []
        if html.unescape(str(raw)) != str(raw): rules.append("HTML_UNESCAPE")
        if _TAG_RE.search(str(raw)): rules.append("HTML_TAG_STRIP")
        if _CONTROL_RE.search(str(raw)): rules.append("CONTROL_CHAR_STRIP")
        if cleaned != str(raw): rules.append("WHITESPACE_NORMALIZE")
        before_tokens = _protected_tokens(raw)
        after_tokens = _protected_tokens(cleaned)
        blocked = before_tokens != after_tokens
        if blocked: rules.append("PROTECTED_FACT_CHANGED")
        if changed:
            result[field] = cleaned
        audits.append({"sku": str(record.get("sku") or record.get("official_sku") or ""), "field_name": field,
                           "raw_hash": _hash(raw), "clean_hash": _hash(cleaned), "cleaning_rules": rules,
                           "changed": changed, "review_required": blocked, "blocked": blocked})
    result["source_hash"] = localization_source_hash(result)
    return result, audits


def _field_status(record: Mapping[str, Any], field: str, *, required: bool) -> str:
    value = record.get(field)
    if value not in (None, ""):
        return "VALID"
    if required:
        return "MISSING_REQUIRED"
    detail_state = str(record.get("detail_status") or "").upper()
    if field in {"desc_es", "details_es"} and detail_state in {"PENDING", "DETAIL_PENDING", "INCOMPLETE", "ACCESS_INTERRUPTED", "403", "429", "CHALLENGE", "TIMEOUT"}:
        return "PENDING_DETAIL"
    return "SOURCE_EMPTY_VALID" if record.get(f"{field}_status") != "PENDING_DETAIL" else "PENDING_DETAIL"


def audit_source_records(
    records: Iterable[Mapping[str, Any]], *, authoritative_skus: Iterable[str] | None = None,
    expected_new_skus: Iterable[str] | None = None, expected_reappeared_skus: Iterable[str] | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    normalized = {str(row.get("sku") or row.get("official_sku") or "").strip() for row in rows}
    normalized.discard("")
    expected = {str(item).strip() for item in (authoritative_skus or ()) if str(item).strip()}
    new_expected = {str(item).strip() for item in (expected_new_skus or ()) if str(item).strip()}
    reappeared = {str(item).strip() for item in (expected_reappeared_skus or ()) if str(item).strip()}
    duplicate_count = len([str(row.get("sku") or row.get("official_sku") or "") for row in rows]) - len(normalized)
    required_issues: list[dict[str, Any]] = []
    field_statuses: list[dict[str, Any]] = []
    for row in rows:
        sku = str(row.get("sku") or row.get("official_sku") or "")
        for field in REQUIRED_FIELDS:
            status = _field_status(row, field, required=True)
            field_statuses.append({"sku": sku, "field": field, "status": status})
            if status == "MISSING_REQUIRED": required_issues.append({"sku": sku, "field": field})
        for field in OPTIONAL_SOURCE_FIELDS:
            field_statuses.append({"sku": sku, "field": field, "status": _field_status(row, field, required=False)})
    missing = sorted(expected - normalized)
    extra = sorted(normalized - expected) if expected else []
    missing_new = sorted(new_expected - normalized)
    extra_new = sorted(normalized & new_expected - new_expected) if new_expected else []
    missing_reappeared = sorted(reappeared - normalized)
    blocked = bool(required_issues or missing or missing_new or missing_reappeared or duplicate_count)
    return {
        "status": "FAIL" if blocked else "PASS",
        "source_ready": not blocked,
        "normalized_skus": sorted(normalized),
        "authoritative_skus": sorted(expected),
        "source_sku_completeness": "PASS" if not missing and not extra else "FAIL",
        "new_sku_completeness": "PASS" if not missing_new and not extra_new else "FAIL",
        "new_sku_expected_count": len(new_expected), "new_sku_normalized_count": len(normalized & new_expected),
        "new_sku_missing_count": len(missing_new), "new_sku_extra_count": len(extra_new),
        "missing_new_skus": missing_new, "missing_reappeared_skus": missing_reappeared,
        "missing_required": required_issues, "missing_skus": missing, "extra_skus": extra,
        "duplicate_sku_count": duplicate_count, "field_statuses": field_statuses,
    }


def source_clean_and_reaudit(records: Iterable[Mapping[str, Any]], **audit_kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    cleaning: list[dict[str, Any]] = []
    for record in records:
        row, audit = clean_source_record(record)
        cleaned.append(row); cleaning.extend(audit)
    result = audit_source_records(cleaned, **audit_kwargs)
    blocked = [item for item in cleaning if item.get("blocked")]
    result["cleaning_blocked_count"] = len(blocked)
    if blocked:
        result["source_ready"] = False; result["status"] = "FAIL"
    return cleaned, cleaning, result
