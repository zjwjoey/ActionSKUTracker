"""Deterministic, fail-closed scoped dictionary matching.

Rules are data, not code.  Only human-approved rules are eligible for a
production resolution; callers may still use this module in shadow mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

_ORDER = {
    ("PRODUCT", None): 700,
    ("CAT2", "FIELD"): 600,
    ("CAT2", None): 500,
    ("CAT1", "FIELD"): 400,
    ("CAT1", None): 300,
    ("FIELD", None): 200,
    ("GLOBAL", None): 100,
}


@dataclass(frozen=True)
class ScopedRule:
    rule_id: str
    scope_type: str
    scope_value: str | None
    field: str
    source_value: str | None
    target_value: str
    review_status: str = "PENDING"
    enabled: bool = True

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "ScopedRule":
        return cls(str(row.get("rule_id") or ""), str(row.get("scope_type") or "GLOBAL").upper(),
                   str(row.get("scope_value")) if row.get("scope_value") is not None else None,
                   str(row.get("field") or ""), str(row.get("source_value")) if row.get("source_value") is not None else None,
                   str(row.get("target_value") or ""), str(row.get("review_status") or "PENDING").upper(),
                   bool(row.get("enabled", True)))

    def specificity(self) -> int:
        scope = self.scope_type.upper()
        if scope == "PRODUCT": key = (scope, None)
        elif scope in {"CAT1", "CAT2"} and self.field: key = (scope, "FIELD")
        else: key = (scope, None)
        return _ORDER.get(key, -1)

    def matches(self, record: Mapping[str, Any], field: str) -> bool:
        if not self.enabled or self.field != field or self.specificity() < 0:
            return False
        if self.review_status not in {"HUMAN_APPROVED", "APPROVED"}:
            return False
        if self.source_value is not None and str(record.get("source_term") or "").strip() != self.source_value:
            return False
        scope = self.scope_type.upper()
        if scope == "GLOBAL": return True
        if scope == "FIELD": return True
        if scope == "PRODUCT": return str(record.get("sku") or record.get("official_sku") or "") == str(self.scope_value or "")
        if scope == "CAT1": return str(record.get("cat1_es") or "") == str(self.scope_value or "")
        if scope == "CAT2": return str(record.get("cat2_es") or "") == str(self.scope_value or "")
        return False


@dataclass(frozen=True)
class ScopedMatch:
    value: str
    rule: ScopedRule | None
    conflict: bool = False
    candidates: tuple[str, ...] = ()


def match_scoped(record: Mapping[str, Any], field: str, rules: Iterable[ScopedRule | Mapping[str, Any]]) -> ScopedMatch:
    """Select the most specific approved rule; same-level disagreement fails closed."""
    eligible = [r if isinstance(r, ScopedRule) else ScopedRule.from_mapping(r) for r in rules]
    hits = [r for r in eligible if r.matches(record, field)]
    if not hits:
        return ScopedMatch("", None)
    best = max(r.specificity() for r in hits)
    top = [r for r in hits if r.specificity() == best]
    values = tuple(sorted({r.target_value for r in top}))
    if len(values) > 1:
        return ScopedMatch("", None, True, values)
    chosen = sorted(top, key=lambda r: r.rule_id)[0]
    return ScopedMatch(chosen.target_value, chosen)


def blast_radius(records: Iterable[Mapping[str, Any]], rule: ScopedRule) -> dict[str, Any]:
    rows = [record for record in records if rule.matches(record, rule.field)]
    return {"rule_id": rule.rule_id, "matched_sku_count": len(rows),
            "affected_fields": [rule.field] if rows else [],
            "sample_skus": [str(r.get("sku") or r.get("official_sku")) for r in rows[:10]],
            "cat1_distribution": _distribution(rows, "cat1_es"),
            "cat2_distribution": _distribution(rows, "cat2_es")}


def _distribution(rows: list[Mapping[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        result[value] = result.get(value, 0) + 1
    return result
