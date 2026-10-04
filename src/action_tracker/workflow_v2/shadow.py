"""Offline comparison helper for old-chain and Workflow V2 fixtures."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def compare_shadow(legacy: Iterable[Mapping[str, Any]], workflow_v2: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    def index(rows):
        return {str(row.get("sku") or row.get("official_sku") or ""): dict(row) for row in rows}
    old = index(legacy); new = index(workflow_v2)
    fields = (
        "canonical_id", "current_price", "original_price", "status", "presence_source",
        "name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es", "product_url",
    )
    drift = []
    for sku in sorted(set(old) | set(new)):
        if sku not in old or sku not in new:
            drift.append({"sku": sku, "code": "SKU_SET_DRIFT"}); continue
        for field in fields:
            if old[sku].get(field) != new[sku].get(field): drift.append({"sku": sku, "field": field, "code": "FACT_DRIFT"})
    return {"status": "PASS" if not drift else "FAIL", "legacy_skus": len(old), "workflow_v2_skus": len(new), "drift": drift}


def audit_records_preflight(records: Iterable[Mapping[str, Any]], *, authoritative_skus: Iterable[str] | None = None) -> dict[str, Any]:
    """Run the read-only daily-run Shadow Comparison preflight.

    The legacy side is the exact in-memory record produced by the existing
    daily chain.  Workflow V2's source stage is represented by its deterministic
    cleanup and re-audit output.  No provider, browser, database or export is
    touched.  Any fact drift or source-audit failure is returned as a blocker
    for the caller's publish gate.
    """
    from .source_audit import source_clean_and_reaudit

    legacy = [dict(row) for row in records]
    expected = list(authoritative_skus or [row.get("sku") or row.get("official_sku") for row in legacy])
    cleaned, cleaning, source_audit = source_clean_and_reaudit(legacy, authoritative_skus=expected)
    comparison = compare_shadow(legacy, cleaned)
    blocked = source_audit.get("status") != "PASS" or comparison.get("status") != "PASS"
    return {
        "status": "BLOCKED" if blocked else "PASS",
        "legacy_records": len(legacy),
        "workflow_v2_records": len(cleaned),
        "source_audit": {
            "status": source_audit.get("status"),
            "missing_required": source_audit.get("missing_required", []),
            "missing_skus": source_audit.get("missing_skus", []),
            "duplicate_sku_count": source_audit.get("duplicate_sku_count", 0),
            "cleaning_blocked_count": source_audit.get("cleaning_blocked_count", 0),
        },
        "comparison": comparison,
        "cleaned_fields": sum(1 for item in cleaning if item.get("changed")),
        "read_only": True,
    }


def compare_shadow_payloads(legacy: Mapping[str, Any], workflow_v2: Mapping[str, Any]) -> dict[str, Any]:
    """Compare persisted old/V2 fixture payloads without touching a database."""
    row_result = compare_shadow(legacy.get("records") or legacy.get("rows") or [], workflow_v2.get("records") or workflow_v2.get("rows") or [])
    checks: dict[str, Any] = {"records": row_result}
    for key in ("new_skus", "reappeared_skus", "missing_skus", "offline_skus", "price_events", "badge_events"):
        old_value = legacy.get(key, [])
        new_value = workflow_v2.get(key, [])
        old_norm = sorted(str(item.get("sku") or item.get("official_sku") or item) if isinstance(item, Mapping) else str(item) for item in old_value)
        new_norm = sorted(str(item.get("sku") or item.get("official_sku") or item) if isinstance(item, Mapping) else str(item) for item in new_value)
        checks[key] = {"status": "PASS" if old_norm == new_norm else "FAIL", "legacy": old_norm, "workflow_v2": new_norm}
    failed = [key for key, value in checks.items() if value.get("status") != "PASS"]
    return {"status": "PASS" if not failed else "FAIL", "failed_checks": failed, "checks": checks}
