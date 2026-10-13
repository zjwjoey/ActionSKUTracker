"""Explicit optional-price absence; unknown observations never clear facts."""
from __future__ import annotations
import math
from typing import Any
from .normalization import parse_price, parse_discount_percent

PRICE_FIELDS = frozenset({"current_price", "original_price", "discount"})

def complete_price_observation(raw: dict[str, Any]) -> bool:
    if raw.get("price_observation_complete") is not True:
        return False
    if "original" not in raw or "discount" not in raw:
        return False
    whole, fraction = str(raw.get("whole") or ""), str(raw.get("fractional") or "")
    current = parse_price(f"{whole},{fraction}" if fraction else whole)
    if current is None or not math.isfinite(current) or current <= 0:
        return False
    original_text = str(raw.get("original") or "").strip()
    discount_text = str(raw.get("discount") or "").strip()
    if original_text and parse_price(original_text) is None:
        return False
    if discount_text and parse_discount_percent(discount_text) is None:
        return False
    return True

def merge_observed_prices(record: dict[str, Any], observed: dict[str, Any]) -> bool:
    if observed.get("_price_observation_complete") is not True:
        return False
    if "original_price" not in observed or "discount" not in observed:
        raise ValueError("COMPLETE_PRICE_OBSERVATION_FIELDS_MISSING")
    current = parse_price(observed.get("current_price"))
    if current is None or not math.isfinite(current) or current <= 0:
        raise ValueError("COMPLETE_PRICE_OBSERVATION_CURRENT_INVALID")
    original = parse_price(observed.get("original_price"))
    raw_original = observed.get("original_price")
    if raw_original not in (None, "") and (original is None or not math.isfinite(original) or original <= 0):
        raise ValueError("COMPLETE_PRICE_OBSERVATION_ORIGINAL_INVALID")
    record.update(current_price=current, original_price=original, discount=observed.get("discount"))
    record["_price_observation_complete"] = True
    return True
