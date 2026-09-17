"""Business-language QA layered on top of factual translation QA."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .product_family import ProductFamilyRegistry, TranslationContext, UNKNOWN_FAMILY, family_policy_for
from .normalization.structured_details import parse_structured_details


@dataclass(frozen=True)
class CanonicalFinding:
    rule_id: str
    severity: str
    field_name: str
    message: str
    evidence: Mapping[str, Any]
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "severity": self.severity, "field_name": self.field_name, "message": self.message, "evidence": dict(self.evidence), "blocking": self.blocking, "qa_layer": "CANONICAL"}


def audit_canonical(context: TranslationContext, fields: Mapping[str, Any], *, registry: ProductFamilyRegistry | None = None, production: bool = False) -> tuple[CanonicalFinding, ...]:
    if context.family_id == UNKNOWN_FAMILY:
        return ()
    policy = family_policy_for(context, registry)
    if policy is None:
        return (CanonicalFinding("FAMILY_POLICY_CONFLICT", "BLOCKER", context.field_name, "family policy is unavailable", {"family_id": context.family_id}),)
    findings: list[CanonicalFinding] = []
    field = context.field_name
    target = str(fields.get(field) or "")
    if field == "details":
        for item in parse_structured_details(target):
            if item.context_key == "material" and "橡皮筋" in item.value:
                findings.append(CanonicalFinding("DETAIL_CONTEXT_TERMINOLOGY_VIOLATION", "BLOCKER", field, "material Goma must be 橡胶, not 橡皮筋", {"context_key": "material", "expected": "橡胶", "found": item.value}))
    if field == "description" and any(str(f.source_text).casefold() == "gomas" for f in context.semantic_facts) and "橡皮筋" not in target:
        findings.append(CanonicalFinding("FIELD_TERMINOLOGY_NONCANONICAL", "ERROR", field, "gomas in description must use 橡皮筋", {"expected": "橡皮筋"}, blocking=production))
    field_policy = policy.field_policies.get(field)
    if field_policy:
        for forbidden in field_policy.forbidden_terms:
            if forbidden in target:
                findings.append(CanonicalFinding("FAMILY_PRODUCT_TYPE_NONCANONICAL", "ERROR", field, f"non-canonical family term: {forbidden}", {"family_id": context.family_id, "expected": policy.canonical_product_type, "found": forbidden}, blocking=production))
        for required in field_policy.required_terms:
            if required not in target:
                findings.append(CanonicalFinding("FAMILY_PRODUCT_TYPE_NONCANONICAL", "ERROR", field, f"missing canonical family term: {required}", {"family_id": context.family_id, "expected": required}, blocking=production))
    if context.family_id == "CLEANING_CLOTH":
        if field == "name":
            for noncanonical in ("抹布", "擦布", "湿布"):
                if noncanonical in target:
                    findings.append(CanonicalFinding("FAMILY_PRODUCT_TYPE_NONCANONICAL", "ERROR", field, "cleaning-cloth name is not canonical", {"found": noncanonical, "expected": "清洁布"}, blocking=production))
            if "清洁布" not in target:
                findings.append(CanonicalFinding("FAMILY_PRODUCT_TYPE_NONCANONICAL", "ERROR", field, "cleaning-cloth name must use 清洁布", {"expected": "清洁布"}, blocking=production))
            # The family naming order is feature/material -> usage -> product
            # type.  Reject a mechanically reversed form such as
            # ``清洁布微纤维`` while leaving free description prose alone.
            for marker in ("微纤维", "地板"):
                if marker in target and "清洁布" in target and target.index("清洁布") < target.index(marker):
                    findings.append(CanonicalFinding("FAMILY_NAME_ORDER_VIOLATION", "ERROR", field, "cleaning-cloth name order must place modifiers before product type", {"expected_order": ["微纤维/用途", "清洁布"], "found": target}, blocking=production))
            if "microfibra" in " ".join(str(f.source_text) for f in context.semantic_facts).casefold() and "超细纤维" in target:
                findings.append(CanonicalFinding("FAMILY_MATERIAL_NONCANONICAL", "ERROR", field, "name microfibra should use 微纤维", {"expected": "微纤维", "found": "超细纤维"}, blocking=production))
        if field == "description" and "microfibra" in " ".join(str(f.source_text) for f in context.semantic_facts).casefold() and "微纤维" in target and "超细纤维" not in target:
            findings.append(CanonicalFinding("FAMILY_MATERIAL_NONCANONICAL", "ERROR", field, "description microfibra should use 超细纤维", {"expected": "超细纤维"}, blocking=production))
        if field == "details":
            for item in parse_structured_details(str(fields.get(field) or "")):
                if item.context_key == "material" and "微纤维" in item.value:
                    findings.append(CanonicalFinding("FAMILY_MATERIAL_NONCANONICAL", "ERROR", field, "details material microfibra should use 超细纤维", {"expected": "超细纤维"}, blocking=production))
    return tuple(findings)


def canonical_guard(context: TranslationContext, fields: Mapping[str, Any], *, registry: ProductFamilyRegistry | None = None, production: bool = False) -> dict[str, Any]:
    findings = audit_canonical(context, fields, registry=registry, production=production)
    return {"status": "PASS" if not any(item.blocking for item in findings) else "FAIL", "findings": [item.as_dict() for item in findings], "qa_layer": "CANONICAL", "family_id": context.family_id, "family_policy_version": context.family_policy_version}
