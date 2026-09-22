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


def normalize_category(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


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
