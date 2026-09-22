"""Field-level Stage 6 preview with fail-closed freshness and hash checks."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..dictionary_apply import ALLOWLIST
from ..services.hashing import field_source_hash, localization_source_hash
from ..stage5.pipeline import canonical_json
from ..stage5.source_candidate_v2 import source_consistency_flags


CONFLICT_CLASSES = frozenset({
    "SOURCE_CHANGED", "TARGET_CHANGED", "REVIEW_CHANGED", "POLICY_CHANGED",
    "CANDIDATE_STALE", "MISSING_PROVENANCE", "NOT_OWNER_APPROVED",
    "FIELD_NOT_APPLYABLE", "SOURCE_CONFLICT",
})


def digest(value: Any) -> str:
    """Hash a typed value, preserving the distinction between null and blank."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def preview_one(
    owner: Mapping[str, Any], candidate: Mapping[str, Any] | None,
    source: Mapping[str, str] | None, target: Mapping[str, Any] | None,
    *, policy_hash: str, source_snapshot_path: str | None = None,
    expected_target_hash: str | None = None,
    expected_reviewed_value_hash: str | None = None,
    expected_policy_hash: str | None = None,
) -> dict[str, Any]:
    """Return a decision record; any conflict means DO_NOT_APPLY.

    The optional expected hashes are the values frozen by an earlier preview.
    Passing changed values exercises the same optimistic-concurrency guard
    that a separately authorized Apply must later enforce.
    """
    sku = str(owner.get("sku") or "").strip()
    field = str(owner.get("field") or "").strip()
    disposition = str(owner.get("owner_disposition") or "").strip()
    reviewed_value = owner.get("owner_final_candidate")
    reviewed_hash = digest(reviewed_value)
    current_target_value = target.get(ALLOWLIST[field]) if target is not None and field in ALLOWLIST else None
    current_target_hash = digest(current_target_value)
    reviewed_source_hash = str(candidate.get("source_hash") or "") if candidate else ""
    hash_scope = str(candidate.get("hash_scope") or "legacy_overall") if candidate else "legacy_overall"
    if hash_scope == "field":
        source_for_hash = {
            "name_es": source.get("name", "") if source else "",
            "cat1_es": source.get("cat1", "") if source else "",
            "cat2_es": source.get("cat2", "") if source else "",
            "spec_es": source.get("spec", "") if source else "",
            "desc_es": source.get("description", "") if source else "",
            "details_es": source.get("details", "") if source else "",
        }
        current_source_hash = field_source_hash(source_for_hash, field) if source is not None else None
    else:
        current_source_hash = localization_source_hash(dict(target)) if target is not None else None
    flags = source_consistency_flags(source) if source else []
    conflicts: set[str] = set()

    if disposition not in {"ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT"} or not owner.get("owner_reviewer") or not owner.get("owner_reviewed_at"):
        conflicts.add("NOT_OWNER_APPROVED")
    if field not in ALLOWLIST:
        conflicts.add("FIELD_NOT_APPLYABLE")
    if candidate is None or source is None or target is None or not source_snapshot_path:
        conflicts.add("MISSING_PROVENANCE")
    else:
        required_candidate = (
            "candidate_id", "source_hash", "source_spanish_value", "contract_hash",
            "guard_policy_hash", "resolver_result", "raw_model_output", "parsed_candidate",
        )
        if (
            any(key not in candidate for key in required_candidate)
            or candidate.get("candidate_id") != owner.get("candidate_id")
            or candidate.get("sku") != sku
            or candidate.get("source_field") != field
            or candidate.get("source_spanish_value") != owner.get("spanish_source")
            or not isinstance(reviewed_value, str) or not reviewed_value.strip()
        ):
            conflicts.add("MISSING_PROVENANCE")
        source_hash_record = {
            "name_es": source.get("name"), "cat1_es": source.get("cat1"),
            "cat2_es": source.get("cat2"), "spec_es": source.get("spec"),
            "desc_es": source.get("description"), "details_es": source.get("details"),
        }
        expected_source_hash = (
            field_source_hash(source_hash_record, field)
            if hash_scope == "field"
            else localization_source_hash(source_hash_record)
        )
        if source and expected_source_hash != reviewed_source_hash:
            conflicts.add("CANDIDATE_STALE")
        if flags:
            conflicts.add("SOURCE_CONFLICT")
        if current_source_hash != reviewed_source_hash:
            conflicts.add("SOURCE_CHANGED")
    if expected_target_hash is not None and current_target_hash != expected_target_hash:
        conflicts.add("TARGET_CHANGED")
    if expected_reviewed_value_hash is not None and reviewed_hash != expected_reviewed_value_hash:
        conflicts.add("REVIEW_CHANGED")
    if expected_policy_hash is not None and policy_hash != expected_policy_hash:
        conflicts.add("POLICY_CHANGED")
    if not set(conflicts) <= CONFLICT_CLASSES:
        raise AssertionError("UNKNOWN_CONFLICT_CLASS")

    if conflicts:
        action = "BLOCKED_CONFLICT"
    elif current_target_value == reviewed_value:
        action = "NO_CHANGE"
    else:
        action = "WOULD_UPDATE"
    return {
        "sku": sku, "field": field, "candidate_id": owner.get("candidate_id"),
        "review_id": owner.get("review_id"), "owner_disposition": disposition,
        "reviewer": owner.get("owner_reviewer"), "reviewed_at": owner.get("owner_reviewed_at"),
        "source_snapshot_path": source_snapshot_path,
        "hash_scope": hash_scope,
        "current_source_hash": current_source_hash,
        "reviewed_source_hash": reviewed_source_hash,
        "current_target_value": current_target_value,
        "current_target_hash": current_target_hash,
        "reviewed_value": reviewed_value,
        "reviewed_value_hash": reviewed_hash,
        "expected_target_hash": expected_target_hash or current_target_hash,
        "policy_manifest_hash": policy_hash,
        "source_consistency_flags": flags,
        "apply_action": action,
        "conflict_status": "DO_NOT_APPLY" if conflicts else "NONE",
        "conflict_reason": sorted(conflicts),
    }


def preview_hash(rows: list[dict[str, Any]]) -> str:
    # Include the canonical row as a tie-breaker so equal identity keys do not
    # make the digest depend on the caller's input order.
    return digest(sorted(
        rows,
        key=lambda row: (
            str(row.get("sku") or ""),
            str(row.get("field") or ""),
            str(row.get("candidate_id") or ""),
            canonical_json(row),
        ),
    ))
