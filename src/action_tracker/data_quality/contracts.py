from __future__ import annotations

"""Shared, deterministic contracts for data-quality work."""

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping


class IssueScope(StrEnum):
    HISTORICAL = "HISTORICAL"
    CURRENT = "CURRENT"
    COLLECTION = "COLLECTION"


ISSUE_SEVERITIES = frozenset({"BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"})
ISSUE_STATUSES = frozenset({"OPEN", "REVIEW_REQUIRED", "APPROVED", "RESOLVED", "WAIVED", "SUPERSEDED"})
COLLECTION_STATES = frozenset({"COLLECTION_OK", "COLLECTION_WARN", "COLLECTION_DEGRADED", "COLLECTION_BLOCKED"})
ISSUE_TYPES = frozenset({
    "INVALID_ORIGINAL_PRICE", "PROMOTION_FIELD_CONTAMINATION", "UI_TEXT_CONTAMINATION",
    "HTML_CONTAMINATION", "UNRESOLVED_HISTORICAL_IDENTITY", "ORPHAN_PRICE_HISTORY",
    "ORPHAN_EVENT_HISTORY", "CATEGORY_MISSING", "DUPLICATE_SKU", "ZH_ES_SKU_SET_MISMATCH",
    "CURRENT_WITHOUT_SKU", "FIELD_PROVENANCE_MISSING", "SOURCE_HASH_MISSING",
    "FIELD_COVERAGE_COLLAPSE", "PRICE_SCHEMA_DRIFT", "UI_CONTAMINATION_DRIFT",
    "HTML_CONTAMINATION_DRIFT", "CATEGORY_COLLECTION_DRIFT", "REPAIR_APPLY_BLOCKED",
})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def issue_id(*, issue_type: str, scope: str, official_sku: str | None = None,
             run_id: str | None = None, field_name: str | None = None,
             source_hash: str | None = None) -> str:
    """Return the stable issue key required by the V1 contract."""
    payload = "|".join("" if value is None else str(value) for value in (
        issue_type, scope, official_sku, run_id, field_name, source_hash,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DataQualityIssue:
    issue_type: str
    severity: str
    scope: str
    official_sku: str | None = None
    canonical_id: str | None = None
    run_id: str | None = None
    field_name: str | None = None
    current_value: Any = None
    expected_rule: str = ""
    evidence: Mapping[str, Any] | None = None
    source_hash: str | None = None
    source_commit_id: str | None = None
    status: str = "OPEN"
    resolution_type: str | None = None
    resolution_note: str | None = None
    detected_at: str = ""
    resolved_at: str | None = None
    resolved_by: str | None = None
    issue_id: str = ""

    def __post_init__(self) -> None:
        if self.issue_type not in ISSUE_TYPES:
            raise ValueError(f"ISSUE_TYPE_INVALID:{self.issue_type}")
        if self.severity not in ISSUE_SEVERITIES:
            raise ValueError(f"ISSUE_SEVERITY_INVALID:{self.severity}")
        if self.scope not in {item.value for item in IssueScope}:
            raise ValueError(f"ISSUE_SCOPE_INVALID:{self.scope}")
        if self.status not in ISSUE_STATUSES:
            raise ValueError(f"ISSUE_STATUS_INVALID:{self.status}")
        if self.issue_id:
            return
        object.__setattr__(self, "issue_id", issue_id(
            issue_type=self.issue_type, scope=self.scope,
            official_sku=self.official_sku, run_id=self.run_id,
            field_name=self.field_name, source_hash=self.source_hash,
        ))
        if self.evidence is None:
            object.__setattr__(self, "evidence", {})

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_json"] = canonical_json(result.pop("evidence") or {})
        return result


@dataclass(frozen=True)
class CollectionMetric:
    run_id: str
    metric_name: str
    metric_scope: str | None
    metric_value: float | None
    numerator: float | None = None
    denominator: float | None = None
    baseline_7d: float | None = None
    baseline_30d: float | None = None
    delta_7d: float | None = None
    delta_30d: float | None = None
    gate_status: str = "UNAVAILABLE"
    evidence: Mapping[str, Any] | None = None
    metric_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.metric_id:
            key = "|".join((self.run_id, self.metric_name, self.metric_scope or ""))
            object.__setattr__(self, "metric_id", hashlib.sha256(key.encode("utf-8")).hexdigest())

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_json"] = canonical_json(result.pop("evidence") or {})
        return result
