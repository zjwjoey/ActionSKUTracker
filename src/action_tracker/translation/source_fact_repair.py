"""Candidate-only source-fidelity suggestions for model output.

Deterministic checks may flag evidence, but they never authoritatively repair a
translation or write a reviewed value. Semantic review remains the authority.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


_MODEL_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,5}[/-]?[A-Z]*\d+[A-Z0-9./-]*(?![A-Za-z0-9])")
_TWO_IN_ONE = re.compile(r"(?P<a>[2-9])\s*en\s*(?P<b>[1-9])", re.IGNORECASE)
_SINGULAR_SOCK = re.compile(r"\bun(?:a)?\s+calcet[ií]n\b", re.IGNORECASE)


def _model_tokens(value: object) -> list[str]:
    return [re.sub(r"[\s-]+", "-", match.group().upper()) for match in _MODEL_TOKEN.finditer(str(value or ""))]


def repair_model_output(
    source: Mapping[str, object], prediction: Mapping[str, object] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Return the unchanged candidate and semantic-review suggestions."""
    if not isinstance(prediction, Mapping):
        return None, []
    result = {str(key): value for key, value in prediction.items()}
    suggestions: list[dict[str, str]] = []

    source_name = str(source.get("name", "") or "")
    output_name = result.get("name")
    if isinstance(output_name, str):
        output_tokens = set(_model_tokens(output_name))
        for token in _model_tokens(source_name):
            if token not in output_tokens:
                suggestions.append({
                    "field": "name", "issue_type": "MODEL_TOKEN_MISSING",
                    "evidence": token, "suggested_value": f"{token}{output_name}",
                    "status": "SEMANTIC_REVIEW_REQUIRED",
                })

    source_description = str(source.get("description", "") or "")
    output_description = result.get("description")
    if isinstance(output_description, str):
        match = _TWO_IN_ONE.search(source_description)
        if match:
            canonical = f"{match.group('a')}合{match.group('b')}"
            if canonical not in output_description:
                suggestions.append({
                    "field": "description", "issue_type": "FUNCTIONAL_TOKEN_MISSING",
                    "evidence": canonical, "suggested_value": f"{output_description}；{canonical}功能",
                    "status": "SEMANTIC_REVIEW_REQUIRED",
                })
        if _SINGULAR_SOCK.search(source_description) and "一双" in output_description:
            suggestions.append({
                "field": "description", "issue_type": "QUANTITY_OR_NUMBER_REVIEW",
                "evidence": "un calcetín", "suggested_value": output_description.replace("一双", "一只", 1),
                "status": "SEMANTIC_REVIEW_REQUIRED",
            })

    return result, suggestions


__all__ = ["repair_model_output"]
