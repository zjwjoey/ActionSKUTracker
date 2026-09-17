"""Deterministic product-family governance for Translation System V1.

Product type answers *what the product is*; product family answers *which
business language policy applies*.  This module is intentionally deterministic
and fail-closed so a provider never gets to choose a family implicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .contracts import SemanticFact, SourceFacts


FAMILY_POLICY_SCHEMA_VERSION = "PRODUCT_FAMILY_POLICY_V1"
UNKNOWN_FAMILY = "UNKNOWN"


@dataclass(frozen=True)
class FamilyFieldPolicy:
    field_name: str
    canonical_terms: Mapping[str, str] = field(default_factory=dict)
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    naming_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalTermRule:
    source_term: str
    target_term: str
    field_name: str | None = None
    context_key: str | None = None
    match_mode: str = "PHRASE"


@dataclass(frozen=True)
class ProductFamilyPolicy:
    family_id: str
    policy_version: str
    canonical_product_type: str
    aliases: tuple[str, ...]
    category_constraints: tuple[str, ...] = ()
    field_policies: Mapping[str, FamilyFieldPolicy] = field(default_factory=dict)
    canonical_terms: tuple[CanonicalTermRule, ...] = ()
    naming_rules: tuple[str, ...] = ()
    review_status: str = "SEED_REVIEWED"

    def terms_for(self, field_name: str, context_key: str | None = None) -> tuple[CanonicalTermRule, ...]:
        return tuple(rule for rule in self.canonical_terms
                     if (rule.field_name is None or rule.field_name == field_name)
                     and (rule.context_key is None or rule.context_key == context_key))


@dataclass(frozen=True)
class ProductFamilyMatch:
    family_id: str
    confidence: float
    evidence: tuple[str, ...] = ()
    policy_version: str = ""

    @property
    def is_known(self) -> bool:
        return self.family_id != UNKNOWN_FAMILY


@dataclass(frozen=True)
class TranslationContext:
    """The only context object allowed to cross the translation chain."""

    sku: str
    field_name: str
    source_hash: str
    family_id: str = UNKNOWN_FAMILY
    family_policy_version: str = ""
    product_type: str = ""
    cat1: str = ""
    cat2: str = ""
    detail_key: str = ""
    detail_value_type: str = ""
    context_key: str = ""
    semantic_facts: tuple[SemanticFact, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku, "field_name": self.field_name,
            "source_hash": self.source_hash, "family_id": self.family_id,
            "family_policy_version": self.family_policy_version,
            "product_type": self.product_type, "cat1": self.cat1,
            "cat2": self.cat2, "detail_key": self.detail_key,
            "detail_value_type": self.detail_value_type,
            "context_key": self.context_key,
            "semantic_facts": [fact.as_dict() for fact in self.semantic_facts],
        }


def _phrase_present(text: str, phrase: str) -> bool:
    """Match words/phrases, never arbitrary substrings."""
    phrase = " ".join(str(phrase or "").casefold().split())
    text = " ".join(str(text or "").casefold().split())
    if not phrase:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.I))


_CLEANING_CLOTH_ALIASES = (
    "paño", "paños", "bayeta", "bayetas",
    "paño de limpieza", "paños de limpieza",
    "paño de microfibra", "paños de microfibra",
    "bayeta de microfibra", "bayetas de microfibra",
)

_CLEANING_CLOTH_POLICY = ProductFamilyPolicy(
    family_id="CLEANING_CLOTH",
    policy_version="CLEANING_CLOTH_V1",
    canonical_product_type="清洁布",
    aliases=_CLEANING_CLOTH_ALIASES,
    category_constraints=("hogar", "limpieza", "cuidado del hogar", "家务清洁"),
    field_policies={
        "name": FamilyFieldPolicy(
            "name", canonical_terms={"paño": "清洁布", "paños": "清洁布", "bayeta": "清洁布", "bayetas": "清洁布", "microfibra": "微纤维"},
            required_terms=("清洁布",), forbidden_terms=("抹布", "擦布", "湿布"), naming_order=("material", "usage", "product_type")),
        "description": FamilyFieldPolicy("description", canonical_terms={"microfibra": "超细纤维"}),
        "details.material": FamilyFieldPolicy("details.material", canonical_terms={"microfibra": "超细纤维", "goma": "橡胶"}),
    },
    canonical_terms=(
        CanonicalTermRule("paño", "清洁布", "name"), CanonicalTermRule("paños", "清洁布", "name"),
        CanonicalTermRule("bayeta", "清洁布", "name"), CanonicalTermRule("bayetas", "清洁布", "name"),
        CanonicalTermRule("microfibra", "微纤维", "name"), CanonicalTermRule("microfibra", "超细纤维", "description"),
        CanonicalTermRule("microfibra", "超细纤维", "details", "material"),
        CanonicalTermRule("gomas", "橡皮筋", "description"), CanonicalTermRule("goma", "橡胶", "details", "material"),
    ),
    naming_rules=("CANONICAL_PRODUCT_TYPE", "NAME_MATERIAL_USAGE_ORDER", "NO_BRAND_DISPLAY"),
)


class ProductFamilyRegistry:
    """Versioned in-code seed registry; future policies can be loaded from a manifest."""

    def __init__(self, policies: Iterable[ProductFamilyPolicy] | None = None):
        policies = tuple(policies or (_CLEANING_CLOTH_POLICY,))
        self._policies = {policy.family_id: policy for policy in policies}

    def get(self, family_id: str) -> ProductFamilyPolicy | None:
        return self._policies.get(str(family_id or ""))

    def all(self) -> tuple[ProductFamilyPolicy, ...]:
        return tuple(self._policies.values())


def _family_text(source: SourceFacts) -> str:
    return " ".join((source.name_es, source.cat1_es, source.cat2_es, source.spec_es, source.desc_es, source.details_es))


def classify_product_family(source: SourceFacts | Mapping[str, Any], *, semantic_facts: Iterable[SemanticFact] = (), registry: ProductFamilyRegistry | None = None) -> ProductFamilyMatch:
    """Classify deterministically and fail closed on weak evidence."""
    source = source if isinstance(source, SourceFacts) else SourceFacts.from_record(source)
    registry = registry or ProductFamilyRegistry()
    facts = tuple(semantic_facts)
    product_type = {str(f.canonical_value or f.value).strip() for f in facts if f.semantic_type == "PRODUCT_TYPE"}
    text = _family_text(source)
    candidates: list[ProductFamilyMatch] = []
    for policy in registry.all():
        aliases = tuple(alias for alias in policy.aliases if _phrase_present(text, alias))
        if not aliases:
            continue
        evidence = [f"alias:{alias}" for alias in aliases]
        score = 0.72
        category_match = any(constraint.casefold() in f"{source.cat1_es} {source.cat2_es}".casefold() for constraint in policy.category_constraints)
        semantic_match = policy.canonical_product_type in product_type
        # A bare ordinary noun (for example ``paño``) is not enough to force
        # a family.  Require independent category/semantic evidence, or a
        # multi-word family phrase whose identity is unambiguous.
        strong_phrase = any(len(alias.split()) > 1 and _phrase_present(source.name_es, alias) for alias in aliases)
        if not (category_match or semantic_match or strong_phrase):
            continue
        if semantic_match:
            evidence.append("semantic:PRODUCT_TYPE"); score += 0.18
        if category_match:
            evidence.append("category_context"); score += 0.08
        # A phrase alias in the product name is stronger than one only in a description.
        if any(_phrase_present(source.name_es, alias) for alias in aliases):
            evidence.append("name_alias"); score += 0.08
        candidates.append(ProductFamilyMatch(policy.family_id, min(score, 0.99), tuple(evidence), policy.policy_version))
    if not candidates:
        return ProductFamilyMatch(UNKNOWN_FAMILY, 0.0, (), "")
    return sorted(candidates, key=lambda item: (-item.confidence, item.family_id))[0]


def build_translation_context(record: Mapping[str, Any], field_name: str, *, semantic_facts: Iterable[SemanticFact] = (), detail_key: str = "", detail_value_type: str = "", context_key: str = "", registry: ProductFamilyRegistry | None = None) -> TranslationContext:
    from .contracts import SourceFacts
    source = SourceFacts.from_record(record)
    facts = tuple(semantic_facts)
    match = classify_product_family(source, semantic_facts=facts, registry=registry)
    product_type = next((str(f.canonical_value or f.value) for f in facts if f.semantic_type == "PRODUCT_TYPE"), "")
    return TranslationContext(source.sku, field_name, source.source_hash, match.family_id, match.policy_version, product_type, source.cat1_es, source.cat2_es, detail_key, detail_value_type, context_key or detail_key, facts)


def family_policy_for(context: TranslationContext, registry: ProductFamilyRegistry | None = None) -> ProductFamilyPolicy | None:
    return (registry or ProductFamilyRegistry()).get(context.family_id)


def context_for_field(context: TranslationContext, field_name: str, *, context_key: str | None = None, detail_value_type: str | None = None) -> TranslationContext:
    """Return the same immutable context with the current field selected."""
    return TranslationContext(
        context.sku, field_name, context.source_hash, context.family_id,
        context.family_policy_version, context.product_type, context.cat1,
        context.cat2, context.detail_key, detail_value_type or context.detail_value_type,
        context_key if context_key is not None else context.context_key,
        context.semantic_facts,
    )


def cleaning_cloth_policy() -> ProductFamilyPolicy:
    return _CLEANING_CLOTH_POLICY
