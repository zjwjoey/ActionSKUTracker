"""Conservative, field-level auto-approval policy (default shadow only)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import KNOWLEDGE_FIELDS
from .validator import CandidateValidation

POLICY_VERSION = "auto_approval_v1"
LOW_RISK_FIELDS = frozenset({"cat1", "cat2", "spec"})
HIGH_RISK_FIELDS = frozenset({"description", "details"})


@dataclass(frozen=True)
class ApprovalDecision:
    field: str
    decision: str
    rules_passed: tuple[str, ...]
    rules_failed: tuple[str, ...]
    policy_version: str = POLICY_VERSION


def evaluate_candidate(
    candidate: Mapping[str, Any],
    validation: CandidateValidation,
    *,
    enabled: bool = False,
    shadow: bool = True,
    source_unchanged: bool = True,
    human_conflict: bool = False,
    scope_conflict: bool = False,
    confidence_threshold: float = 0.95,
) -> tuple[ApprovalDecision, ...]:
    """Return decisions without applying anything to production.

    In shadow mode a safe result is reported as WOULD_AUTO_APPROVE; when the
    gate is disabled it never becomes AUTO_APPROVED.
    """
    fields = candidate.get("fields") if isinstance(candidate.get("fields"), Mapping) else {}
    confidence = float(candidate.get("confidence") or 0)
    decisions: list[ApprovalDecision] = []
    for field in KNOWLEDGE_FIELDS:
        if field not in fields:
            continue
        passed = ["VALIDATOR_PASS"] if validation.ok else []
        failed = list(validation.reasons)
        if not source_unchanged:
            failed.append("SOURCE_HASH_CHANGED")
        if human_conflict:
            failed.append("HUMAN_OVERRIDE_CONFLICT")
        if scope_conflict:
            failed.append("SCOPED_TERM_CONFLICT")
        if confidence < confidence_threshold:
            failed.append("CONFIDENCE_BELOW_THRESHOLD")
        if field not in LOW_RISK_FIELDS:
            failed.append("FIELD_NOT_LOW_RISK")
        if failed:
            decision = "REVIEW_REQUIRED"
        elif enabled:
            decision = "AUTO_APPROVED"
        elif shadow:
            decision = "WOULD_AUTO_APPROVE"
        else:
            decision = "REVIEW_REQUIRED"
        decisions.append(ApprovalDecision(field, decision, tuple(dict.fromkeys(passed)), tuple(dict.fromkeys(failed))))
    return tuple(decisions)
