"""Safety validator for model translation candidates."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import KNOWLEDGE_FIELDS

_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class CandidateValidation:
    ok: bool
    reasons: tuple[str, ...]


def validate_candidate(candidate: Mapping[str, Any], record: Mapping[str, Any]) -> CandidateValidation:
    reasons: list[str] = []
    if not str(candidate.get("sku") or candidate.get("official_sku") or "").strip() == str(record.get("sku") or record.get("official_sku") or "").strip():
        reasons.append("SKU_MISMATCH")
    if str(candidate.get("source_hash") or "") != _source_hash(record):
        reasons.append("SOURCE_HASH_MISMATCH")
    fields = candidate.get("fields")
    if not isinstance(fields, Mapping):
        reasons.append("FIELDS_NOT_OBJECT")
        fields = {}
    unknown = set(fields) - set(KNOWLEDGE_FIELDS)
    if unknown:
        reasons.append("UNKNOWN_FIELD")
    for field, value in fields.items():
        if field not in KNOWLEDGE_FIELDS:
            continue
        if value is None:
            continue
        if not isinstance(value, str):
            reasons.append(f"{field.upper()}_NOT_STRING")
        elif len(value) > 4000:
            reasons.append(f"{field.upper()}_TOO_LONG")
        elif _URL_RE.search(value):
            reasons.append(f"{field.upper()}_URL_CONTAMINATION")
        else:
            # A translation may change language, but it must not silently
            # drop numeric facts from the corresponding Spanish field.
            source_field = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es",
                            "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]
            expected = set(_NUMBER_RE.findall(str(record.get(source_field) or "")))
            found = set(_NUMBER_RE.findall(value))
            if expected - found:
                reasons.append(f"{field.upper()}_NUMBER_DROPPED")
    confidence = candidate.get("confidence")
    if confidence is not None:
        try:
            if not 0 <= float(confidence) <= 1:
                reasons.append("CONFIDENCE_OUT_OF_RANGE")
        except (TypeError, ValueError):
            reasons.append("CONFIDENCE_INVALID")
    return CandidateValidation(not reasons, tuple(dict.fromkeys(reasons)))


def _source_hash(record: Mapping[str, Any]) -> str:
    from .contracts import source_hash
    return source_hash(record)
