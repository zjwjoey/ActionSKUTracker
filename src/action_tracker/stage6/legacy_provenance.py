"""Fail-closed compatibility validation for historical review artifacts.

This adapter records only evidence that existed in a legacy artifact. It does
not synthesize modern model, policy, request, reviewer, or snapshot metadata.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from ..services.hashing import field_source_hash
from ..stage5.source_candidate_v2 import source_consistency_flags

LEGACY_PROVENANCE_TYPE = "LEGACY_ARTIFACT"
LEGACY_SOURCE_REVALIDATION_METHOD = "HISTORICAL_SOURCE_TEXT_EXACT_MATCH"
LEGACY_ALLOWED_DECISIONS = frozenset({"KEEP", "CORRECTED", "APPROVED", "VERIFIED"})
LEGACY_ABSENT_MODERN_FIELDS = (
    "contract_hash", "guard_policy_hash", "raw_model_output", "model_request_id",
    "model_response", "review_model_version", "modern_source_snapshot_path",
    "historical_policy_hash",
)

SOURCE_KEY = {
    "name": "name", "cat1": "cat1", "cat2": "cat2",
    "spec": "spec", "description": "description", "details": "details",
}


@dataclass(frozen=True)
class LegacyArtifactProvenance:
    provenance_type: str
    artifact_path: str
    artifact_kind: str
    artifact_sha256: str
    sku: str
    field: str
    historical_source_text: str
    historical_source_hash: str
    current_source_text: str
    current_source_hash: str
    reviewed_value: str
    review_decision: str
    reviewed_at: str
    review_evidence_id: str
    owner_decision: str
    owner_note: str
    owner_approval_artifact: str
    owner_approval_sha256: str
    master_baseline: str
    sqlite_baseline: str
    sqlite_baseline_present: bool
    source_revalidated: bool
    source_revalidation_method: str
    revalidation_status: str
    artifact_conflict: bool = False
    source_conflict_flags: tuple[str, ...] = ()
    historical_metadata: Mapping[str, Any] = field(default_factory=dict)
    legacy_missing_fields: tuple[str, ...] = field(default_factory=lambda: LEGACY_ABSENT_MODERN_FIELDS)
    evidence_absence_reason: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_source_text(value: Any) -> str:
    """Only trim and normalize line endings; preserve case, accents and punctuation."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def adapt_legacy_record(
    revalidation: Mapping[str, Any], owner_row: Mapping[str, Any], *,
    artifact_kind: str, artifact_sha256: str = "",
    owner_approval_artifact: str = "", owner_approval_sha256: str = "",
    current_sqlite_value: str | None = None,
    historical_metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], LegacyArtifactProvenance]:
    """Build a Stage6 candidate/owner envelope from real persisted evidence.

    This function copies historical values; it does not populate any modern
    model or policy field. The caller still must run ``legacy_conflicts``.
    """
    sku = str(revalidation.get("sku") or "").strip()
    field_name = str(revalidation.get("field") or "").strip()
    candidate_id = str(owner_row.get("candidate_id") or "").strip()
    review_artifact = str(revalidation.get("review_artifact") or "").strip()
    historical_source = str(revalidation.get("historical_source_text") or "")
    current_source = str(revalidation.get("current_source_text") or "")
    reviewed_value = str(revalidation.get("reviewed_value") or "")
    owner_decision = str(owner_row.get("owner_decision") or "").strip().upper()
    missing = list(LEGACY_ABSENT_MODERN_FIELDS)
    absence_reason = {key: "NOT_RECORDED_BY_HISTORICAL_REVIEW_WORKFLOW" for key in missing}
    provenance = LegacyArtifactProvenance(
        provenance_type=LEGACY_PROVENANCE_TYPE,
        artifact_path=review_artifact,
        artifact_kind=artifact_kind,
        artifact_sha256=artifact_sha256,
        sku=sku,
        field=field_name,
        historical_source_text=historical_source,
        historical_source_hash=str(revalidation.get("historical_field_hash") or ""),
        current_source_text=current_source,
        current_source_hash=str(revalidation.get("current_field_hash") or ""),
        reviewed_value=reviewed_value,
        review_decision=str(revalidation.get("review_decision") or "").strip().upper(),
        reviewed_at=str(revalidation.get("reviewed_at") or ""),
        review_evidence_id=str(revalidation.get("review_artifact") or ""),
        owner_decision=owner_decision,
        owner_note=str(owner_row.get("owner_note") or ""),
        owner_approval_artifact=owner_approval_artifact,
        owner_approval_sha256=owner_approval_sha256,
        master_baseline=str(owner_row.get("master_value") or ""),
        sqlite_baseline="" if current_sqlite_value is None else str(current_sqlite_value),
        sqlite_baseline_present=current_sqlite_value is not None,
        source_revalidated=(
            str(revalidation.get("revalidation_status") or "") == "REVALIDATED_CURRENT"
            and str(revalidation.get("source_text_match") or "") == "YES"
            and str(revalidation.get("source_hash_match") or "") == "YES"
        ),
        source_revalidation_method=LEGACY_SOURCE_REVALIDATION_METHOD,
        revalidation_status=str(revalidation.get("revalidation_status") or ""),
        artifact_conflict=(str(revalidation.get("revalidation_status") or "") == "ARTIFACT_CONFLICT"),
        historical_metadata=dict(historical_metadata or {}),
        legacy_missing_fields=tuple(missing),
        evidence_absence_reason=absence_reason,
    )
    candidate = {
        "provenance_type": LEGACY_PROVENANCE_TYPE,
        "candidate_id": candidate_id,
        "sku": sku,
        "source_field": field_name,
        "source_spanish_value": current_source,
        "source_hash": str(revalidation.get("current_field_hash") or ""),
        "hash_scope": "field",
        "parsed_candidate": reviewed_value,
        "legacy_provenance": provenance.as_dict(),
    }
    owner = {
        "sku": sku,
        "field": field_name,
        "candidate_id": candidate_id,
        "review_id": str(owner_row.get("review_id") or ""),
        "spanish_source": str(owner_row.get("spanish_source") or ""),
        "owner_final_candidate": str(owner_row.get("reviewed_value") or ""),
        "owner_decision": owner_decision,
        "owner_note": str(owner_row.get("owner_note") or ""),
        "owner_approval_artifact": owner_approval_artifact,
        "owner_approval_sha256": owner_approval_sha256,
        "master_value": str(owner_row.get("master_value") or ""),
        "sqlite_value": str(owner_row.get("sqlite_value") or ""),
    }
    return candidate, owner, provenance


