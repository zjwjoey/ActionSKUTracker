from __future__ import annotations

"""Collection run metric extraction.

The collector remains the owner of facts.  This module only consumes a run
report/evidence payload and records what is known; unavailable values are
explicitly represented as ``None`` rather than fabricated.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from ..contracts import CollectionMetric

BASE_METRICS = (
    "sitemap_unique", "listing_unique", "current_valid", "nuevo_count", "promotion_count",
    "price_coverage", "image_coverage", "cat1_coverage", "cat2_coverage", "spec_coverage",
    "description_coverage", "details_coverage", "duplicate_rate", "parse_error_rate",
    "detail_failure_rate", "original_price_equals_current_ratio", "ui_contamination_rate",
    "html_contamination_rate", "listing_page_count", "successful_category_count",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(payload: Mapping[str, Any], name: str) -> tuple[float | None, float | None, float | None]:
    value = payload.get(name)
    if isinstance(value, Mapping):
        numerator = _number(value.get("numerator"))
        denominator = _number(value.get("denominator"))
        if value.get("value") is not None:
            return _number(value.get("value")), numerator, denominator
        if numerator is not None and denominator not in (None, 0):
            return numerator / denominator, numerator, denominator
        return None, numerator, denominator
    return _number(value), None, None


def _source_value(payload: Mapping[str, Any], name: str) -> Any:
    aliases = {
        "sitemap_unique": ("sitemap_unique", "sitemap_sku_count"),
        "listing_unique": ("listing_unique", "listing_sku_count"),
        "current_valid": ("current_valid", "today_sku", "union_present_sku_count"),
        "nuevo_count": ("nuevo_count", "new_badge_count", "nuevo"),
        "promotion_count": ("promotion_count", "promo_count", "promotion"),
        "listing_page_count": ("listing_page_count", "completed_pages"),
        "successful_category_count": ("successful_category_count",),
    }
    for key in aliases.get(name, (name,)):
        if key in payload:
            return payload[key]
    coverage = payload.get("coverage")
    if isinstance(coverage, Mapping) and name in coverage:
        return coverage[name]
    return None


def build_collection_metrics(run_id: str, payload: Mapping[str, Any]) -> list[CollectionMetric]:
    """Convert a run report into deterministic metric records."""
    metrics: list[CollectionMetric] = []
    created = _now()
    category_coverage = payload.get("category_coverage") or payload.get("categories") or {}
    for name in BASE_METRICS:
        value = _source_value(payload, name)
        numerator = denominator = None
        if name.endswith("_coverage") or name in {"duplicate_rate", "parse_error_rate", "detail_failure_rate"}:
            value, numerator, denominator = _ratio(payload, name)
        if name == "successful_category_count" and value is None and isinstance(category_coverage, Mapping):
            value = sum(1 for item in category_coverage.values() if item is True or str(item).upper() in {"PASS", "OK", "SUCCESS", "COMPLETE"})
        metrics.append(CollectionMetric(
            run_id=run_id, metric_name=name, metric_scope=None,
            metric_value=value, numerator=numerator, denominator=denominator,
            gate_status="UNAVAILABLE" if value is None else "OK", evidence={"source": "run_evidence"}, created_at=created,
        ))
    if isinstance(category_coverage, Mapping):
        for category, value in sorted(category_coverage.items(), key=lambda item: str(item[0])):
            numeric = 1.0 if value is True or str(value).upper() in {"PASS", "OK", "SUCCESS", "COMPLETE"} else 0.0
            metrics.append(CollectionMetric(
                run_id=run_id, metric_name="category_success", metric_scope=str(category),
                metric_value=numeric, numerator=numeric, denominator=1.0,
                gate_status="OK" if numeric else "BLOCKED", evidence={"source": "category_coverage"}, created_at=created,
            ))
    # This marker lets baseline.py exclude a degraded/blocked run without a
    # second state table; it is metadata, not a product fact.
    return metrics


def load_run_payload(db_path: Path, run_id: str) -> dict[str, Any]:
    """Read one run's evidence without changing the database."""
    with sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT evidence_json FROM run_evidence WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"RUN_EVIDENCE_NOT_FOUND:{run_id}")
        try:
            value = json.loads(row[0] or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"RUN_EVIDENCE_INVALID:{run_id}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"RUN_EVIDENCE_NOT_OBJECT:{run_id}")
        return value
