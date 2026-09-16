from __future__ import annotations

import re
from collections import Counter
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
    sku: str = ""
    source_hash: str = ""
    source: str = ""
    target: str = ""
    message: str = ""
    repairable: bool = False
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _numbers(value: str) -> Counter[str]:
    return Counter(item.replace(",", ".") for item in re.findall(r"\d+(?:[.,]\d+)?", value or ""))


def audit_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...]) -> tuple[QAFinding, ...]:
    findings: list[QAFinding] = []
    for field_name in requested_fields:
        target = fields.get(field_name)
        source_name = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, "")
        source_text = str(getattr(source, source_name, "") or "")
        if not isinstance(target, str) or not target.strip():
            findings.append(QAFinding("EMPTY_REQUIRED_FIELD", "BLOCKER", field_name, {}, source=source_text, message="required target field is empty", blocking=True))
            continue
        if "null" in target.casefold() or "undefined" in target.casefold():
            findings.append(QAFinding("NULL_UNDEFINED_RESIDUAL", "BLOCKER", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        if has_ordinary_spanish(target, allowed_tokens=set()):
            findings.append(QAFinding("SPANISH_RESIDUAL", "ERROR", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        source_numbers, target_numbers = _numbers(source_text), _numbers(target)
        dropped = source_numbers - target_numbers
        duplicated = target_numbers - source_numbers
        if dropped:
            findings.append(QAFinding("NUMERIC_DROPPED", "BLOCKER", field_name, {"source": dict(source_numbers), "target": dict(target_numbers), "missing": dict(dropped)}, source=source_text, target=target, message="numeric fact dropped", blocking=True))
        if duplicated:
            findings.append(QAFinding("NUMERIC_ADDED", "BLOCKER", field_name, {"source": dict(source_numbers), "target": dict(target_numbers), "extra": dict(duplicated)}, source=source_text, target=target, message="numeric fact added", blocking=True))
        protected = protect_text(source_text)
        for value, kind in zip(protected.tokens, protected.token_types):
            if kind in {"URL", "SKU", "EAN", "MODEL", "TECH", "CERTIFICATION", "BATTERY_CAPACITY", "POWER", "VOLTAGE"}:
                expected_count, actual_count = source_text.count(value), target.count(value)
                if actual_count < expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_MISSING", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
                elif actual_count > expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_DUPLICATED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
        if field_name == "cat1" and target not in FIXED_CAT1:
            findings.append(QAFinding("CATEGORY_INVALID", "BLOCKER", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        if "<" in target and ">" in target:
            findings.append(QAFinding("HTML_RESIDUAL", "ERROR", field_name, {}, source=source_text, target=target, repairable=True, blocking=True))
    return tuple(findings)


def guard_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...]) -> dict[str, Any]:
    findings = audit_translation(source, fields, requested_fields)
    return {"status": "PASS" if not findings else "FAIL", "findings": [finding.as_dict() for finding in findings]}
