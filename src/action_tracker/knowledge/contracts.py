"""Stable contracts shared by P3--P6 Knowledge Production V1."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ..services.hashing import localization_source_hash

SPANISH_FIELDS = ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
KNOWLEDGE_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
FIELD_TO_ES = dict(zip(KNOWLEDGE_FIELDS, SPANISH_FIELDS))
KNOWLEDGE_STATES = frozenset({
    "UNCHANGED", "AUTO_READY", "REVIEW_REQUIRED", "SOURCE_BLOCKED",
    "AI_PENDING", "AI_CANDIDATE", "AI_REJECTED", "HUMAN_APPROVED",
    "AUTO_APPROVED", "APPLIED",
})
FIELD_SOURCES = frozenset({
    "manual_override", "product_dictionary", "scoped_dictionary",
    "category_dictionary", "term_dictionary", "model_cache", "ai_candidate",
    "spanish_fallback", "source_blocked", "missing", "human_approved_ai", "auto_approved_ai",
})


def source_hash(record: Mapping[str, Any]) -> str:
    """Hash only the six Spanish fact fields, using stable JSON encoding."""
    return localization_source_hash(dict(record))


@dataclass(frozen=True)
class ResolutionField:
    value: str
    source: str
    status: str

    def __post_init__(self) -> None:
        if self.source not in FIELD_SOURCES:
            raise ValueError(f"unknown field source: {self.source}")
        if self.status not in {"READY", "FALLBACK", "MISSING", "REVIEW"}:
            raise ValueError(f"unknown field status: {self.status}")


@dataclass(frozen=True)
class Resolution:
    sku: str
    source_hash: str
    readiness: str
    fields: Mapping[str, ResolutionField]
    reasons: tuple[str, ...] = ()
    base_commit_id: str | None = None
    dictionary_hash: str | None = None

    def __post_init__(self) -> None:
        if self.readiness not in KNOWLEDGE_STATES:
            raise ValueError(f"unknown readiness: {self.readiness}")
        if set(self.fields) - set(KNOWLEDGE_FIELDS):
            raise ValueError("resolution contains an unknown field")

    def as_jsonable(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "source_hash": self.source_hash,
            "readiness": self.readiness,
            "fields": {
                key: {"value": value.value, "source": value.source, "status": value.status}
                for key, value in self.fields.items()
            },
            "reasons": list(self.reasons),
            "base_commit_id": self.base_commit_id,
            "dictionary_hash": self.dictionary_hash,
        }
