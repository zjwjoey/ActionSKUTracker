from __future__ import annotations

"""Collection quality state and commit gate."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..contracts import COLLECTION_STATES, CollectionMetric, DataQualityIssue, IssueScope, canonical_json
from ..repository import DataQualityRepository
from .baseline import calculate_baselines
from .drift import detect_schema_drift
from .metrics import build_collection_metrics

DEFAULTS: dict[str, Any] = {
    "required_categories": 15, "listing_warn_drop_pct": 8.0, "listing_block_drop_pct": 15.0,
    "current_warn_drop_pct": 8.0, "current_block_drop_pct": 15.0,
    "coverage_warn_drop_points": 5.0, "coverage_block_drop_points": 10.0,
    "price_coverage_warn_below": 0.995, "price_coverage_block_below": 0.98,
    "description_warn_drop_points": 10.0, "description_block_drop_points": 25.0,
    "detail_failure_warn_above": 0.10, "detail_failure_block_above": 0.25,
}


@dataclass(frozen=True)
class CollectionQualityResult:
    run_id: str
    state: str
    metrics: tuple[CollectionMetric, ...]
    baselines: Mapping[str, Mapping[str, Any]]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    drift_issues: tuple[DataQualityIssue, ...]

    @property
    def commit_allowed(self) -> bool:
        return self.state in {"COLLECTION_OK", "COLLECTION_WARN"}

    @property
    def metrics_hash(self) -> str:
        payload = [metric.as_dict() for metric in sorted(self.metrics, key=lambda item: (item.metric_name, item.metric_scope or ""))]
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "state": self.state, "commit_allowed": self.commit_allowed,
            "metrics_hash": self.metrics_hash,
            "metrics": [metric.as_dict() for metric in self.metrics],
            "baselines": dict(self.baselines), "blockers": list(self.blockers),
            "warnings": list(self.warnings), "drift_issues": [issue.as_dict() for issue in self.drift_issues],
        }


def _value(metrics: Mapping[str, float | None], name: str) -> float | None:
    value = metrics.get(name)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def evaluate_collection(run_id: str, metrics: list[CollectionMetric], *, history: list[Mapping[str, Any]] | None = None,
                        config: Mapping[str, Any] | None = None) -> CollectionQualityResult:
    cfg = dict(DEFAULTS); cfg.update(config or {})
    values = {metric.metric_name: metric.metric_value for metric in metrics}
    baselines = calculate_baselines(history or [])
    blockers: list[str] = []; warnings: list[str] = []
    categories = _value(values, "successful_category_count")
    required = int(cfg["required_categories"])
    if categories is not None and categories < required:
        blockers.append(f"CATEGORY_SUCCESS:{categories:g}/{required}")
    for name, warn_pct, block_pct in (("listing_unique", cfg["listing_warn_drop_pct"], cfg["listing_block_drop_pct"]), ("current_valid", cfg["current_warn_drop_pct"], cfg["current_block_drop_pct"])):
        value = _value(values, name); base = (baselines.get(name) or {}).get("median_7d")
        if value is None or base in (None, 0):
            continue
        drop = (float(base) - value) / float(base) * 100
        if drop >= float(block_pct): blockers.append(f"{name.upper()}_DROP:{drop:.2f}%")
        elif drop >= float(warn_pct): warnings.append(f"{name.upper()}_DROP:{drop:.2f}%")
    for name, warn_points, block_points in (("cat2_coverage", cfg["coverage_warn_drop_points"], cfg["coverage_block_drop_points"]), ("description_coverage", cfg["description_warn_drop_points"], cfg["description_block_drop_points"])):
        value = _value(values, name); base = (baselines.get(name) or {}).get("median_7d")
        if value is None or base is None: continue
        drop = (float(base) - value) * 100 if abs(float(base)) <= 1 and abs(value) <= 1 else float(base) - value
        if drop >= float(block_points): blockers.append(f"{name.upper()}_DROP:{drop:.2f}POINTS")
        elif drop >= float(warn_points): warnings.append(f"{name.upper()}_DROP:{drop:.2f}POINTS")
    price = _value(values, "price_coverage")
    if price is not None:
        if price < float(cfg["price_coverage_block_below"]): blockers.append(f"PRICE_COVERAGE:{price:.4f}")
        elif price < float(cfg["price_coverage_warn_below"]): warnings.append(f"PRICE_COVERAGE:{price:.4f}")
    failure = _value(values, "detail_failure_rate")
    if failure is not None:
        if failure > float(cfg["detail_failure_block_above"]): blockers.append(f"DETAIL_FAILURE_RATE:{failure:.4f}")
        elif failure > float(cfg["detail_failure_warn_above"]): warnings.append(f"DETAIL_FAILURE_RATE:{failure:.4f}")
    current_plain = {name: value for name, value in values.items()}
    drift_cfg = dict(cfg.get("drift") or {})
    drift_cfg.setdefault("coverage_drop_points", cfg["coverage_block_drop_points"])
    drift = detect_schema_drift(current_plain, baselines, run_id=run_id, config=drift_cfg)
    if drift:
        blockers.extend(f"DRIFT:{issue.issue_type}:{issue.field_name}" for issue in drift)
    if blockers: state = "COLLECTION_BLOCKED"
    elif drift or any("DROP" in item for item in warnings): state = "COLLECTION_DEGRADED"
    elif warnings: state = "COLLECTION_WARN"
    else: state = "COLLECTION_OK"
    return CollectionQualityResult(run_id, state, tuple(metrics), baselines, tuple(blockers), tuple(warnings), tuple(drift))


def evaluate_and_persist(db_path: Path, run_id: str, payload: Mapping[str, Any], *, config: Mapping[str, Any] | None = None) -> CollectionQualityResult:
    repo = DataQualityRepository(Path(db_path))
    metrics = build_collection_metrics(run_id, payload)
    history = repo.get_metrics()
    result = evaluate_collection(run_id, metrics, history=history, config=config)
    state_metric = CollectionMetric(run_id=run_id, metric_name="__collection_state", metric_scope=result.state,
                                    metric_value=None, gate_status=result.state, evidence={"blockers": list(result.blockers), "warnings": list(result.warnings), "metrics_hash": result.metrics_hash})
    repo.save_metrics([*metrics, state_metric])
    repo.save_issues(result.drift_issues)
    return result


def collection_commit_allowed(state: str, *, override: bool = False,
                              override_evidence: Mapping[str, Any] | None = None,
                              run_id: str | None = None, metrics_hash: str | None = None) -> bool:
    state = str(state or "").upper()
    if state == "COLLECTION_BLOCKED":
        return False
    if state == "COLLECTION_DEGRADED":
        return bool(override and run_id and metrics_hash and validate_collection_override(
            override_evidence, run_id=run_id, metrics_hash=metrics_hash))
    return state in {"", "COLLECTION_OK", "COLLECTION_WARN"}


def validate_collection_override(evidence: Mapping[str, Any] | None, *, run_id: str,
                                 metrics_hash: str, now: datetime | None = None) -> bool:
    """Validate the bounded one-shot override required for degraded commits."""
    item = dict(evidence or {})
    required = ("actor", "reason", "run_id", "metrics_hash", "expires_at", "one_shot")
    if any(not str(item.get(key) or "").strip() for key in required[:-1]):
        return False
    if str(item.get("run_id")) != str(run_id) or str(item.get("metrics_hash")) != str(metrics_hash):
        return False
    if item.get("one_shot") is not True:
        return False
    try:
        expires = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00"))
        reference = now or datetime.now(timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > reference
    except (TypeError, ValueError):
        return False
