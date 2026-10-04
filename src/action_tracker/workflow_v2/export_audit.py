"""Offline bilingual export audit and parity gate."""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


ES_REQUIRED = ("sku", "name_es", "cat1_es", "product_url", "current_price", "status")
ZH_FIELDS = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")
IDENTITY_FIELDS = ("sku", "current_price", "original_price", "unit_price", "product_url", "status", "promotion_active", "action_new_badge")


def audit_es(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    skus = [str(row.get("sku") or row.get("official_sku") or "").strip() for row in values]
    missing = [{"sku": sku, "field": field} for row, sku in zip(values, skus) for field in ES_REQUIRED if row.get(field) in (None, "")]
    issues: list[dict[str, Any]] = []
    for row, sku in zip(values, skus):
        if str(row.get("status") or "").upper() != "CURRENT": issues.append({"sku": sku, "code": "INVALID_STATUS"})
        try:
            if float(row.get("current_price")) <= 0: issues.append({"sku": sku, "code": "INVALID_PRICE"})
        except (TypeError, ValueError): issues.append({"sku": sku, "code": "INVALID_PRICE"})
        url = str(row.get("product_url") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc: issues.append({"sku": sku, "code": "INVALID_URL"})
        for field in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
            value = str(row.get(field) or "")
            if "\x00" in value or re.search(r"<[^>]+>", value): issues.append({"sku": sku, "field": field, "code": "SOURCE_CONTAMINATION"})
        if not row.get("presence_source"): issues.append({"sku": sku, "code": "SOURCE_PROVENANCE_MISSING"})
    duplicates = [sku for sku, count in Counter(skus).items() if sku and count > 1]
    return {"status": "PASS" if not missing and not duplicates and not issues and all(skus) else "FAIL", "rows": len(values),
            "missing_required": missing, "duplicate_skus": duplicates, "issues": issues, "sku_set": sorted(set(skus))}


def audit_zh(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    issues: list[dict[str, Any]] = []
    from ..localization.contracts import SourceFacts
    from ..localization.qa import audit_translation
    for row in values:
        sku = str(row.get("sku") or row.get("official_sku") or "")
        if str(row.get("translation_status") or "").upper() not in {"APPROVED", "AUTO_VALIDATED", "HUMAN_APPROVED", "PASS"}:
            issues.append({"sku": sku, "code": "TRANSLATION_NOT_READY", "status": row.get("translation_status")})
        for field in ZH_FIELDS:
            if row.get(field) in (None, "") and str(row.get(field.replace("_zh", "_es")) or ""):
                issues.append({"sku": sku, "field": field, "code": "MISSING_ZH"})
            value = str(row.get(field) or "")
            if "\x00" in value or re.search(r"<[^>]+>", value):
                issues.append({"sku": sku, "field": field, "code": "ZH_GARBLED_OR_HTML"})
        provenance = row.get("zh_field_provenance") or {}
        if provenance and any(str((provenance.get(field.replace("_zh", "")) or {}).get("freshness_status") or "").upper() == "STALE" for field in ZH_FIELDS):
            issues.append({"sku": sku, "code": "STALE_TRANSLATION"})
        if str(row.get("translation_freshness") or "").upper() == "STALE":
            issues.append({"sku": sku, "code": "STALE_TRANSLATION"})
        source_hash = str(row.get("source_hash") or "")
        translation_hash = str(row.get("translation_source_hash") or "")
        if source_hash and translation_hash and source_hash != translation_hash:
            issues.append({"sku": sku, "code": "SOURCE_HASH_MISMATCH"})
        source = SourceFacts.from_record(row)
        targets = {
            "name": row.get("name_zh"), "cat1": row.get("cat1_zh"),
            "cat2": row.get("cat2_zh"), "spec": row.get("spec_zh"),
            "description": row.get("desc_zh"), "details": row.get("details_zh"),
        }
        findings = audit_translation(source, targets, tuple(targets))
        issues.extend({"sku": sku, "field": finding.field_name, "code": finding.rule_id,
                       "evidence": dict(finding.evidence)} for finding in findings if finding.blocking)
    return {"status": "PASS" if not issues else "FAIL", "rows": len(values), "issues": issues, "sku_set": sorted({str(row.get("sku") or row.get("official_sku") or "") for row in values})}


def audit_parity(es_rows: Iterable[Mapping[str, Any]], zh_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    es = [dict(row) for row in es_rows]; zh = [dict(row) for row in zh_rows]
    es_by = {str(row.get("sku") or row.get("official_sku") or ""): row for row in es}
    zh_by = {str(row.get("sku") or row.get("official_sku") or ""): row for row in zh}
    issues: list[dict[str, Any]] = []
    if list(es_by) != list(zh_by):
        issues.append({"code": "SKU_SET_MISMATCH", "es_only": sorted(set(es_by) - set(zh_by)), "zh_only": sorted(set(zh_by) - set(es_by))})
    for sku in sorted(set(es_by) & set(zh_by)):
        for field in IDENTITY_FIELDS:
            if es_by[sku].get(field) != zh_by[sku].get(field):
                issues.append({"sku": sku, "field": field, "code": "FACT_MISMATCH"})
    return {"status": "PASS" if not issues and len(es) == len(zh) else "FAIL", "es_rows": len(es), "zh_rows": len(zh), "issues": issues}


def export_readiness(*, source_ready: bool, fact_committed: bool, translation_ready: bool, es_audit: Mapping[str, Any], zh_audit: Mapping[str, Any], parity: Mapping[str, Any]) -> dict[str, Any]:
    checks = {"SOURCE_READY": source_ready, "FACT_COMMITTED": fact_committed, "TRANSLATION_READY": translation_ready,
              "ES_EXPORT_AUDIT": es_audit.get("status") == "PASS", "ZH_EXPORT_AUDIT": zh_audit.get("status") == "PASS", "ES_ZH_PARITY": parity.get("status") == "PASS"}
    return {"export_ready": all(checks.values()), "checks": checks, "status": "PASS" if all(checks.values()) else "BLOCKED"}
