from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _clean(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return sorted({_clean(v) for v in value if str(v).strip()})
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in sorted(value.items()) if v not in (None, "", [], ())}
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    return value


@dataclass(frozen=True)
class ExtractionQuery:
    keyword: str | None = None
    canonical_id: str | None = None
    canonical_ids: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ("CURRENT",)
    cat1: tuple[str, ...] = ()
    cat2: tuple[str, ...] = ()
    min_price: float | None = None
    max_price: float | None = None
    has_original_price: bool | None = None
    promotion: bool | None = None
    new_badge: bool | None = None
    sustainable: bool | None = None
    price_change: str | None = None
    min_change_amount: float | None = None
    min_change_percent: float | None = None
    event_types: tuple[str, ...] = ()
    historical_low_min: float | None = None
    historical_low_max: float | None = None
    historical_high_min: float | None = None
    historical_high_max: float | None = None
    first_seen_from: str | None = None
    first_seen_to: str | None = None
    last_seen_from: str | None = None
    last_seen_to: str | None = None
    event_from: str | None = None
    event_to: str | None = None
    event_last_n_days: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    last_n_days: int | None = None
    image_statuses: tuple[str, ...] = ()
    has_image: bool | None = None
    image_ready_for_export: bool | None = None
    localization_status: str | None = None
    missing_fields: tuple[str, ...] = ()
    sort: str = "sku"
    descending: bool = False
    limit: int = 100
    offset: int = 0

    def normalized(self) -> dict[str, Any]:
        values = dict(self.__dict__)
        # Canonicalize compatibility aliases so equivalent callers produce
        # the same query hash and persisted Selection query.
        if values.get("canonical_id") and not values.get("canonical_ids"):
            values["canonical_ids"] = (values["canonical_id"],)
        values.pop("canonical_id", None)
        if values.get("date_from") and not values.get("last_seen_from"):
            values["last_seen_from"] = values["date_from"]
        if values.get("date_to") and not values.get("last_seen_to"):
            values["last_seen_to"] = values["date_to"]
        values.pop("date_from", None); values.pop("date_to", None)
        return _clean({k: v for k, v in values.items() if v not in (None, "", (), [])})

    def canonical_json(self) -> str:
        return json.dumps(self.normalized(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def query_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractionQuery":
        values = dict(payload or {})
        if values.get("canonical_id") and not values.get("canonical_ids"):
            values["canonical_ids"] = (values["canonical_id"],)
        tuple_fields = {"canonical_ids", "skus", "statuses", "cat1", "cat2", "event_types", "image_statuses", "missing_fields"}
        for key in tuple_fields:
            value = values.get(key, ("CURRENT",) if key == "statuses" else ())
            if isinstance(value, str):
                value = tuple(x.strip() for x in value.split(",") if x.strip())
            else:
                value = tuple(value or ())
            if key == "statuses" and not value:
                value = ("CURRENT",)
            values[key] = value
        return cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ExtractionResult:
    query: dict[str, Any]
    query_hash: str
    matched_count: int
    items: tuple[dict[str, Any], ...]
    sort: dict[str, Any]
    pagination: dict[str, Any]
    source_commit_id: str | None
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.query, "query_hash": self.query_hash, "matched_count": self.matched_count,
                "items": list(self.items), "sort": self.sort, "pagination": self.pagination,
                "source_commit_id": self.source_commit_id, "generated_at": self.generated_at}
