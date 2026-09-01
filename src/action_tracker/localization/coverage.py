"""Fact coverage contract for localization plans.

The validator answers a different question from Spanish-residual checks:
every meaningful source fact recognised by the parser must be placed, ignored
with an explicit reason, or sent to review.  It never mutates source data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import LocalizationPlan, SemanticFact


@dataclass(frozen=True)
class FactCoverageResult:
    ok: bool
    missing: tuple[str, ...] = ()
    statuses: tuple[dict[str, str], ...] = ()


def validate_fact_coverage(plan: LocalizationPlan) -> FactCoverageResult:
    rendered = " ".join(field.value for field in plan.fields.values()).casefold()
    statuses: list[dict[str, str]] = []
    missing: list[str] = []
    for fact in plan.semantic_facts:
        source = fact.source_text.strip()
        target = fact.canonical_value or fact.value
        if fact.placement in {"name", "spec", "description", "details", "cat1", "cat2"} and target and target.casefold() in rendered:
            status, reason = "PLACED", "target appears in planned output"
        elif fact.placement in {"review", ""}:
            status, reason = "REVIEW_REQUIRED", "no deterministic placement policy"
            missing.append(source)
        else:
            status, reason = "NOT_OUTPUT_REQUIRED", "policy marked fact as non-output"
        statuses.append({"semantic_type": fact.semantic_type, "source_text": source, "coverage_status": status, "reason": reason})
    return FactCoverageResult(not missing, tuple(missing), tuple(statuses))

