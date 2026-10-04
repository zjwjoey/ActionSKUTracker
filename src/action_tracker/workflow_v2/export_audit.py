"""Offline bilingual export audit and parity gate."""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

ES_REQUIRED = ("sku", "name_es", "cat1_es", "product_url", "current_price", "status")
ZH_FIELDS = ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh")
IDENTITY_FIELDS = ("sku", "current_price", "original_price", "unit_price", "product_url", "status", "promotion_active", "action_new_badge")


def audit_es(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    skus = [str(row.get("sku") or row.get("official_sku") or "").strip() for row in values]
    missing = [{"sku": sku, "field": field} for row, sku in zip(values, skus) for field in ES_REQUIRED if row.get(field) in (None, "")]
    duplicates = [sku for sku, count in Counter(skus).items() if sku and count > 1]
    return {"status": "PASS" if not missing and not duplicates and all(skus) else "FAIL", "rows": len(values),
            "missing_required": missing, "duplicate_skus": duplicates, "sku_set": sorted(set(skus))}


def audit_zh(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    issues: list[dict[str, Any]] = []
    for row in values:
        sku = str(row.get("sku") or row.get("official_sku") or "")
        if str(row.get("translation_status") or "").upper() not in {"APPROVED", "AUTO_VALIDATED", "HUMAN_APPROVED", "PASS"}:
            issues.append({"sku": sku, "code": "TRANSLATION_NOT_READY", "status": row.get("translation_status")})
        for field in ZH_FIELDS:
            if row.get(field) in (None, "") and str(row.get(field.replace("_zh", "_es")) or ""):
                issues.append({"sku": sku, "field": field, "code": "MISSING_ZH"})
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
