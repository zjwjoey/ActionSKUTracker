"""Same-field QA for explicitly approved translation terminology.

These checks never translate or rewrite. Explicitly forbidden target forms are
hard findings; a missing canonical phrase for semantic terms is only a review
signal because an accepted synonym may be correct.
"""
from __future__ import annotations

import re
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..dictionary import normalize_category_key
from .term_resolver import APPROVED_TERM_STATUSES


_LOCALIZED_FIELDS = frozenset({"name", "spec", "description", "details"})
_REVIEW_TERM_TYPES = frozenset({"material", "attribute", "apparel", "spec"})
_UNIT_TERM_TYPES = frozenset({"unit", "quantity"})
_DEFAULT_REVIEW_POLICY_PATH = Path(__file__).resolve().parents[3] / "config/stage5/approved_term_review_policy.json"
_FALLBACK_REVIEW_POLICY = {
    "canonical_absence_types_by_field": {
        "name": sorted({"material", "apparel"}),
        "spec": sorted({"material", "apparel"}),
        "description": sorted({"material", "apparel"}),
        "details": sorted(_REVIEW_TERM_TYPES),
    },
    "cross_field_types": sorted({"material", "apparel"}),
    "unsupported_target_types": sorted({"material", "apparel"}),
}

# Runtime translation/Stage 5 historically inspected all approved semantic
# term types.  The narrower policy above is intentionally an audit-scope
# policy; it must not silently change candidate generation or existing
# Stage 5 contracts when callers do not opt into it explicitly.
_LEGACY_REVIEW_POLICY = {
    "canonical_absence_types_by_field": {
        field: sorted(_REVIEW_TERM_TYPES)
        for field in _LOCALIZED_FIELDS
    },
    "cross_field_types": sorted(_REVIEW_TERM_TYPES | _UNIT_TERM_TYPES),
    "unsupported_target_types": sorted(_REVIEW_TERM_TYPES),
}


@lru_cache(maxsize=8)
def load_approved_term_review_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the versioned scope for review-only glossary signals.

    A malformed policy falls back to the conservative material/apparel scope;
    it never widens review or authorizes a translation automatically.
    """
    policy_path = Path(path) if path is not None else _DEFAULT_REVIEW_POLICY_PATH
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not str(payload.get("policy_id") or ""):
            raise ValueError("APPROVED_TERM_REVIEW_POLICY_INVALID")
        by_field = payload.get("canonical_absence_types_by_field")
        if not isinstance(by_field, dict) or any(
            field not in _LOCALIZED_FIELDS or not isinstance(values, list)
            for field, values in by_field.items()
        ):
            raise ValueError("APPROVED_TERM_REVIEW_POLICY_SCOPE_INVALID")
        for key in ("cross_field_types", "unsupported_target_types"):
            if not isinstance(payload.get(key), list):
                raise ValueError("APPROVED_TERM_REVIEW_POLICY_TYPES_INVALID")
        return payload
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return {"policy_id": "FALLBACK_CONSERVATIVE", **_FALLBACK_REVIEW_POLICY}


def _review_types(policy: dict[str, Any], key: str, field: str | None = None) -> frozenset[str]:
    if key == "canonical_absence_types_by_field":
        values = (policy.get(key) or {}).get(field or "", ())
    else:
        values = policy.get(key) or ()
    return frozenset(str(value).strip().casefold() for value in values if str(value).strip())


def approved_source_term_ledger(
    source: object, terms: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    """List explicitly approved source facts present in one field.

    This is evidence for a translator/reviewer, not a translation result.  It
    deliberately includes only approved, bounded term types and retains the
    original Spanish term plus its canonical Chinese form.
    """
    source_norm = normalize_category_key(source)
    if not source_norm:
        return []
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    allowed_types = _REVIEW_TERM_TYPES | _UNIT_TERM_TYPES
    for row in terms:
        status = str(row.get("review_status") or "").strip().upper()
        term_type = str(row.get("term_type") or "").strip().casefold()
        source_term = str(row.get("term_es") or "").strip()
        target_term = str(row.get("term_zh") or "").strip()
        normalized_source = normalize_category_key(source_term)
        if (
            status not in APPROVED_TERM_STATUSES
            or term_type not in allowed_types
            or not normalized_source or not target_term
        ):
            continue
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalized_source)}(?![a-z0-9])")
        if not pattern.search(source_norm):
            continue
        key = (normalized_source, term_type)
        entry = rows.setdefault(key, {
            "source_term": source_term,
            "term_type": term_type,
            "translations": {},
        })
        entry["translations"].setdefault(normalize_category_key(target_term), target_term)
    output = []
    for key in sorted(rows):
        entry = rows[key]
        translations = [entry["translations"][item] for item in sorted(entry["translations"])]
        output.append({
            "source_term": entry["source_term"],
            "term_type": entry["term_type"],
            "approved_translation": translations[0] if len(translations) == 1 else "",
            "translation_candidates": translations,
            "ambiguous_mapping": len(translations) > 1,
        })
    return output


def inspect_approved_term_candidate(
    field: str, source: object, target: object, terms: Iterable[dict[str, Any]],
    *, review_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return approved-term findings for one source/target field pair."""
    if field not in _LOCALIZED_FIELDS:
        return []
    source_norm = normalize_category_key(source)
    target_norm = normalize_category_key(target)
    findings: list[dict[str, Any]] = []
    # Only callers that explicitly pass a versioned policy (the read-only
    # audit does so) should narrow review scope.  Preserve the legacy runtime
    # behavior for Stage 5 and model candidate inspection.
    policy = review_policy if review_policy is not None else _LEGACY_REVIEW_POLICY
    for row in terms:
        if str(row.get("review_status") or "").strip().upper() not in APPROVED_TERM_STATUSES:
            continue
        source_term = normalize_category_key(row.get("term_es"))
        canonical = normalize_category_key(row.get("term_zh"))
        term_type = str(row.get("term_type") or "").strip().casefold()
        if not source_term:
            continue
        if term_type in _UNIT_TERM_TYPES and field not in {"spec", "description", "details"}:
            continue
        source_pattern = re.compile(rf"(?<![a-z0-9]){re.escape(source_term)}(?![a-z0-9])")
        if not source_pattern.search(source_norm):
            continue
        forbidden = [
            normalize_category_key(value)
            for value in str(row.get("forbidden_zh") or "").split("|")
            if normalize_category_key(value)
        ]
        for forbidden_value in forbidden:
            if forbidden_value in target_norm:
                findings.append({
                    "code": "TERM_FORBIDDEN_TRANSLATION", "field": field,
                    "source_term": row.get("term_es", ""),
                    "forbidden_candidate": forbidden_value,
                    "approved_translation": row.get("term_zh", ""),
                    "review_only": False,
                })
        if term_type in _review_types(policy, "canonical_absence_types_by_field", field) and canonical and canonical not in target_norm:
            findings.append({
                "code": "APPROVED_TERM_CANONICAL_ABSENT_REVIEW", "field": field,
                "source_term": row.get("term_es", ""), "term_type": term_type,
                "approved_translation": row.get("term_zh", ""),
                "candidate": str(target or "")[:240], "review_only": True,
                "canonical_absence_is_not_proof_of_error": True,
            })
    return findings


