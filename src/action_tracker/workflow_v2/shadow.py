"""Offline comparison helper for old-chain and Workflow V2 fixtures."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def compare_shadow(legacy: Iterable[Mapping[str, Any]], workflow_v2: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    def index(rows):
        return {str(row.get("sku") or row.get("official_sku") or ""): dict(row) for row in rows}
    old = index(legacy); new = index(workflow_v2)
    fields = ("current_price", "original_price", "status", "cat1_es", "spec_es", "desc_es", "details_es", "product_url")
    drift = []
    for sku in sorted(set(old) | set(new)):
        if sku not in old or sku not in new:
            drift.append({"sku": sku, "code": "SKU_SET_DRIFT"}); continue
        for field in fields:
            if old[sku].get(field) != new[sku].get(field): drift.append({"sku": sku, "field": field, "code": "FACT_DRIFT"})
    return {"status": "PASS" if not drift else "FAIL", "legacy_skus": len(old), "workflow_v2_skus": len(new), "drift": drift}


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
