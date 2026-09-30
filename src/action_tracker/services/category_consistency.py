"""Checks for impossible/known cross-level category combinations.

Listing pages can expose the same SKU from more than one top-level entry.  A
listing card therefore cannot be allowed to overwrite an existing official
breadcrumb category.  This module only provides a conservative consistency
hint: a primary mapping is used to queue a detail refresh, never to fabricate
or silently overwrite an official category value.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


_NAVIGATION_CATEGORIES = {
    "atras", "atrás", "volver", "back", "home", "inicio", "productos",
    "cerrar", "ir al contenido principal",
}


def normalize_category(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def is_navigation_category(value: Any) -> bool:
    """Return whether a category value is a generic page-navigation label."""
    return normalize_category(value) in {normalize_category(item) for item in _NAVIGATION_CATEGORIES}


def is_valid_primary_category(value: Any) -> bool:
    """Validate cat1 against the configured official 15 product categories."""
    normalized = normalize_category(value)
    if not normalized:
        return False
    # Import lazily to keep this small validator independent of listing startup.
    from ..monitor.listing import CATEGORY_LABELS

    valid = {
        normalize_category(label)
        for key, label in CATEGORY_LABELS.items()
        if key not in {"nuevo", "promocion-semanal"}
    }
    return normalized in valid


def invalid_category_fields(record: dict[str, Any]) -> set[str]:
    """Identify persisted category fields that are clearly not page facts."""
    name = normalize_category(record.get("name_es"))
    cat1 = normalize_category(record.get("cat1_es"))
    cat2 = normalize_category(record.get("cat2_es"))
    invalid: set[str] = set()
    if cat1 and (not is_valid_primary_category(cat1) or cat1 == name):
        invalid.add("cat1_es")
    if cat2 and (is_navigation_category(cat2) or cat2 == name):
        invalid.add("cat2_es")
    return invalid


def load_primary_category_map(path: Path | str | None) -> dict[str, str]:
    """Load ``cat2_es -> primary_cat1_es`` evidence, failing closed.

    A missing or malformed optional map returns an empty mapping.  The daily
    run must remain able to collect facts when the review-derived hint is not
    available; no category is invented here.
    """
    if not path:
        return {}
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            result: dict[str, str] = {}
            for row in rows:
                cat2 = normalize_category(row.get("cat2_es"))
                cat1 = " ".join(str(row.get("primary_cat1_es") or "").split()).strip()
                evidence = str(row.get("evidence_source") or "").strip().upper()
                # Review workbooks and inference are useful for a queue, but
                # are not authoritative taxonomy evidence.  Only an explicit
                # official breadcrumb/category-page or owner-approved source
                # can activate a production consistency hint.
                trusted = any(token in evidence for token in (
                    "OFFICIAL", "BREADCRUMB", "CATEGORY_PAGE", "OWNER_APPROVED",
                ))
                if cat2 and cat1 and trusted:
                    result[cat2] = cat1
            return result
    except (OSError, UnicodeError, csv.Error):
        return {}


def category_mismatch(record: dict[str, Any], primary_map: dict[str, str] | None) -> bool:
    """Return whether a non-empty cat2 has a known conflicting primary cat1."""
    if not primary_map:
        return False
    cat2 = normalize_category(record.get("cat2_es"))
    cat1 = normalize_category(record.get("cat1_es"))
    expected = normalize_category(primary_map.get(cat2))
    return bool(cat2 and cat1 and expected and cat1 != expected)
