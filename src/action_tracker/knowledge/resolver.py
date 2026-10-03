"""Pure, field-level Knowledge Resolver for P3--P6."""
from __future__ import annotations

from typing import Any, Mapping

from .contracts import FIELD_TO_ES, KNOWLEDGE_FIELDS, Resolution, ResolutionField, source_hash
from ..services.hashing import localization_source_hash_matches

_APPROVED_DICTIONARY_STATUSES = frozenset({
    "APPROVED", "HUMAN_APPROVED", "CONFIRMED", "HUMAN_REVIEWED", "LOCKED", "SEED_REVIEWED",
})


def _value(mapping: Mapping[str, Any] | None, field: str) -> str:
    """Read a plain value without treating it as approval evidence."""
    if not isinstance(mapping, Mapping):
        return ""
    value = mapping.get(field)
    if isinstance(value, Mapping):
        value = value.get("value")
    return str(value or "").strip()


def _dictionary_value(mapping: Mapping[str, Any] | None, field: str) -> tuple[str, bool]:
    """Return a field value and whether its explicit review status approves it.

    A flat string is useful as a review candidate, but carries no provenance
    that an Owner reviewed its meaning. Per-field metadata takes precedence;
    a shared status is accepted only for a flat row-level dictionary record.
    """
    if isinstance(mapping, str):
        return mapping.strip(), False
    if not isinstance(mapping, Mapping):
        return "", False
    raw = mapping.get(field)
    if raw is None and "value" in mapping:
        raw = mapping
    if isinstance(raw, Mapping):
        value = str(raw.get("value") or "").strip()
        status = str(raw.get("approval_status") or raw.get("review_status") or "").strip().upper()
    else:
        value = str(raw or "").strip()
        status = str(mapping.get("approval_status") or mapping.get("review_status") or "").strip().upper()
    return value, bool(value and status in _APPROVED_DICTIONARY_STATUSES)


def _source_quality(record: Mapping[str, Any]) -> str:
    return str(record.get("source_quality") or record.get("source_quality_status") or "OK").upper()


def resolve(
    record: Mapping[str, Any],
    *,
    manual: Mapping[str, Any] | None = None,
    product: Mapping[str, Any] | None = None,
    scoped: Mapping[str, Any] | None = None,
    dictionaries: Mapping[str, Any] | None = None,
    model_cache: Mapping[str, Any] | None = None,
    base_commit_id: str | None = None,
    dictionary_hash: str | None = None,
) -> Resolution:
    """Resolve one product without side effects.

    Priority is field-level: manual > approved product > approved scoped >
    approved term dictionary > review candidates > Spanish fallback. Plain
    dictionary strings carry no Owner approval and remain review candidates.
    A validated model cache is also only a review candidate; validation proves
    structural/source consistency, not semantic Owner approval. A source-
    blocked record never falls back to AI or Spanish text.
    """
    sku = str(record.get("sku") or record.get("official_sku") or "").strip()
    h = source_hash(record)
    blocked = _source_quality(record) in {"SOURCE_BLOCKED", "SOURCE_DAMAGED", "SOURCE_POLLUTED", "SOURCE_UNTRUSTED"}
    reasons: list[str] = []
    fields: dict[str, ResolutionField] = {}
    cache_hash = str((model_cache or {}).get("source_hash") or "")
    cache_valid = bool(
        model_cache
        and localization_source_hash_matches(dict(record), cache_hash)
        and str((model_cache or {}).get("validation_status") or "").upper() == "PASS"
    )

    for field in KNOWLEDGE_FIELDS:
        manual_value = _value(manual, field)
        if manual_value:
            fields[field] = ResolutionField(manual_value, "manual_override", "READY")
            continue
        if blocked:
            fields[field] = ResolutionField("", "source_blocked", "MISSING")
            continue

        dictionary_candidates = (
            ("product_dictionary", product),
            ("scoped_dictionary", scoped),
            ("term_dictionary", (dictionaries or {}).get(field) if dictionaries else None),
        )
        selected = False
        for source, mapping in dictionary_candidates:
            candidate, approved = _dictionary_value(mapping, field)
            if not candidate:
                continue
            if approved:
                fields[field] = ResolutionField(candidate, source, "READY")
            else:
                fields[field] = ResolutionField(candidate, source, "REVIEW")
                reasons.append(f"{field.upper()}_{source.upper()}_OWNER_REVIEW_REQUIRED")
            selected = True
            break
        if selected:
            continue

        cached_candidate = _value(model_cache, field) if cache_valid else ""
        if cached_candidate:
            fields[field] = ResolutionField(cached_candidate, "model_cache", "REVIEW")
            reasons.append(f"{field.upper()}_OWNER_REVIEW_REQUIRED")
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
    elif any(item.status == "REVIEW" for item in fields.values()):
        readiness = "REVIEW_REQUIRED"
    elif all(item.status == "READY" for item in fields.values()):
        readiness = "AUTO_READY"
    else:
        # Unresolved but valid Spanish facts are queued for incremental AI;
        # explicit human conflicts can be represented by callers as review.
        readiness = "AI_PENDING"
    return Resolution(sku, h, readiness, fields, tuple(dict.fromkeys(reasons)), base_commit_id, dictionary_hash)
