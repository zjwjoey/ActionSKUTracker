from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .contracts import LocalizationPlan


def can_promote(candidate: Mapping[str, Any], *, validator_pass: bool, source_hash_match: bool, human_approved: bool = False) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not validator_pass: reasons.append("VALIDATOR_FAIL")
    if not source_hash_match: reasons.append("SOURCE_HASH_MISMATCH")
    kind = str(candidate.get("semantic_type") or "")
    if kind not in {"STANDARD_UNIT", "TECH_TOKEN", "MODEL"} and not human_approved: reasons.append("HUMAN_APPROVAL_REQUIRED")
    if str(candidate.get("status") or "") == "REJECTED": reasons.append("CANDIDATE_REJECTED")
    return not reasons, tuple(reasons)


def promotion_manifest(candidate: Mapping[str, Any], old_state: str, new_state: str, *, evidence_hash: str = "") -> dict[str, Any]:
    return {"candidate_id": candidate.get("candidate_id"), "old_state": old_state, "new_state": new_state,
            "reviewer": candidate.get("reviewer") or candidate.get("approved_by"),
            "policy": "CHINESE_LOCALIZATION_STANDARD_V1", "reason": candidate.get("reason") or "",
            "source_evidence": candidate.get("evidence_skus") or candidate.get("source_examples") or [],
            "evidence_hash": evidence_hash or hashlib.sha256(json.dumps(dict(candidate), ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
            "knowledge_file_before_hash": candidate.get("knowledge_file_before_hash"),
            "knowledge_file_after_hash": candidate.get("knowledge_file_after_hash"),
            "policy_version": "CHINESE_LOCALIZATION_STANDARD_V1"}
