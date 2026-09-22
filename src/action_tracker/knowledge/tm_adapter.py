"""Translation Memory V1 -> Dictionary contract adapter.

The adapter is intentionally pure: it materializes only SKU-scoped product
override candidates and leaves descriptions/details in Shadow Termbase.  It
does not mutate CSVs, SQLite, Master, or production configuration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


TM_PRODUCT_FIELD_MAP = {
    "name": "name_zh_standard",
    "cat1": "cat1_zh",
    "cat2": "cat2_zh",
    "spec": "spec_zh_standard",
}
TM_SHADOW_ONLY_FIELDS = frozenset({"description", "details"})


@dataclass(frozen=True)
class ProductOverrideCandidate:
    scope: str
    key: str
    field: str
    value: str
    reason: str
    source: str
    locked: str
    updated_at: str
    source_hash: str
    tm_id: str
    shadow_scope: str

    def as_jsonable(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "key": self.key,
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
            "source": self.source,
            "locked": self.locked,
            "updated_at": self.updated_at,
            "source_hash": self.source_hash,
            "tm_id": self.tm_id,
            "shadow_scope": self.shadow_scope,
        }


def build_product_override_candidates(
    term_rows: Iterable[Mapping[str, Any]],
    staging_rows: Iterable[Mapping[str, Any]],
    *,
    updated_at: str,
) -> tuple[list[ProductOverrideCandidate], list[dict[str, Any]], dict[str, int]]:
    """Materialize APPROVE TM rows into SKU-scoped override candidates.

    ``provenance`` is the only source of SKU identity.  A duplicate SKU/field
    with different values or source hashes is a hard conflict and is never
    emitted as an override.  CONTEXT_ONLY and description/details rows remain
    Shadow-only by design.
    """
    term_rows = list(term_rows)
    staging_rows = list(staging_rows)
    staging_by_tm = {str(row.get("tm_id")): row for row in staging_rows}
    proposed: dict[tuple[str, str], list[ProductOverrideCandidate]] = defaultdict(list)
    shadow_only: list[dict[str, Any]] = []
    for tm in term_rows:
        tm_id = str(tm.get("tm_id") or "")
        field = str(tm.get("field_name") or "")
        decision = str(tm.get("owner_decision") or "")
        staging = staging_by_tm.get(tm_id, {})
        provenance = staging.get("provenance") or []
        if decision != "APPROVE" or field not in TM_PRODUCT_FIELD_MAP:
            shadow_only.append({
                "tm_id": tm_id,
                "field_name": field,
                "owner_decision": decision,
                "reason": "CONTEXT_ONLY_OR_SHADOW_ONLY_FIELD",
            })
            continue
        target_field = TM_PRODUCT_FIELD_MAP[field]
        for item in provenance:
            sku = str(item.get("sku") or "").strip()
            if not sku:
                shadow_only.append({"tm_id": tm_id, "field_name": field, "owner_decision": decision, "reason": "MISSING_PROVENANCE_SKU"})
                continue
            candidate = ProductOverrideCandidate(
                scope="product",
                key=sku,
                field=target_field,
                value=str(tm.get("target_value") or "").strip(),
                reason=f"tm_v1_owner_approved:{tm_id}",
                source="TM_V1_OWNER_APPROVED",
                locked="0",
                updated_at=updated_at,
                source_hash=str(staging.get("source_hash") or ""),
                tm_id=tm_id,
                shadow_scope=str(tm.get("shadow_scope") or "EXACT_GLOBAL_TM"),
            )
            proposed[(sku, target_field)].append(candidate)

    conflicts: list[dict[str, Any]] = []
    candidates: list[ProductOverrideCandidate] = []
    for key, items in sorted(proposed.items()):
        values = {(item.value, item.source_hash) for item in items}
        if len(values) != 1:
            conflicts.append({
                "sku": key[0],
                "field": key[1],
                "candidate_count": len(items),
                "values": sorted({item.value for item in items}),
                "source_hashes": sorted({item.source_hash for item in items}),
                "status": "CONFLICT_REVIEW",
            })
            continue
        candidates.append(items[0])

    counts = {
        "product_override_candidates": len(candidates),
        "shadow_only_rows": len(shadow_only),
        "conflict_rows": len(conflicts),
        "source_tm_rows": len(term_rows),
    }
    return candidates, conflicts + shadow_only, counts
