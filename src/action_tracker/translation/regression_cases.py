"""Versioned, source-bound localization regression cases.

The regression corpus is deliberately data driven.  It is not a list of SKU
patches: a case describes a Spanish source fragment, the rejected Chinese
rendering, the owner-confirmed correction, and the context in which it is
valid.  Runtime tools can use :func:`find_same_source_occurrences` to report
every matching record instead of fixing only the SKU that first exposed a
bug.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

LOCALIZATION_FIELDS = ("name", "cat1", "cat2", "spec", "description", "details")
REQUIRED_KEYS = {
    "case_id", "field", "source_es", "wrong_zh", "expected_zh", "issue_type",
    "risk_level", "evidence_status", "policy_version",
}


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    field: str
    source_es: str
    wrong_zh: str
    expected_zh: str
    issue_type: str
    risk_level: str
    evidence_status: str
    policy_version: str
    source_key: str = ""
    source_value: str = ""
    context: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.field not in LOCALIZATION_FIELDS:
            raise ValueError(f"UNKNOWN_LOCALIZATION_FIELD:{self.field}")
        if not self.source_es.strip() or not self.expected_zh.strip():
            raise ValueError(f"EMPTY_REGRESSION_TEXT:{self.case_id}")
        if self.context is None:
            object.__setattr__(self, "context", {})


def load_regression_cases(path: Path) -> tuple[RegressionCase, ...]:
    """Load and validate a JSONL corpus; malformed evidence fails closed."""
    cases: list[RegressionCase] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"REGRESSION_JSON_INVALID:{line_no}") from exc
        if not isinstance(row, dict) or not REQUIRED_KEYS.issubset(row):
            raise ValueError(f"REGRESSION_SCHEMA_INVALID:{line_no}")
        case = RegressionCase(**{key: row[key] for key in RegressionCase.__dataclass_fields__ if key in row})
        if case.case_id in seen:
            raise ValueError(f"REGRESSION_DUPLICATE_CASE:{case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def find_same_source_occurrences(
    records: Iterable[Mapping[str, Any]], case: RegressionCase,
) -> list[dict[str, Any]]:
    """Return all records containing a case's source fragment in its own field."""
    source_key = {
        "name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es",
        "description": "desc_es", "details": "details_es",
    }[case.field]
    source = case.source_es.casefold().strip()
    rows: list[dict[str, Any]] = []
    for record in records:
        value = str(record.get(source_key) or "")
        if source and source in value.casefold():
            rows.append({
                "case_id": case.case_id,
                "sku": str(record.get("sku") or ""),
                "field": case.field,
                "source_value": value,
                "target_value": str(record.get({
                    "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh",
                    "description": "desc_zh", "details": "details_zh",
                }[case.field]) or ""),
            })
    return rows


def summarize_occurrences(records: Iterable[Mapping[str, Any]], cases: Iterable[RegressionCase]) -> list[dict[str, Any]]:
    """Produce deterministic same-source counts and target-translation splits."""
    materialized = list(records)
    output: list[dict[str, Any]] = []
    for case in cases:
        occurrences = find_same_source_occurrences(materialized, case)
        variants = Counter(row["target_value"] for row in occurrences)
        output.append({
            "case_id": case.case_id,
            "field": case.field,
            "source_es": case.source_es,
            "occurrence_count": len(occurrences),
            "sku_count": len({row["sku"] for row in occurrences}),
            "target_variant_count": len(variants),
            "target_variants": dict(sorted(variants.items())),
        })
    return output


def validate_case_against_candidate(case: RegressionCase, candidate: str) -> tuple[bool, str]:
    """Check a deterministic candidate against the owner-confirmed expected text."""
    normalized = " ".join(str(candidate or "").split()).casefold()
    expected = " ".join(case.expected_zh.split()).casefold()
    return (normalized == expected, "PASS" if normalized == expected else "EXPECTED_VALUE_MISMATCH")


__all__ = [
    "LOCALIZATION_FIELDS", "RegressionCase", "find_same_source_occurrences",
    "load_regression_cases", "summarize_occurrences", "validate_case_against_candidate",
]
