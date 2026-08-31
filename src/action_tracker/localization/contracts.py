from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

POLICY_VERSION = "CHINESE_LOCALIZATION_STANDARD_V1"
LOCALIZATION_FIELDS = (
    "name_zh", "cat1_zh", "cat2_zh", "spec_zh", "unit_price_zh", "desc_zh", "details_zh",
)
SEMANTIC_TYPES = (
    "PRODUCT_TYPE", "BRAND", "SERIES", "IP_CHARACTER", "MODEL", "TECH_TOKEN",
    "STANDARD_UNIT", "SIZE_DIMENSION", "CAPACITY", "WEIGHT", "QUANTITY", "COLOR",
    "VARIANT", "MATERIAL", "FUNCTION", "COMPATIBILITY", "VOLTAGE", "POWER",
    "CURRENT", "FREQUENCY", "BATTERY_CAPACITY", "SOCKET", "INTERFACE",
    "PROTECTION_RATING", "CARE", "NUTRITION", "DETAIL_KEY", "DESCRIPTION_FACT",
)


def source_hash(record: Mapping[str, Any]) -> str:
    """Stable hash of official Spanish facts only (never Chinese fields)."""
    payload = {
        key: str(record.get(key) or "")
        for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es")
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class SourceFacts:
    sku: str
    canonical_id: str = ""
    name_es: str = ""
    cat1_es: str = ""
    cat2_es: str = ""
    spec_es: str = ""
    unit_price_es: str = ""
    desc_es: str = ""
    details_es: str = ""
    product_url: str = ""
    current_price: Any = None
    original_price: Any = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "SourceFacts":
        return cls(
            sku=str(record.get("sku") or record.get("official_sku") or "").strip(),
            canonical_id=str(record.get("canonical_id") or "").strip(),
            name_es=str(record.get("name_es") or "").strip(),
            cat1_es=str(record.get("cat1_es") or "").strip(),
            cat2_es=str(record.get("cat2_es") or "").strip(),
            spec_es=str(record.get("spec_es") or "").strip(),
            unit_price_es=str(record.get("unit_price_es") or record.get("unit_price") or "").strip(),
            desc_es=str(record.get("desc_es") or record.get("description_es") or "").strip(),
            details_es=str(record.get("details_es") or record.get("product_details_es") or "").strip(),
            product_url=str(record.get("product_url") or "").strip(),
            current_price=record.get("current_price"), original_price=record.get("original_price"),
        )

    def as_record(self) -> dict[str, Any]:
        return {"sku": self.sku, "canonical_id": self.canonical_id, "name_es": self.name_es,
                "cat1_es": self.cat1_es, "cat2_es": self.cat2_es, "spec_es": self.spec_es,
                "unit_price_es": self.unit_price_es, "desc_es": self.desc_es, "details_es": self.details_es,
                "product_url": self.product_url, "current_price": self.current_price, "original_price": self.original_price}

    @property
    def source_hash(self) -> str:
        return source_hash(self.as_record())


@dataclass(frozen=True)
class SemanticFact:
    semantic_type: str
    source_text: str
    value: str
    canonical_value: str = ""
    source_field: str = ""
    evidence: str = ""
    confidence: float = 1.0
    placement: str = ""

    def __post_init__(self) -> None:
        if self.semantic_type not in SEMANTIC_TYPES:
            raise ValueError(f"UNKNOWN_SEMANTIC_TYPE:{self.semantic_type}")

    def as_dict(self) -> dict[str, Any]:
        return {"semantic_type": self.semantic_type, "source_text": self.source_text,
                "value": self.value, "canonical_value": self.canonical_value,
                "source_field": self.source_field, "evidence": self.evidence,
                "confidence": self.confidence, "placement": self.placement}


@dataclass(frozen=True)
class LocalizationField:
    value: str
    source: str = "missing"
    status: str = "REVIEW_REQUIRED"
    source_hash: str = ""
    freshness_status: str = "CURRENT"
    policy_version: str = POLICY_VERSION
    review_reasons: tuple[str, ...] = ()
    provenance: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "status": self.status,
                "source_hash": self.source_hash, "freshness_status": self.freshness_status,
                "policy_version": self.policy_version, "review_reasons": list(self.review_reasons),
                "provenance": list(self.provenance)}


@dataclass(frozen=True)
class LocalizationPlan:
    sku: str
    source_hash: str
    fields: Mapping[str, LocalizationField]
    semantic_facts: tuple[SemanticFact, ...] = ()
    readiness: str = "REVIEW_REQUIRED"
    review_reasons: tuple[str, ...] = ()
    knowledge_hits: tuple[str, ...] = ()
    ai_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"sku": self.sku, "source_hash": self.source_hash,
                "fields": {k: v.as_dict() for k, v in self.fields.items()},
                "semantic_facts": [f.as_dict() for f in self.semantic_facts],
                "readiness": self.readiness, "review_reasons": list(self.review_reasons),
                "knowledge_hits": list(self.knowledge_hits), "ai_used": self.ai_used}
