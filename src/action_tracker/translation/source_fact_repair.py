"""Narrow, auditable *suggestions* for immutable source facts in model output.

These rules are not a translator and never repair a candidate authoritatively.
They only identify a suspected source-fidelity issue and return evidence plus
a suggested value for the model-led semantic reviewer.  The returned value is
always a copy of the original prediction; callers may not treat this module as
an Apply step.
"""
from __future__ import annotations

import re
from typing import Mapping, Any


_MODEL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{1,5}[/-]?[A-Z]*\d+[A-Z0-9./-]*(?![A-Za-z0-9])"
)
_TWO_IN_ONE = re.compile(r"(?P<a>[2-9])\s*en\s*(?P<b>[1-9])", re.IGNORECASE)
_SINGULAR_SOCK = re.compile(r"\bun(?:a)?\s+calcet[ií]n\b", re.IGNORECASE)


def _model_tokens(value: object) -> list[str]:
    return [re.sub(r"[\s-]+", "-", match.group().upper()) for match in _MODEL_TOKEN.finditer(str(value or ""))]


def repair_model_output(
    source: Mapping[str, object], prediction: Mapping[str, object] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Return ``(unchanged_candidate, review_suggestions)``.

    The legacy function name is retained for callers, but the contract is now
    candidate-only. ``review_suggestions`` uses
    ``SEMANTIC_REVIEW_REQUIRED`` and carries a proposed value as evidence; it
    is never a reviewed value.
    """

    if not isinstance(prediction, Mapping):
        return None, []
    result = {str(key): value for key, value in prediction.items()}
    suggestions: list[dict[str, str]] = []

    source_name = str(source.get("name", "") or "")
    name = result.get("name")
    if isinstance(name, str):
        output_name = name
        output_tokens = set(_model_tokens(output_name))
        for token in _model_tokens(source_name):
            if token not in output_tokens:
                suggested = f"{token}{output_name}" if output_name else token
                suggestions.append({
                    "field": "name", "issue_type": "MODEL_TOKEN_MISSING",
                    "evidence": token, "suggested_value": suggested,
                    "status": "SEMANTIC_REVIEW_REQUIRED",
                })

    source_description = str(source.get("description", "") or "")
    description = result.get("description")
    if isinstance(description, str):
        output_description = description
        two_in_one = _TWO_IN_ONE.search(source_description)
        if two_in_one:
            canonical = f"{two_in_one.group('a')}合{two_in_one.group('b')}"
            if canonical not in output_description:
                if "双面" in output_description:
                    suggested = output_description.replace("双面", canonical)
                else:
                    suggested = f"{output_description}；{canonical}功能"
                suggestions.append({
                    "field": "description", "issue_type": "FUNCTIONAL_TOKEN_MISSING",
                    "evidence": canonical, "suggested_value": suggested,
                    "status": "SEMANTIC_REVIEW_REQUIRED",
                })
        if _SINGULAR_SOCK.search(source_description) and "一双" in output_description:
            suggestions.append({
                "field": "description", "issue_type": "QUANTITY_OR_NUMBER_REVIEW",
                "evidence": "un calcetín",
                "suggested_value": output_description.replace("一双", "一只", 1),
                "status": "SEMANTIC_REVIEW_REQUIRED",
            })

    return result, suggestions


__all__ = ["repair_model_output"]
