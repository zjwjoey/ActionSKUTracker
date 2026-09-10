from __future__ import annotations

"""Healthy historical metric baselines."""

from statistics import median
from typing import Any, Iterable, Mapping


def calculate_baselines(rows: Iterable[Mapping[str, Any]], *, metric_name: str | None = None) -> dict[str, dict[str, float | None]]:
    """Return previous/7-day/30-day medians, excluding unhealthy runs.

    Rows may be repository records or plain dictionaries.  A row named
    ``__collection_state`` with a degraded/blocked value marks its entire run
    ineligible, which prevents a bad run from contaminating future baselines.
    """
    values = [dict(row) for row in rows]
    # Repository queries are ordered, but callers may provide an arbitrary
    # list.  Baselines must be time based, never dependent on input order.
    values.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("run_id") or "")))
    unhealthy = {
        str(row.get("run_id")) for row in values
        if str(row.get("metric_name")) == "__collection_state"
        and str(row.get("metric_scope") or row.get("metric_value") or "").upper() in {"COLLECTION_DEGRADED", "COLLECTION_BLOCKED"}
    }
    groups: dict[str, list[float]] = {}
    for row in values:
        name = str(row.get("metric_name") or "")
        if not name or name == "__collection_state" or (metric_name and name != metric_name):
            continue
        if str(row.get("run_id")) in unhealthy:
            continue
        if str(row.get("gate_status") or "").upper() in {"UNAVAILABLE", "BLOCKED", "DEGRADED"}:
            continue
        value = row.get("metric_value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        groups.setdefault(name, []).append(number)
    result: dict[str, dict[str, float | None]] = {}
    for name, numbers in groups.items():
        numbers = list(numbers)
        recent = numbers[-7:]
        result[name] = {
            "previous": numbers[-1] if numbers else None,
            "median_7d": float(median(recent)) if recent else None,
            "median_30d": float(median(numbers[-30:])) if numbers else None,
        }
    return result