def legacy_conflicts(
    provenance: Mapping[str, Any], owner: Mapping[str, Any],
    candidate: Mapping[str, Any], source: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None, *, field_target: str,
) -> list[str]:
    """Validate the complete evidence chain for one legacy preview candidate."""
    conflicts: set[str] = set()
    if provenance.get("provenance_type") != LEGACY_PROVENANCE_TYPE:
        conflicts.add("UNSUPPORTED_PROVENANCE_TYPE")
    sku = str(owner.get("sku") or "").strip()
    canonical_field = str(owner.get("field") or "").strip()
    if not sku or provenance.get("sku") != sku or candidate.get("sku") != sku:
        conflicts.add("FIELD_MISMATCH")
    if not canonical_field or provenance.get("field") != canonical_field or candidate.get("source_field") != canonical_field:
        conflicts.add("FIELD_MISMATCH")

    historical_source = normalize_source_text(provenance.get("historical_source_text"))
    current_source = normalize_source_text(provenance.get("current_source_text"))
    if not historical_source:
        conflicts.add("MISSING_HISTORICAL_SOURCE")
    if not current_source or source is None or canonical_field not in SOURCE_KEY:
        conflicts.add("NO_SOURCE")
    else:
        current_field_source = normalize_source_text(source.get(SOURCE_KEY[canonical_field]))
        if current_field_source != current_source or historical_source != current_source:
            conflicts.add("SOURCE_CHANGED")
        current_hash = field_source_hash({
            "name_es": source.get("name", ""), "cat1_es": source.get("cat1", ""),
            "cat2_es": source.get("cat2", ""), "spec_es": source.get("spec", ""),
            "desc_es": source.get("description", ""), "details_es": source.get("details", ""),
        }, canonical_field)
        if current_hash != str(provenance.get("current_source_hash") or ""):
            conflicts.add("SOURCE_HASH_MISMATCH")
        if current_hash != str(provenance.get("historical_source_hash") or ""):
            conflicts.add("SOURCE_HASH_MISMATCH")
        if current_hash != str(candidate.get("source_hash") or ""):
            conflicts.add("SOURCE_HASH_MISMATCH")
        if candidate.get("hash_scope") != "field":
            conflicts.add("FIELD_HASH_REQUIRED")

    if provenance.get("source_revalidated") is not True or provenance.get("source_revalidation_method") != LEGACY_SOURCE_REVALIDATION_METHOD:
        conflicts.add("SOURCE_NOT_REVALIDATED")
    if provenance.get("revalidation_status") != "REVALIDATED_CURRENT":
        conflicts.add("SOURCE_NOT_REVALIDATED")
    if provenance.get("artifact_conflict") is True:
        conflicts.add("ARTIFACT_CONFLICT")
    flags = tuple(provenance.get("source_conflict_flags") or ())
    if source is not None:
        flags = tuple(sorted(set(flags) | set(source_consistency_flags(source))))
    if flags:
        conflicts.add("SOURCE_CONFLICT")

    decision = str(provenance.get("review_decision") or "").strip().upper()
    if decision == "REVIEW_REQUIRED":
        conflicts.add("REVIEW_REQUIRED")
    elif decision not in LEGACY_ALLOWED_DECISIONS:
        conflicts.add("MISSING_FINAL_REVIEWED_VALUE")
    if not normalize_source_text(provenance.get("reviewed_value")):
        conflicts.add("MISSING_FINAL_REVIEWED_VALUE")
    if (
        not str(provenance.get("artifact_path") or "").strip()
        or not str(provenance.get("artifact_sha256") or "").strip()
        or not str(provenance.get("review_evidence_id") or "").strip()
        or not str(provenance.get("owner_approval_artifact") or "").strip()
        or not str(provenance.get("owner_approval_sha256") or "").strip()
    ):
        conflicts.add("MISSING_CRITICAL_EVIDENCE")
    if not str(provenance.get("reviewed_at") or "").strip():
        conflicts.add("MISSING_CRITICAL_EVIDENCE")

    owner_decision = str(provenance.get("owner_decision") or "").strip().upper()
    if owner_decision == "REJECT":
        conflicts.add("OWNER_REJECTED")
    elif owner_decision == "HOLD":
        conflicts.add("OWNER_HOLD")
    elif owner_decision != "ACCEPT":
        conflicts.add("OWNER_NOT_APPROVED")
    owner_value = normalize_source_text(owner.get("owner_final_candidate"))
    candidate_value = normalize_source_text(candidate.get("parsed_candidate"))
    reviewed_value = normalize_source_text(provenance.get("reviewed_value"))
    if owner_value != reviewed_value or candidate_value != reviewed_value:
        conflicts.add("CANDIDATE_VALUE_MISMATCH")

    baseline = target.get(field_target) if target is not None else None
    if target is None or baseline is None:
        conflicts.add("MISSING_CRITICAL_EVIDENCE")
    elif normalize_source_text(baseline) != normalize_source_text(provenance.get("master_baseline")):
        conflicts.add("MASTER_BASELINE_CHANGED")
    if normalize_source_text(owner.get("master_value")) != normalize_source_text(provenance.get("master_baseline")):
        conflicts.add("MASTER_BASELINE_CHANGED")
    if "sqlite_baseline" not in provenance or provenance.get("sqlite_baseline_present") is not True:
        conflicts.add("MISSING_CRITICAL_EVIDENCE")
    elif normalize_source_text(owner.get("sqlite_value")) != normalize_source_text(provenance.get("sqlite_baseline")):
        conflicts.add("SQLITE_BASELINE_CHANGED")
    return sorted(conflicts)
