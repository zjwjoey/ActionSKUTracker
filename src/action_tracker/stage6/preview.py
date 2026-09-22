"""Main-compatible, read-only Stage 6 preview.

The preview validates owner evidence and source freshness. It never applies a
candidate or mutates SQLite, Master, or a dictionary.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..dictionary_apply import ALLOWLIST
from ..services.hashing import field_source_hash, localization_source_hash
from ..stage5.source_candidate_v2 import source_consistency_flags


CONFLICT_CLASSES = frozenset({
    "SOURCE_CHANGED", "TARGET_CHANGED", "REVIEW_CHANGED", "POLICY_CHANGED",
    "CANDIDATE_STALE", "MISSING_PROVENANCE", "NOT_OWNER_APPROVED",
    "FIELD_NOT_APPLYABLE", "SOURCE_CONFLICT", "NO_SOURCE",
    "FIELD_HASH_REQUIRED", "UNSUPPORTED_HASH_SCOPE", "SOURCE_FIELD_MISMATCH",
})

_SOURCE_FIELDS = {
    "name": "name",
    "cat1": "cat1",
    "cat2": "cat2",
    "spec": "spec",
    "description": "description",
    "details": "details",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def preview_one(
    owner: Mapping[str, Any], candidate: Mapping[str, Any] | None,
    source: Mapping[str, str] | None, target: Mapping[str, Any] | None,
    *, policy_hash: str, source_snapshot_path: str | None = None,
    expected_target_hash: str | None = None,
    expected_reviewed_value_hash: str | None = None,
    expected_policy_hash: str | None = None,
) -> dict[str, Any]:
    sku = str(owner.get("sku") or "").strip()
    field = str(owner.get("field") or "").strip()
    disposition = str(owner.get("owner_disposition") or "").strip()
    reviewed_value = owner.get("owner_final_candidate")
    current_target_value = target.get(ALLOWLIST[field]) if target is not None and field in ALLOWLIST else None
    current_target_hash = _digest(current_target_value)
    reviewed_hash = _digest(reviewed_value)
    # New candidates must carry an explicit field-scoped provenance marker.
    # ``legacy_overall`` is retained only for records that explicitly declare
    # the compatibility path; a missing marker is never silently upgraded to
    # the unsafe aggregate-hash behavior.
    hash_scope = str(candidate.get("hash_scope") or "") if candidate else ""
    conflicts: set[str] = set()
    current_source_hash = None
    source_value = None
    source_is_empty = False
    if source is not None and field in _SOURCE_FIELDS:
        source_value = str(source.get(_SOURCE_FIELDS[field]) or "").strip()
        source_is_empty = not source_value
        if source_is_empty:
            # Empty official fields are terminal NO_SOURCE, regardless of
            # whether a candidate happens to have been supplied.
            conflicts.add("NO_SOURCE")
    if disposition not in {"ACCEPT_AS_IS", "ACCEPT_WITH_MINOR_EDIT"} or not owner.get("owner_reviewer") or not owner.get("owner_reviewed_at"):
        conflicts.add("NOT_OWNER_APPROVED")
    if field not in ALLOWLIST:
        conflicts.add("FIELD_NOT_APPLYABLE")
    if candidate is None or source is None or target is None or not source_snapshot_path:
        conflicts.add("MISSING_PROVENANCE")
    else:
        source_key = _SOURCE_FIELDS.get(field)
        if source_key is None:
            conflicts.add("FIELD_NOT_APPLYABLE")
        else:
            if candidate.get("source_spanish_value") is not None and str(candidate.get("source_spanish_value") or "").strip() != source_value:
                conflicts.add("SOURCE_FIELD_MISMATCH")
        required = ("candidate_id", "source_hash", "source_spanish_value", "parsed_candidate")
        if any(key not in candidate for key in required) or candidate.get("candidate_id") != owner.get("candidate_id") or candidate.get("sku") != sku or candidate.get("source_field") != field:
            conflicts.add("MISSING_PROVENANCE")
        source_hash_record = {
            "name_es": source.get("name", ""), "cat1_es": source.get("cat1", ""),
            "cat2_es": source.get("cat2", ""), "spec_es": source.get("spec", ""),
            "desc_es": source.get("description", ""), "details_es": source.get("details", ""),
        }
        if field not in _SOURCE_FIELDS:
            expected_source_hash = ""
            conflicts.add("FIELD_NOT_APPLYABLE")
        elif hash_scope == "field":
            expected_source_hash = field_source_hash(source_hash_record, field)
        elif hash_scope == "legacy_overall" and candidate.get("legacy_compatibility") is True:
            expected_source_hash = localization_source_hash(source_hash_record)
        elif not hash_scope:
            expected_source_hash = field_source_hash(source_hash_record, field)
            conflicts.add("FIELD_HASH_REQUIRED")
        else:
            expected_source_hash = field_source_hash(source_hash_record, field)
            conflicts.add("UNSUPPORTED_HASH_SCOPE")
        if expected_source_hash != str(candidate.get("source_hash") or ""):
            conflicts.add("CANDIDATE_STALE")
        current_source_hash = expected_source_hash
        if current_source_hash != str(candidate.get("source_hash") or ""):
            conflicts.add("SOURCE_CHANGED")
        flags = source_consistency_flags(source)
        if flags:
            conflicts.add("SOURCE_CONFLICT")
    if expected_target_hash is not None and current_target_hash != expected_target_hash:
        conflicts.add("TARGET_CHANGED")
    if expected_reviewed_value_hash is not None and reviewed_hash != expected_reviewed_value_hash:
        conflicts.add("REVIEW_CHANGED")
    if expected_policy_hash is not None and policy_hash != expected_policy_hash:
        conflicts.add("POLICY_CHANGED")
    if not conflicts <= CONFLICT_CLASSES:
        raise AssertionError("UNKNOWN_CONFLICT_CLASS")
    if source_is_empty:
        # Do not allow a pre-existing target or candidate to turn NO_SOURCE
        # into an update. The caller may separately clear legacy data under a
        # dedicated cleanup workflow, but Stage6 preview never invents facts.
        action = "NO_SOURCE"
    else:
        action = "BLOCKED_CONFLICT" if conflicts else ("NO_CHANGE" if current_target_value == reviewed_value else "WOULD_UPDATE")
    return {
        "sku": sku, "field": field, "candidate_id": owner.get("candidate_id"),
        "hash_scope": hash_scope or None, "current_source_hash": current_source_hash if candidate and source else None,
        "reviewed_source_hash": candidate.get("source_hash") if candidate else "",
        "current_target_value": current_target_value, "current_target_hash": current_target_hash,
        "reviewed_value": reviewed_value, "reviewed_value_hash": reviewed_hash,
        "policy_manifest_hash": policy_hash, "apply_action": action,
        "conflict_status": "DO_NOT_APPLY" if conflicts else "NONE",
        "conflict_reason": sorted(conflicts),
    }


def preview_hash(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: (str(row.get("sku") or ""), str(row.get("field") or ""), str(row.get("candidate_id") or ""), json.dumps(row, ensure_ascii=False, sort_keys=True)))
    return _digest(ordered)
