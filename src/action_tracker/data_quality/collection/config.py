from __future__ import annotations

"""Fail-closed loading and validation for collection-integrity thresholds."""

from collections.abc import Mapping
import math
from pathlib import Path
from typing import Any

import yaml


class CollectionConfigError(ValueError):
    """A collection-integrity configuration cannot safely drive a gate."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(f"{code}:{detail}" if detail else code)


_NUMERIC_KEYS = (
    "listing_warn_drop_pct", "listing_block_drop_pct",
    "current_warn_drop_pct", "current_block_drop_pct",
    "coverage_warn_drop_points", "coverage_block_drop_points",
    "price_coverage_warn_below", "price_coverage_block_below",
    "description_warn_drop_points", "description_block_drop_points",
    "detail_failure_warn_above", "detail_failure_block_above",
)


def _number(value: Any, key: str) -> int | float:
    # bool is an int subclass, but accepting it would turn malformed YAML
    # into a valid threshold.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_TYPE", key)
    if not math.isfinite(float(value)):
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_VALUE", key)
    return value


def validate_collection_thresholds(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the complete production ``collection_integrity`` contract."""

    if not isinstance(value, Mapping):
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_STRUCTURE")
    required = ("required_categories", *_NUMERIC_KEYS, "drift")
    missing = [key for key in required if key not in value]
    if missing:
        raise CollectionConfigError("COLLECTION_CONFIG_MISSING_KEY", ",".join(missing))

    categories = value["required_categories"]
    if isinstance(categories, bool) or not isinstance(categories, int) or categories <= 0:
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_TYPE", "required_categories")

    result = dict(value)
    result["required_categories"] = categories
    for key in _NUMERIC_KEYS:
        result[key] = _number(value[key], key)

    for warn_key, block_key in (
        ("listing_warn_drop_pct", "listing_block_drop_pct"),
        ("current_warn_drop_pct", "current_block_drop_pct"),
        ("coverage_warn_drop_points", "coverage_block_drop_points"),
        ("description_warn_drop_points", "description_block_drop_points"),
        ("detail_failure_warn_above", "detail_failure_block_above"),
    ):
        if float(result[warn_key]) > float(result[block_key]):
            raise CollectionConfigError("COLLECTION_CONFIG_INVALID_ORDER", f"{warn_key}>{block_key}")

    for key in ("price_coverage_warn_below", "price_coverage_block_below", "detail_failure_warn_above", "detail_failure_block_above"):
        if not 0 <= float(result[key]) <= 1:
            raise CollectionConfigError("COLLECTION_CONFIG_INVALID_RANGE", key)
    for key in (
        "listing_warn_drop_pct", "listing_block_drop_pct", "current_warn_drop_pct",
        "current_block_drop_pct", "coverage_warn_drop_points", "coverage_block_drop_points",
        "description_warn_drop_points", "description_block_drop_points",
    ):
        if float(result[key]) < 0:
            raise CollectionConfigError("COLLECTION_CONFIG_INVALID_RANGE", key)

    drift = value["drift"]
    if not isinstance(drift, Mapping):
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_STRUCTURE", "drift")
    drift_required = ("price_equal_current_ratio", "ui_contamination_rate", "html_contamination_rate")
    drift_missing = [key for key in drift_required if key not in drift]
    if drift_missing:
        raise CollectionConfigError("COLLECTION_CONFIG_MISSING_KEY", "drift." + ",drift.".join(drift_missing))
    normalized_drift = dict(drift)
    for key in drift_required:
        normalized_drift[key] = _number(drift[key], f"drift.{key}")
    if not 0 <= float(normalized_drift["price_equal_current_ratio"]) <= 1:
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_RANGE", "drift.price_equal_current_ratio")
    for key in ("ui_contamination_rate", "html_contamination_rate"):
        if not 0 <= float(normalized_drift[key]) <= 1:
            raise CollectionConfigError("COLLECTION_CONFIG_INVALID_RANGE", f"drift.{key}")
    result["drift"] = normalized_drift
    return result


def load_collection_thresholds(path: Path) -> dict[str, Any]:
    """Load production thresholds; missing or malformed files fail closed."""

    path = Path(path)
    if not path.exists():
        raise CollectionConfigError("COLLECTION_CONFIG_MISSING", str(path))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CollectionConfigError("COLLECTION_CONFIG_UNREADABLE", str(path)) from exc
    if not isinstance(raw, Mapping):
        raise CollectionConfigError("COLLECTION_CONFIG_INVALID_STRUCTURE", str(path))
    thresholds = raw.get("collection_integrity")
    if not isinstance(thresholds, Mapping):
        raise CollectionConfigError("COLLECTION_CONFIG_MISSING_SECTION", "collection_integrity")
    return validate_collection_thresholds(thresholds)
