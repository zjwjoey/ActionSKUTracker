from __future__ import annotations

"""Schema/semantic drift detection for collection metrics."""

from typing import Any, Mapping

from ..contracts import DataQualityIssue, IssueScope


def detect_schema_drift(current: Mapping[str, Any], baselines: Mapping[str, Mapping[str, Any]], *, run_id: str,
                        sample_skus: Mapping[str, list[str]] | None = None, config: Mapping[str, Any] | None = None) -> list[DataQualityIssue]:
    cfg = dict(config or {})
    drift: list[DataQualityIssue] = []
    samples = sample_skus or {}

    def number(name: str) -> float | None:
        value = current.get(name)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def baseline(name: str) -> float | None:
        entry = baselines.get(name) or {}
        try:
            return float(entry.get("median_7d")) if entry.get("median_7d") is not None else None
        except (TypeError, ValueError):
            return None

    coverage_fields = ("price_coverage", "cat1_coverage", "cat2_coverage", "spec_coverage", "description_coverage", "details_coverage")
    threshold = float(cfg.get("coverage_drop_points", 10.0))
    for field in coverage_fields:
        now = number(field); old = baseline(field)
        if now is None or old is None:
            continue
        delta_points = (now - old) * 100 if abs(now) <= 1.0 and abs(old) <= 1.0 else now - old
        if delta_points <= -threshold:
            issue_type = "FIELD_COVERAGE_COLLAPSE" if field != "cat2_coverage" else "CATEGORY_COLLECTION_DRIFT"
            drift.append(DataQualityIssue(
                issue_type=issue_type, severity="HIGH", scope=IssueScope.COLLECTION.value,
                run_id=run_id, field_name=field, current_value=now,
                expected_rule=f"coverage drop must not exceed {threshold} points",
                evidence={"current": now, "baseline_7d": old, "delta_points": delta_points, "sample_skus": samples.get(field, [])},
            ))
    equals = number("original_price_equals_current_ratio")
    if equals is not None and equals >= float(cfg.get("price_equal_current_ratio", 0.90)):
        drift.append(DataQualityIssue(
            issue_type="PRICE_SCHEMA_DRIFT", severity="HIGH", scope=IssueScope.COLLECTION.value,
            run_id=run_id, field_name="original_price", current_value=equals,
            expected_rule="original_price must not collapse to current_price for nearly every SKU",
            evidence={"ratio": equals, "sample_skus": samples.get("original_price", [])},
        ))
    for field, issue_type, threshold_key in (("ui_contamination_rate", "UI_CONTAMINATION_DRIFT", "ui_contamination_rate"), ("html_contamination_rate", "HTML_CONTAMINATION_DRIFT", "html_contamination_rate")):
        value = number(field)
        if value is not None and value > float(cfg.get(threshold_key, 0.0)):
            drift.append(DataQualityIssue(
                issue_type=issue_type, severity="HIGH", scope=IssueScope.COLLECTION.value,
                run_id=run_id, field_name=field, current_value=value,
                expected_rule=f"{field} must remain at or below configured threshold",
                evidence={"current": value, "sample_skus": samples.get(field, [])},
            ))
    return drift

