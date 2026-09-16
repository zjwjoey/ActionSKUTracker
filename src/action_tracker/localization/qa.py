from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import SourceFacts
from .policy import FIXED_CAT1, has_ordinary_spanish
from .protection.tokens import ProtectedTokenError, protect_text, restore_text


_STRICT_UNIT_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s*(mAh|Ah|Wh|kWh|mW|kW|Hz|V|W|dB|°C)(?![A-Za-z0-9])", re.I)
_STRICT_TOKEN_TYPES = {"URL", "SKU", "EAN", "MODEL", "TECH", "CERTIFICATION", "CAPACITY", "BATTERY_CAPACITY"}


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


def audit_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...], *, terminology: tuple[Mapping[str, Any], ...] = ()) -> tuple[QAFinding, ...]:
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
        target_protected = protect_text(target)
        source_strict = Counter((kind, value.casefold()) for kind, value in zip(protected.token_types, protected.tokens) if kind in _STRICT_TOKEN_TYPES)
        target_strict = Counter((kind, value.casefold()) for kind, value in zip(target_protected.token_types, target_protected.tokens) if kind in _STRICT_TOKEN_TYPES)
        for (kind, value), expected_count in source_strict.items():
            actual_count = target_strict.get((kind, value), 0)
            if actual_count < expected_count:
                same_kind = sum(count for (other_kind, _), count in target_strict.items() if other_kind == kind)
                if kind == "MODEL" and same_kind == 0:
                    findings.append(QAFinding("MODEL_DROPPED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
                elif same_kind >= expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_CHANGED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
        for (kind, value), actual_count in target_strict.items():
            if actual_count > source_strict.get((kind, value), 0):
                rule = "MODEL_CHANGED" if kind == "MODEL" and not source_strict.get((kind, value), 0) else "PROTECTED_TOKEN_ADDED"
                findings.append(QAFinding(rule, "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": source_strict.get((kind, value), 0), "actual": actual_count}, source=source_text, target=target, blocking=True))
        for value, kind in zip(protected.tokens, protected.token_types):
            if kind in {"URL", "SKU", "EAN", "MODEL", "TECH", "CERTIFICATION", "CAPACITY", "BATTERY_CAPACITY", "POWER", "VOLTAGE"}:
                expected_count, actual_count = source_text.count(value), target.count(value)
                if actual_count < expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_MISSING", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
                elif actual_count > expected_count:
                    findings.append(QAFinding("PROTECTED_TOKEN_DUPLICATED", "BLOCKER", field_name, {"token_type": kind, "value": value, "expected": expected_count, "actual": actual_count}, source=source_text, target=target, blocking=True))
        source_units = _STRICT_UNIT_RE.findall(source_text)
        target_units = {unit.casefold() for unit in _STRICT_UNIT_RE.findall(target)}
        for unit in source_units:
            if unit.casefold() not in target_units:
                findings.append(QAFinding("UNIT_DROPPED", "BLOCKER", field_name, {"unit": unit}, source=source_text, target=target, message="technical unit dropped", blocking=True))
        for term in terminology:
            source_term = str(term.get("source") or term.get("source_term") or "")
            target_term = str(term.get("target") or term.get("target_term") or "")
            if source_term and target_term and source_term.casefold() in source_text.casefold() and target_term not in target:
                findings.append(QAFinding("TERMINOLOGY_VIOLATION", "ERROR", field_name, {"source": source_term, "target": target_term}, source=source_text, target=target, blocking=True))
            forbidden = str(term.get("forbidden_target") or "")
            if forbidden and forbidden in target:
                findings.append(QAFinding("FORBIDDEN_TERM", "ERROR", field_name, {"term": forbidden}, source=source_text, target=target, blocking=True))
        if field_name == "cat1" and target not in FIXED_CAT1:
            findings.append(QAFinding("CATEGORY_INVALID", "BLOCKER", field_name, {"value": target}, source=source_text, target=target, blocking=True))
        if "<" in target and ">" in target:
            findings.append(QAFinding("HTML_RESIDUAL", "ERROR", field_name, {}, source=source_text, target=target, repairable=True, blocking=True))
    return tuple(findings)


def guard_translation(source: SourceFacts, fields: Mapping[str, Any], requested_fields: tuple[str, ...], *, terminology: tuple[Mapping[str, Any], ...] = ()) -> dict[str, Any]:
    findings = audit_translation(source, fields, requested_fields, terminology=terminology)
    return {"status": "PASS" if not findings else "FAIL", "findings": [finding.as_dict() for finding in findings]}
