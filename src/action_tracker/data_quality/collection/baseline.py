from __future__ import annotations

"""Healthy calendar-day metric baselines."""

from datetime import date, datetime, timedelta
import json
from statistics import median
from typing import Any, Iterable, Mapping


def _date_value(row: Mapping[str, Any]) -> date | None:
    """Read the formal observation date, with a legacy compatibility fallback."""
    raw = row.get("observation_date") or row.get("run_date")
    if raw is None:
        evidence = row.get("evidence_json") or row.get("evidence")
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (TypeError, ValueError):
                evidence = None
        if isinstance(evidence, Mapping):
            raw = evidence.get("observation_date") or evidence.get("run_date")
    if raw is None:
        # V1 rows created before observation_date was formalized can still be
        # read. New collection writes always carry observation_date.
        raw = row.get("created_at")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _evidence_object(row: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = row.get("evidence_json") or row.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            return {}
    return evidence if isinstance(evidence, Mapping) else {}


def _run_is_baseline_ineligible(row: Mapping[str, Any]) -> bool:
    """Apply V1 eligibility while preserving legacy rows without evidence."""
    if str(row.get("metric_name") or "") != "__collection_state":
        return False
    state = str(row.get("metric_scope") or row.get("metric_value") or row.get("gate_status") or "").upper()
    if state in {"COLLECTION_DEGRADED", "COLLECTION_BLOCKED"}:
        return True
    evidence = _evidence_object(row)
    if "baseline_eligible" in evidence and evidence.get("baseline_eligible") is not True:
        return True
    qa_state = str(evidence.get("qa_state") or "").upper()
    if qa_state and qa_state not in {"PASS", "PASS_PRESENCE_ONLY"}:
        return True
    return False


def calculate_baselines(
    rows: Iterable[Mapping[str, Any]],
    *,
    metric_name: str | None = None,
    as_of: str | date | None = None,
) -> dict[str, dict[str, float | None]]:
    """Return prior healthy observation medians for 7/30 calendar days.

    The current observation date is excluded. Multiple healthy runs on the
    same date contribute only the last run. Rows with no formal date retain a
    bounded-record fallback for compatibility with pre-V1 fixtures.
    """
    values = [dict(row) for row in rows]
    values.sort(key=lambda row: (str(_date_value(row) or ""), str(row.get("created_at") or ""), str(row.get("run_id") or "")))
    unhealthy = {str(row.get("run_id")) for row in values if _run_is_baseline_ineligible(row)}
    row_dates = [_date_value(row) for row in values]
    formal_dates = any(row.get("observation_date") or row.get("run_date") for row in values)
    if as_of is not None:
        if isinstance(as_of, date):
            end_date = as_of
        else:
            end_date = _date_value({"observation_date": as_of})
    else:
        end_date = max((item for item in row_dates if item is not None), default=None)

    result: dict[str, dict[str, float | None]] = {}
    if formal_dates and end_date is not None:
        grouped: dict[str, dict[date, list[Mapping[str, Any]]]] = {}
        for row, row_date in zip(values, row_dates):
            name = str(row.get("metric_name") or "")
            if not name or name == "__collection_state" or (metric_name and name != metric_name):
                continue
            if row_date is None or row_date >= end_date or str(row.get("run_id")) in unhealthy:
                continue
            if str(row.get("gate_status") or "").upper() in {"UNAVAILABLE", "BLOCKED", "DEGRADED"}:
                continue
            grouped.setdefault(name, {}).setdefault(row_date, []).append(row)
        start_7 = end_date - timedelta(days=7)
        start_30 = end_date - timedelta(days=30)
        for name, by_date in grouped.items():
            selected = {
                day: max(items, key=lambda row: (str(row.get("created_at") or ""), str(row.get("run_id") or "")))
                for day, items in by_date.items()
            }
            values_7: list[float] = []
            values_30: list[float] = []
            for day in sorted(selected):
                if day < start_30:
                    continue
                try:
                    number = float(selected[day].get("metric_value"))
                except (TypeError, ValueError):
                    continue
                values_30.append(number)
                if day >= start_7:
                    values_7.append(number)
            result[name] = {
                "previous": values_30[-1] if values_30 else None,
                "median_7d": float(median(values_7)) if values_7 else None,
                "median_30d": float(median(values_30)) if values_30 else None,
            }
        return result

    # Legacy fallback: no formal date is available, so preserve the original
    # bounded observation behavior rather than silently inventing a calendar.
    groups: dict[str, list[float]] = {}
    for row in values:
        name = str(row.get("metric_name") or "")
        if not name or name == "__collection_state" or (metric_name and name != metric_name):
            continue
        if str(row.get("run_id")) in unhealthy:
            continue
        if str(row.get("gate_status") or "").upper() in {"UNAVAILABLE", "BLOCKED", "DEGRADED"}:
            continue
        try:
            groups.setdefault(name, []).append(float(row.get("metric_value")))
        except (TypeError, ValueError):
            continue
    for name, numbers in groups.items():
        result[name] = {
            "previous": numbers[-1] if numbers else None,
            "median_7d": float(median(numbers[-7:])) if numbers else None,
            "median_30d": float(median(numbers[-30:])) if numbers else None,
        }
    return result
