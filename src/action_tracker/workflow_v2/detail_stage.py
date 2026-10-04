"""Adapter for the existing detail-state contract.

Workflow V2 does not create a second detail crawler.  This module only turns
the existing ``product_detail_state`` freshness records and extraction
metadata into a field-level plan; a production detail retry/apply adapter can
be injected later without changing the state machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Iterable

from .detail_state import detail_is_stale
from ..localization.contracts import source_hash


PENDING_DETAIL = {"PENDING", "DETAIL_PENDING", "INCOMPLETE", "ACCESS_INTERRUPTED", "403", "429", "CHALLENGE", "TIMEOUT"}


def plan_detail_refresh(records: Iterable[Mapping[str, Any]], *, db_path: Path | None = None,
                        max_age_days: int = 7) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for row in records:
        sku = str(row.get("sku") or row.get("official_sku") or "").strip()
        if not sku:
            continue
        state = str(row.get("detail_status") or "").upper()
        reason = None
        if state in {"NEW", "REAPPEARED", "MISSING_DETAIL", "DETAIL_SOURCE_CHANGED", "MANUAL"}:
            reason = state
        elif any(not str(row.get(field) or "").strip() for field in ("desc_es", "details_es")):
            reason = "MISSING_REQUIRED_DETAIL"
        elif db_path and detail_is_stale(db_path, sku, source_hash=source_hash(row), max_age_days=max_age_days):
            reason = "DETAIL_EXPIRED"
        if reason:
            planned.append({"sku": sku, "reason": reason, "source_hash": source_hash(row), "status": "PLANNED"})
    return planned


def classify_detail_outcome(record: Mapping[str, Any]) -> str:
    state = str(record.get("detail_status") or "").upper()
    return "DETAIL_PENDING" if state in PENDING_DETAIL else "COMPLETE"
