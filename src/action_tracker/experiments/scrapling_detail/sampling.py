"""Reproducible cat1-stratified SKU sampling from a validated CURRENT snapshot."""
from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any, Iterable


_URL_SKU = re.compile(r"/p/(\d+)(?:/|$)", re.I)
_PROMO = re.compile(r"nuevo|promoci[oó]n semanal", re.I)


def select_sample(
    rows: Iterable[dict[str, Any]],
    *,
    categories: list[str],
    limit: int = 30,
    seed: int = 20260924,
    requested_skus: set[str] | None = None,
    requested_category: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sample evenly by recognized cat1, then reserve slots for rare patterns.

    Each category receives floor(limit/category_count) records; remainder slots
    follow the seeded shuffle of category names. Missing/unknown categories and
    invalid product URLs are excluded and counted in the returned diagnostics.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    rng = random.Random(seed)
    category_order = list(dict.fromkeys(categories))
    if requested_category:
        if requested_category not in category_order:
            raise ValueError(f"unknown category: {requested_category}")
        category_order = [requested_category]

    selected_skus = {str(s).strip() for s in requested_skus or set() if str(s).strip()}
    by_sku: dict[str, dict[str, Any]] = {}
    excluded = Counter()
    excluded_rows: list[dict[str, str]] = []
    allowed = set(category_order)
    for original in rows:
        row = dict(original)
        sku = str(row.get("sku") or "").strip()
        if not sku or sku in by_sku:
            excluded["missing_or_duplicate_sku"] += 1
            excluded_rows.append({"sku": sku, "cat1_es": str(row.get("cat1_es") or ""),
                                  "product_url": str(row.get("product_url") or ""),
                                  "reason": "missing_or_duplicate_sku"})
            continue
        if not _URL_SKU.search(str(row.get("product_url") or "")):
            excluded["invalid_product_url"] += 1
            excluded_rows.append({"sku": sku, "cat1_es": str(row.get("cat1_es") or ""),
                                  "product_url": str(row.get("product_url") or ""),
                                  "reason": "invalid_product_url"})
            continue
        category = str(row.get("cat1_es") or "").strip()
        if category not in allowed:
            excluded["unknown_or_blank_category"] += 1
            excluded_rows.append({"sku": sku, "cat1_es": category,
                                  "product_url": str(row.get("product_url") or ""),
                                  "reason": "unknown_or_blank_category"})
            continue
        if selected_skus and sku not in selected_skus:
            continue
        by_sku[sku] = row

    pools: dict[str, list[dict[str, Any]]] = {category: [] for category in category_order}
    for row in by_sku.values():
        pools[str(row["cat1_es"])].append(row)
    for pool in pools.values():
        pool.sort(key=lambda r: str(r.get("sku") or ""))
        rng.shuffle(pool)

    available_categories = [category for category in category_order if pools[category]]
    if not available_categories:
        return [], {"seed": seed, "limit": limit, "categories": {}, "excluded": dict(excluded),
                    "excluded_rows": excluded_rows, "rare_strata_available": {}}
    actual_limit = min(limit, sum(len(pool) for pool in pools.values()))
    if requested_skus and len(selected_skus) > actual_limit:
        raise ValueError("SKU-filtered source has fewer valid rows than requested SKUs")

    quota, remainder = divmod(actual_limit, len(available_categories))
    remainder_order = list(available_categories)
    rng.shuffle(remainder_order)
    quotas = {category: quota for category in available_categories}
    for category in remainder_order[:remainder]:
        quotas[category] += 1
    sample_by_category: dict[str, list[dict[str, Any]]] = {
        category: pools[category][:min(quotas[category], len(pools[category]))]
        for category in available_categories
    }

    rare_predicates = {
        "missing_description": lambda r: not str(r.get("desc_es") or "").strip(),
        "missing_specification": lambda r: not str(r.get("spec_es") or "").strip(),
        "missing_product_details": lambda r: not str(r.get("details_es") or "").strip(),
        "new_or_reappeared": lambda r: str(r.get("presence_event") or "").upper() in {"NEW", "REAPPEARED"},
        "promotional_or_new_badge": lambda r: bool(
            _PROMO.search(str(r.get("raw_tags") or ""))
            or str(r.get("is_new_badge") or "").lower() in {"1", "true", "yes"}
        ),
    }
    all_candidates = [row for pool in pools.values() for row in pool]
    available_rare = {
        label: sum(1 for row in all_candidates if predicate(row))
        for label, predicate in rare_predicates.items()
    }
    represented: list[str] = []
    for label, predicate in rare_predicates.items():
        selected_rows = [row for pool in sample_by_category.values() for row in pool]
        if any(predicate(row) for row in selected_rows):
            represented.append(label)
            continue
        candidates = [row for row in all_candidates if predicate(row) and not _is_selected(row, sample_by_category)]
        candidates.sort(key=lambda r: (str(r.get("cat1_es") or ""), str(r.get("sku") or "")))
        if not candidates:
            continue
        rng.shuffle(candidates)
        for candidate in candidates:
            category = str(candidate["cat1_es"])
            current = sample_by_category.get(category) or []
            if not current:
                continue
            victim_options = sorted(
                current,
                key=lambda row: (
                    sum(bool(test(row)) for test in rare_predicates.values()),
                    str(row.get("sku") or ""),
                ),
            )
            victim = next((row for row in victim_options if row is not candidate), None)
            if victim is None:
                continue
            current.remove(victim)
            current.append(candidate)
            represented.append(label)
            break

    result: list[dict[str, Any]] = []
    for category in available_categories:
        for row in sample_by_category[category]:
            item = dict(row)
            item["sample_strata"] = ";".join(label for label, pred in rare_predicates.items() if pred(row))
            result.append(item)
    diagnostics = {
        "seed": seed,
        "limit": limit,
        "selected_count": len(result),
        "category_counts": dict(Counter(str(row.get("cat1_es") or "") for row in result)),
        "available_category_counts": {category: len(pools[category]) for category in category_order},
        "rare_strata_available": available_rare,
        "rare_strata_represented": list(dict.fromkeys(represented)),
        "excluded": dict(excluded),
        "excluded_rows": excluded_rows,
    }
    return result, diagnostics


def _is_selected(candidate: dict[str, Any], samples: dict[str, list[dict[str, Any]]]) -> bool:
    sku = str(candidate.get("sku") or "")
    return any(str(row.get("sku") or "") == sku for pool in samples.values() for row in pool)
