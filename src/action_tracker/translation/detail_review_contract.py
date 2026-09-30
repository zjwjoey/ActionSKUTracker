"""Fail-closed contract for owner decisions on detail-rule review queues.

The audit queue is evidence only.  This module validates a separate decision
ledger and returns a proposal-shaped result; it never edits the terminology
rules, workbooks, dictionaries, or database.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


APPROVE = "APPROVE"
REJECT = "REJECT"
DECISIONS = frozenset({APPROVE, REJECT})


def validate_detail_rule_decisions(
    queue_rows: Iterable[Mapping[str, Any]],
    decision_rows: Iterable[Mapping[str, Any]],
    *,
    source_sha256: str,
    target_sha256: str,
    rules_sha256: str,
) -> dict[str, Any]:
    """Validate owner decisions against one frozen queue snapshot.

    An approved row must carry human identity, timestamp, owner-confirmed
    evidence, and all three hashes.  Unknown, duplicate, stale, or incomplete
    decisions are errors.  Rejected rows remain auditable but produce no rule
    proposal.
    """
    queue: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for raw in queue_rows:
        row = dict(raw)
        review_id = str(row.get("review_id") or "").strip()
        if not review_id:
            errors.append({"code": "QUEUE_REVIEW_ID_EMPTY", "review_id": ""})
            continue
        if review_id in queue:
            errors.append({"code": "QUEUE_REVIEW_ID_DUPLICATE", "review_id": review_id})
            continue
        queue[review_id] = row
    decisions: dict[str, dict[str, Any]] = {}
    for raw in decision_rows:
        row = dict(raw)
        review_id = str(row.get("review_id") or "").strip()
        decision = str(row.get("decision") or "").strip().upper()
        if not review_id:
            errors.append({"code": "DECISION_REVIEW_ID_EMPTY", "review_id": ""})
            continue
        if review_id not in queue:
            errors.append({"code": "DECISION_REVIEW_ID_UNKNOWN", "review_id": review_id})
            continue
        if review_id in decisions:
            errors.append({"code": "DECISION_DUPLICATE", "review_id": review_id})
            continue
        decisions[review_id] = row
        if decision not in DECISIONS:
            errors.append({"code": "DECISION_VALUE_INVALID", "review_id": review_id})
            continue
        for field, expected in (
            ("source_sha256", source_sha256),
            ("target_sha256", target_sha256),
            ("detail_rules_sha256", rules_sha256),
        ):
            if str(row.get(field) or "") != str(expected):
                errors.append({"code": "DECISION_HASH_MISMATCH", "review_id": review_id, "field": field})
        if decision == APPROVE:
            required = {
                "reviewer": str(row.get("reviewer") or "").strip(),
                "reviewed_at": str(row.get("reviewed_at") or "").strip(),
                "evidence_status": str(row.get("evidence_status") or "").strip().upper(),
                "approved_target": str(row.get("approved_target") or "").strip(),
            }
            if not required["reviewer"]:
                errors.append({"code": "APPROVAL_REVIEWER_MISSING", "review_id": review_id})
            if not required["reviewed_at"]:
                errors.append({"code": "APPROVAL_TIMESTAMP_MISSING", "review_id": review_id})
            if required["evidence_status"] != "OWNER_CONFIRMED":
                errors.append({"code": "APPROVAL_EVIDENCE_NOT_CONFIRMED", "review_id": review_id})
            if not required["approved_target"]:
                errors.append({"code": "APPROVED_TARGET_MISSING", "review_id": review_id})

    approved = [
        {"review_id": review_id, **row}
        for review_id, row in decisions.items()
        if str(row.get("decision") or "").strip().upper() == APPROVE
        and not any(error.get("review_id") == review_id for error in errors)
    ]
    rejected = [
        {"review_id": review_id, "decision": REJECT}
        for review_id, row in decisions.items()
        if str(row.get("decision") or "").strip().upper() == REJECT
        and not any(error.get("review_id") == review_id for error in errors)
    ]
    return {
        "accepted": not errors,
        "errors": errors,
        "queue_row_count": len(queue),
        "decision_row_count": len(decisions),
        "approved_rows": approved,
        "rejected_rows": rejected,
        "auto_apply": False,
        "rules_write": False,
    }


__all__ = ["APPROVE", "REJECT", "DECISIONS", "validate_detail_rule_decisions"]
