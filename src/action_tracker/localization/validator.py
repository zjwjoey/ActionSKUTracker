from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import LocalizationPlan, SourceFacts
from .policy import FIXED_CAT1, has_ordinary_spanish
from .coverage import validate_fact_coverage

# Start at the beginning of a standalone number.  Including digits in the
# look-behind prevents the regex from recovering a trailing digit of an
# alphanumeric model such as ``A3`` or the right side of ``50x60``.
# Keep dimensions such as ``50x60`` as two numeric facts while excluding
# digits embedded in model tokens such as ``A3``/``E27``.
_NUMBER = re.compile(r"(?<![A-WYZ0-9a-wyz])\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class LocalizationValidation:
    ok: bool
    reasons: tuple[str, ...]
    spanish_residue_tokens: tuple[str, ...] = ()
    numeric_mismatches: tuple[str, ...] = ()


def validate_plan(source: SourceFacts, plan: LocalizationPlan, *, allowed_tokens: set[str] | None = None) -> LocalizationValidation:
    reasons: list[str] = list(plan.review_reasons)
    residue: list[str] = []
    for key, field in plan.fields.items():
        if has_ordinary_spanish(field.value, allowed_tokens=allowed_tokens):
            residue.append(key)
            reasons.append("SPANISH_RESIDUAL")
    if plan.fields["cat1_zh"].value not in FIXED_CAT1:
        reasons.append("CATEGORY_REVIEW")
    if plan.source_hash != source.source_hash:
        reasons.append("SOURCE_HASH_MISMATCH")
    numeric_bad: list[str] = []
    # Numbers may legitimately move between name/spec/details according to
    # placement policy, so compare the complete source/output projections.
    # This still rejects both dropped and invented numeric facts.
    source_values = " ".join(str(getattr(source, key) or "") for key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"))
    output_values = " ".join(plan.fields[key].value for key in ("name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh"))
    def normalized_numbers(text: str) -> set[str]:
        return {token.replace(",", ".") for token in _NUMBER.findall(text)}
    expected_numbers = normalized_numbers(source_values)
    found_numbers = normalized_numbers(output_values)
    # The product number may be added to structured details even when the
    # Spanish details omitted it; it is identity evidence, not a changed fact.
    sku_number = str(source.sku).replace(",", ".")
    expected_numbers.discard(sku_number)
    found_numbers.discard(sku_number)
    if expected_numbers - found_numbers:
        for source_key in ("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"):
            if normalized_numbers(str(getattr(source, source_key) or "")) - found_numbers:
                numeric_bad.append(source_key)
    if found_numbers - expected_numbers:
        numeric_bad.append("output")
    if numeric_bad: reasons.append("NUMERIC_FACT_MISMATCH")
    unit_expected = normalized_numbers(str(source.unit_price_es or ""))
    unit_found = normalized_numbers(plan.fields["unit_price_zh"].value)
    if unit_expected - unit_found:
        numeric_bad.append("unit_price_es")
        reasons.append("PRICE_FACT_MISMATCH")
    spec = plan.fields["spec_zh"].value
    if "|" in spec or re.search(r"\d\s*[xX]\s*\d", spec) or re.search(r"\d+\s+(?:mm|cm|ml|kg|mg|mcg|mAh|V|W|Hz|g|L|lm|°C)\b", spec, re.I):
        reasons.append("SPEC_FORMAT_REVIEW")
    rendered = " ".join(field.value for field in plan.fields.values())
    for fact in plan.semantic_facts:
        if fact.semantic_type in {"TECH_TOKEN", "MODEL", "SOCKET", "INTERFACE"} and fact.value and fact.value not in rendered:
            reasons.append("TECH_TOKEN_REVIEW")
    details = plan.fields["details_zh"].value
    sku_matches = re.findall(r"商品编号\s*[：:]\s*([A-Za-z0-9-]+)", details)
    if sku_matches and any(str(value) != str(source.sku) for value in sku_matches):
        reasons.append("DETAIL_SKU_MISMATCH")
    if any(field.freshness_status == "STALE" for field in plan.fields.values()):
        reasons.append("STALE_LOCALIZATION")
    coverage = validate_fact_coverage(plan)
    if not coverage.ok:
        reasons.append("FACT_NOT_COVERED")
    return LocalizationValidation(not reasons, tuple(dict.fromkeys(reasons)), tuple(residue), tuple(numeric_bad))