def inspect_approved_term_cross_field_movement(
    source_fields: dict[str, object], target_fields: dict[str, object],
    terms: Iterable[dict[str, Any]], *, review_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Flag likely movement of a glossary-backed fact between localized fields.

    These are review-only: the approved phrase may be supported by an unmodeled
    synonym in the destination source field, or a glossary phrase may be a
    false positive in free text. Findings are evidence of risk, not proof that
    the target phrase is false.
    """
    findings: list[dict[str, Any]] = []
    # See inspect_approved_term_candidate: policy narrowing is opt-in for
    # audit callers and must not alter the established Stage 5 behavior.
    policy = review_policy if review_policy is not None else _LEGACY_REVIEW_POLICY
    cross_field_types = _review_types(policy, "cross_field_types")
    unsupported_target_types = _review_types(policy, "unsupported_target_types")
    seen: set[tuple[str, str, str, str]] = set()
    for row in terms:
        if str(row.get("review_status") or "").strip().upper() not in APPROVED_TERM_STATUSES:
            continue
        term_type = str(row.get("term_type") or "").strip().casefold()
        if term_type not in _REVIEW_TERM_TYPES | _UNIT_TERM_TYPES:
            continue
        source_term = normalize_category_key(row.get("term_es"))
        canonical = normalize_category_key(row.get("term_zh"))
        if not source_term or not canonical:
            continue
        source_pattern = re.compile(rf"(?<![a-z0-9]){re.escape(source_term)}(?![a-z0-9])")
        source_fields_with_term = {
            field for field in _LOCALIZED_FIELDS
            if source_pattern.search(normalize_category_key(source_fields.get(field)))
        }
        if not source_fields_with_term:
            if term_type in unsupported_target_types:
                for target_field in _LOCALIZED_FIELDS:
                    if canonical not in normalize_category_key(target_fields.get(target_field)):
                        continue
                    identity = ("", target_field, source_term, canonical)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    findings.append({
                        "code": "APPROVED_TERM_UNSUPPORTED_TARGET_REVIEW",
                        "field": target_field,
                        "source_field": "",
                        "source_term": row.get("term_es", ""),
                        "term_type": term_type,
                        "approved_translation": row.get("term_zh", ""),
                        "source_value": "",
                        "candidate": str(target_fields.get(target_field) or "")[:240],
                        "review_only": True,
                        "source_term_absent_from_all_localized_fields": True,
                        "unsupported_target_is_not_proof_of_hallucination": True,
                    })
            continue
        if term_type not in cross_field_types:
            continue
        for source_field in source_fields_with_term:
            for target_field in _LOCALIZED_FIELDS:
                if target_field == source_field or target_field in source_fields_with_term:
                    continue
                if canonical not in normalize_category_key(target_fields.get(target_field)):
                    continue
                identity = (source_field, target_field, source_term, canonical)
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append({
                    "code": "APPROVED_TERM_CROSS_FIELD_REVIEW",
                    "field": target_field,
                    "source_field": source_field,
                    "source_term": row.get("term_es", ""),
                    "term_type": term_type,
                    "approved_translation": row.get("term_zh", ""),
                    "source_value": str(source_fields.get(source_field) or "")[:240],
                    "candidate": str(target_fields.get(target_field) or "")[:240],
                    "review_only": True,
                    "cross_field_support_not_proven": True,
                })
    return findings


__all__ = [
    "approved_source_term_ledger", "inspect_approved_term_candidate",
    "inspect_approved_term_cross_field_movement", "load_approved_term_review_policy",
]
