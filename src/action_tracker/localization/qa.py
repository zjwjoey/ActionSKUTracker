from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import SourceFacts
from .policy import FIXED_CAT1, has_ordinary_spanish
from .protection.tokens import ProtectedTokenError, protect_text, restore_text


@dataclass(frozen=True)
class QAFinding:
    rule_id: str
    severity: str
    field_name: str
    evidence: Mapping[str, Any]


def _numbers(value: str) -> set[str]:
    return {item.replace(",", ".") for item in re.findall(r"\d+(?:[.,]\d+)?", value or "")}


def audit_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...]) -> tuple[QAFinding, ...]:
    findings: list[QAFinding] = []
    for field_name in requested_fields:
        target = fields.get(field_name)
        source_name = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, "")
        source_text = str(getattr(source, source_name, "") or "")
        if not isinstance(target, str) or not target.strip():
            findings.append(QAFinding("EMPTY_REQUIRED_FIELD", "HIGH", field_name, {}))
            continue
        if "null" in target.casefold() or "undefined" in target.casefold():
            findings.append(QAFinding("NULL_UNDEFINED_RESIDUAL", "HIGH", field_name, {"value": target}))
        if has_ordinary_spanish(target, allowed_tokens=set()):
            findings.append(QAFinding("SPANISH_RESIDUAL", "HIGH", field_name, {"value": target}))
        if _numbers(source_text) - _numbers(target):
            findings.append(QAFinding("NUMERIC_DROPPED", "HIGH", field_name, {"source": sorted(_numbers(source_text)), "target": sorted(_numbers(target))}))
        if field_name == "cat1" and target not in FIXED_CAT1:
            findings.append(QAFinding("INVALID_CATEGORY", "HIGH", field_name, {"value": target}))
        if "<" in target and ">" in target:
            findings.append(QAFinding("HTML_RESIDUAL", "HIGH", field_name, {}))
    return tuple(findings)


def guard_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...]) -> dict[str, Any]:
    findings = audit_translation(source, fields, requested_fields)
    return {"status": "PASS" if not findings else "FAIL", "findings": [finding.__dict__ for finding in findings]}
