"""Six-layer Gold gate.

The release gate checks the concrete export projection.  This gate separately
checks whether the configured semantic coverage is complete enough to support
a Gold claim, and fails closed when policy coverage or provenance is partial.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

GOLD_STATUSES = frozenset({"GOLD_PASS", "RELEASE_PASS_WITH_REVIEW_NOT_GOLD", "BLOCKED", "NOT_CERTIFIED"})


def evaluate_gold_gate(
    release_gate: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    source_snapshot_frozen: bool,
    provenance_complete: bool,
) -> dict[str, Any]:
    """Aggregate L1-L6 evidence without changing candidate values."""
    layers = policy.get("layers")
    issues: list[str] = []
    if policy.get("gold_claim_allowed") is not True:
        issues.append("GOLD_CLAIM_DISABLED_BY_POLICY")
    if not source_snapshot_frozen:
        issues.append("SOURCE_SNAPSHOT_NOT_FROZEN")
    if not provenance_complete:
        issues.append("PROVENANCE_INCOMPLETE")
    if not isinstance(layers, list) or len(layers) != 6:
        issues.append("GOLD_LAYER_SCHEMA_INVALID")
    else:
        for layer in layers:
            if not isinstance(layer, Mapping) or not str(layer.get("id") or ""):
                issues.append("GOLD_LAYER_INVALID")
                continue
            if layer.get("coverage") != "COMPLETE":
                issues.append(f"{layer.get('id')}_COVERAGE_PARTIAL")
            if layer.get("not_covered"):
                issues.append(f"{layer.get('id')}_NOT_COVERED")
            if not layer.get("covered_checks"):
                issues.append(f"{layer.get('id')}_CHECKS_EMPTY")
    blocking = list(release_gate.get("blocking_findings") or ())
    review = list(release_gate.get("review_findings") or ())
    if blocking:
        status = "BLOCKED"
    elif issues or review:
        status = "RELEASE_PASS_WITH_REVIEW_NOT_GOLD"
    else:
        status = "GOLD_PASS"
    return {
        "status": status,
        "gold_eligible": status == "GOLD_PASS",
        "issues": sorted(set(issues)),
        "blocking_finding_count": len(blocking),
        "review_finding_count": len(review),
        "policy_id": policy.get("policy_id"),
        "source_snapshot_frozen": bool(source_snapshot_frozen),
        "provenance_complete": bool(provenance_complete),
    }


__all__ = ["GOLD_STATUSES", "evaluate_gold_gate"]
