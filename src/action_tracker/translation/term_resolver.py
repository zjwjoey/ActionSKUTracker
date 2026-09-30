"""Conservative exact-cell resolution for approved Spanish glossary terms."""
from __future__ import annotations

from typing import Any

from ..dictionary import normalize_category_key

TERM_RESOLVER_VERSION = "approved-exact-spec-term-v1"
APPROVED_TERM_STATUSES = frozenset({
    "SEED_REVIEWED", "HUMAN_REVIEWED", "HUMAN_APPROVED", "LOCKED", "APPROVED", "CONFIRMED",
})
EXACT_SPEC_TERM_TYPES = frozenset({"spec", "material", "attribute", "apparel", "quantity", "unit"})


def resolve_exact_spec_term(
    source_value: object, terms: tuple[dict[str, str], ...],
) -> tuple[str, str] | None:
    """Resolve only a whole-cell, approved, unambiguous term; never substring-rewrite."""
    source_key = normalize_category_key(source_value)
    if not source_key:
        return None
    matches: list[tuple[str, str]] = []
    for row in terms:
        if normalize_category_key(row.get("term_es")) != source_key:
            continue
        status = str(row.get("review_status") or "").strip().upper()
        term_type = str(row.get("term_type") or "").strip().casefold()
        if status not in APPROVED_TERM_STATUSES or term_type not in EXACT_SPEC_TERM_TYPES:
            continue
        term_es = str(row.get("term_es") or "").strip()
        term_zh = str(row.get("term_zh") or "").strip()
        keep_original = str(row.get("keep_original") or "").strip().casefold() in {"1", "true", "yes", "locked"}
        value = term_es if keep_original else term_zh
        if value:
            matches.append((value, status))
    distinct_values = {normalize_category_key(value) for value, _ in matches}
    if len(distinct_values) != 1 or not matches:
        return None
    return matches[0]


__all__ = [
    "APPROVED_TERM_STATUSES", "EXACT_SPEC_TERM_TYPES", "TERM_RESOLVER_VERSION",
    "resolve_exact_spec_term",
]
