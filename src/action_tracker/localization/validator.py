from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import LocalizationPlan, SourceFacts
from .policy import FIXED_CAT1, has_ordinary_spanish

# Start at the beginning of a standalone number.  Including digits in the
# look-behind prevents the regex from recovering a trailing digit of an
# alphanumeric model such as ``A3`` or the right side of ``50x60``.
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")


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
    numeric_bad: list[str] = []
    pairs = (("spec_es", "spec_zh"), ("name_es", "name_zh"), ("desc_es", "desc_zh"), ("details_es", "details_zh"))
    for src_key, dst_key in pairs:
        expected = set(_NUMBER.findall(str(getattr(source, src_key) or "")))
        found = set(_NUMBER.findall(plan.fields[dst_key].value))
        if expected - found:
            numeric_bad.append(src_key)
    if numeric_bad: reasons.append("NUMERIC_FACT_MISMATCH")
    return LocalizationValidation(not reasons, tuple(dict.fromkeys(reasons)), tuple(residue), tuple(numeric_bad))
