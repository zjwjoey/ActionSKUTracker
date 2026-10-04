"""Offline comparison helper for old-chain and Workflow V2 fixtures."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def compare_shadow(legacy: Iterable[Mapping[str, Any]], workflow_v2: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    def index(rows):
        return {str(row.get("sku") or row.get("official_sku") or ""): dict(row) for row in rows}
    old = index(legacy); new = index(workflow_v2)
    fields = ("current_price", "original_price", "status", "cat1_es", "spec_es", "desc_es", "details_es")
    drift = []
    for sku in sorted(set(old) | set(new)):
        if sku not in old or sku not in new:
            drift.append({"sku": sku, "code": "SKU_SET_DRIFT"}); continue
        for field in fields:
            if old[sku].get(field) != new[sku].get(field): drift.append({"sku": sku, "field": field, "code": "FACT_DRIFT"})
    return {"status": "PASS" if not drift else "FAIL", "legacy_skus": len(old), "workflow_v2_skus": len(new), "drift": drift}
