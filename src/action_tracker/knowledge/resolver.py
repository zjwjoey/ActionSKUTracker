"""Pure, field-level Knowledge Resolver for P3--P6."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import FIELD_TO_ES, KNOWLEDGE_FIELDS, Resolution, ResolutionField, source_hash
from .scoped import match_scoped


def _value(mapping: Mapping[str, Any] | None, field: str) -> str:
    if not mapping:
        return ""
    if isinstance(mapping, str):
        return mapping.strip() if field in {"name", "cat1", "cat2", "spec", "description", "details"} else ""
    if not isinstance(mapping, Mapping):
        return ""
    value = mapping.get(field)
    if isinstance(value, Mapping):
        value = value.get("value")
    return str(value or "").strip()


def _source_quality(record: Mapping[str, Any]) -> str:
    return str(record.get("source_quality") or record.get("source_quality_status") or "OK").upper()


def _approved(mapping: Mapping[str, Any] | str | None, field: str) -> str:
    if not mapping:
        return ""
    if isinstance(mapping, str):
        return mapping.strip()
    status = str(mapping.get("review_status") or mapping.get("approval_status") or "").upper()
    if status and status not in {"APPROVED", "HUMAN_APPROVED", "CONFIRMED", "LOCKED"}:
        return ""
    return _value(mapping, field)


def resolve(
    record: Mapping[str, Any],
    *,
    manual: Mapping[str, Any] | None = None,
    product: Mapping[str, Any] | None = None,
    scoped: Mapping[str, Any] | None = None,
    dictionaries: Mapping[str, Any] | None = None,
    model_cache: Mapping[str, Any] | None = None,
    ai_candidate: Mapping[str, Any] | None = None,
    base_commit_id: str | None = None,
    dictionary_hash: str | None = None,
) -> Resolution:
    """Resolve one product without side effects.

    Priority is field-level and intentionally mirrors the production plan:
    manual > product > scoped > dictionaries > validated model cache > ES
    fallback.  A source-blocked record never falls back to AI or Spanish text.
    """
    sku = str(record.get("sku") or record.get("official_sku") or "").strip()
    h = source_hash(record)
    blocked = _source_quality(record) in {"SOURCE_BLOCKED", "SOURCE_DAMAGED", "SOURCE_POLLUTED", "SOURCE_UNTRUSTED"}
    reasons: list[str] = []
    fields: dict[str, ResolutionField] = {}
    cache_hash = str((model_cache or {}).get("source_hash") or "")
    cache_valid = bool(model_cache and cache_hash == h and str((model_cache or {}).get("validation_status") or "").upper() == "PASS")

    for field in KNOWLEDGE_FIELDS:
        scoped_value = ""
        if isinstance(scoped, Mapping):
            scoped_value = _approved(scoped, field)
        elif scoped:
            match = match_scoped(record, field, scoped)  # type: ignore[arg-type]
            if match.conflict:
                reasons.append(f"{field.upper()}_SCOPED_CONFLICT")
            scoped_value = match.value
        dictionary_value = _approved((dictionaries or {}).get(field) if dictionaries else None, field)
        candidates = (
            ("manual_override", _value(manual, field)),
            ("product_dictionary", _approved(product, field)),
            ("scoped_dictionary", scoped_value),
            ("term_dictionary", dictionary_value),
            ("model_cache", _value(model_cache, field) if cache_valid else ""),
            ("ai_candidate", _value(ai_candidate, field) if ai_candidate else ""),
        )
        selected_source, selected = next(((src, val) for src, val in candidates if val), ("missing", ""))
        if selected:
            fields[field] = ResolutionField(selected, selected_source, "READY")
            continue
        if blocked:
            fields[field] = ResolutionField("", "source_blocked", "MISSING")
            continue
        fallback = str(record.get(FIELD_TO_ES[field]) or "").strip()
        if fallback:
            fields[field] = ResolutionField(fallback, "spanish_fallback", "FALLBACK")
            reasons.append(f"{field.upper()}_FALLBACK")
        else:
            fields[field] = ResolutionField("", "missing", "MISSING")
            reasons.append(f"{field.upper()}_MISSING")

    if blocked:
        readiness = "SOURCE_BLOCKED"
        reasons.insert(0, "SOURCE_BLOCKED")
    elif any("_SCOPED_CONFLICT" in reason for reason in reasons):
        readiness = "REVIEW_REQUIRED"
    elif all(item.status == "READY" for item in fields.values()):
        readiness = "AUTO_READY"
    else:
        # Unresolved but valid Spanish facts are queued for incremental AI;
        # explicit human conflicts can be represented by callers as review.
        readiness = "AI_PENDING"
    return Resolution(sku, h, readiness, fields, tuple(dict.fromkeys(reasons)), base_commit_id, dictionary_hash)
