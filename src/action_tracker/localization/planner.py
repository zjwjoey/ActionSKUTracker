from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .contracts import LocalizationField, LocalizationPlan, SemanticFact, SourceFacts
from .formatter import format_details, format_spec, format_text, format_unit_price
from .policy import FIXED_CAT1, has_ordinary_spanish, map_cat1

_FIELD_NAMES = {"name_zh": "name", "cat1_zh": "cat1", "cat2_zh": "cat2", "spec_zh": "spec", "unit_price_zh": "unit_price", "desc_zh": "description", "details_zh": "details"}


def _dict_value(mapping: Mapping[str, Any] | None, *keys: str) -> str:
    if not mapping: return ""
    if isinstance(mapping, str):
        return mapping.strip()
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, Mapping): value = value.get("value")
        if value: return str(value).strip()
    return ""


def plan_localization(source: SourceFacts, facts: tuple[SemanticFact, ...], *, knowledge: Mapping[str, Any] | None = None, existing: Mapping[str, Any] | None = None) -> LocalizationPlan:
    knowledge = knowledge or {}; existing = existing or {}
    hits: list[str] = []
    def value(field: str, fallback: str = "") -> tuple[str, str]:
        v = _dict_value(knowledge.get(field), "value", field) or _dict_value(existing, field, field + "_zh")
        if v: hits.append(field)
        return v or fallback, "knowledge" if v else "deterministic"
    product_type = next((f.canonical_value or f.value for f in facts if f.semantic_type == "PRODUCT_TYPE"), "")
    brand = next((f.value for f in facts if f.semantic_type == "BRAND"), "")
    allowed_tokens = {f.value for f in facts if f.semantic_type in {"BRAND", "SERIES", "IP_CHARACTER", "MODEL", "TECH_TOKEN", "STANDARD_UNIT", "SOCKET", "INTERFACE"}}
    def clean(value: str) -> bool:
        return bool(value) and not has_ordinary_spanish(value, allowed_tokens=allowed_tokens)
    name, ns = value("name_zh")
    if not name:
        # Colors, quantities and dimensions are selection parameters and are
        # deliberately kept out of the stable product name.  Identity-level
        # interfaces/models may be retained when they define the product.
        identity = [f.canonical_value or f.value for f in facts
                    if f.semantic_type in {"INTERFACE", "IP_CHARACTER"}
                    ]
        name = "".join(x for x in (brand + "牌" if brand else "", product_type, *identity) if x)
    elif brand and not name.startswith(brand + "牌"):
        # Confirmed brand evidence may decorate a dictionary title; an
        # explicit field-level manual override can opt out by passing the
        # already-prefixed value.
        name = brand + "牌" + name
    if not name:
        name = source.name_es
    cat1, cs = value("cat1_zh", map_cat1(source.cat1_es, knowledge.get("cat1_map")))
    cat2, c2s = value("cat2_zh", _dict_value(knowledge.get("cat2_map"), source.cat2_es) or source.cat2_es)
    spec, ss = value("spec_zh")
    if not spec:
        spec = format_spec(source.spec_es)
        # Name/description/details can contain selection parameters.  Add
        # them to the specification when the official spec field omitted
        # them, while preserving the source evidence for audit/debugging.
        rendered_spec = spec.casefold()
        rendered_name = name.casefold()
        extra: list[str] = []
        for fact in facts:
            if fact.semantic_type not in {"SIZE_DIMENSION", "CAPACITY", "WEIGHT", "QUANTITY", "VOLTAGE", "POWER", "CURRENT", "FREQUENCY", "BATTERY_CAPACITY", "SOCKET", "INTERFACE", "PROTECTION_RATING", "MODEL", "STANDARD_UNIT", "COLOR", "VARIANT", "COMPATIBILITY"}:
                continue
            rendered = format_spec(fact.canonical_value or fact.value)
            if rendered and rendered.casefold() not in rendered_spec and rendered.casefold() not in rendered_name and rendered.casefold() not in {x.casefold() for x in extra}:
                extra.append(rendered)
        if extra:
            spec = "｜".join(x for x in (spec, *extra) if x)
    else:
        spec = format_spec(spec)
    unit_price, ups = value("unit_price_zh", format_unit_price(source.unit_price_es))
    desc, ds = value("desc_zh", format_text(source.desc_es))
    details, dts = value("details_zh", format_details(source.details_es))
    details = format_details(details)
    placements = {"PRODUCT_TYPE": "name", "BRAND": "name", "SERIES": "name", "IP_CHARACTER": "name", "MODEL": "spec", "TECH_TOKEN": "spec", "STANDARD_UNIT": "spec", "SIZE_DIMENSION": "spec", "CAPACITY": "spec", "WEIGHT": "spec", "QUANTITY": "spec", "COLOR": "spec", "VARIANT": "spec", "MATERIAL": "name", "FUNCTION": "description", "COMPATIBILITY": "spec", "VOLTAGE": "spec", "POWER": "spec", "CURRENT": "spec", "FREQUENCY": "spec", "BATTERY_CAPACITY": "spec", "SOCKET": "spec", "INTERFACE": "spec", "PROTECTION_RATING": "spec", "CARE": "details", "NUTRITION": "name", "DETAIL_KEY": "details", "DESCRIPTION_FACT": "description"}
    planned_facts = tuple(SemanticFact(f.semantic_type, f.source_text, f.value, f.canonical_value, f.source_field, f.evidence, f.confidence, placements.get(f.semantic_type, "review"), f.source_hash or source.source_hash) for f in facts)
    # Every semantic fact receives explicit placement evidence.  This is
    # intentionally field-level provenance rather than a single opaque
    # translator provenance string.
    def provenance(target: str) -> tuple[dict[str, Any], ...]:
        return tuple({"semantic_type": f.semantic_type, "source_text": f.source_text,
                      "source_field": f.source_field, "evidence": f.evidence,
                      "placement": f.placement} for f in planned_facts if f.placement == target)

    fields = {
        "name_zh": LocalizationField(name, ns, "READY" if clean(name) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("name"), review_reasons=() if clean(name) else ("NAME_REVIEW", "SPANISH_RESIDUAL")),
        "cat1_zh": LocalizationField(cat1, cs, "READY" if cat1 in FIXED_CAT1 else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("cat1"), review_reasons=() if cat1 in FIXED_CAT1 else ("CATEGORY_REVIEW",)),
        "cat2_zh": LocalizationField(cat2, c2s, "READY" if (not source.cat2_es or clean(cat2)) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("cat2"), review_reasons=() if (not source.cat2_es or clean(cat2)) else ("CATEGORY_REVIEW",)),
        "spec_zh": LocalizationField(spec, ss, "READY" if clean(spec) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("spec"), review_reasons=() if clean(spec) else ("SPEC_FORMAT_REVIEW",)),
        "unit_price_zh": LocalizationField(unit_price, ups, "READY" if (not source.unit_price_es or clean(unit_price)) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("unit_price")),
        "desc_zh": LocalizationField(desc, ds, "READY" if (not source.desc_es or clean(desc)) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("description"), review_reasons=() if (not source.desc_es or clean(desc)) else ("DESCRIPTION_REVIEW",)),
        "details_zh": LocalizationField(details, dts, "READY" if (not source.details_es or clean(details)) else "REVIEW_REQUIRED", source.source_hash, "CURRENT", provenance=provenance("details"), review_reasons=() if (not source.details_es or clean(details)) else ("DETAIL_VALUE_REVIEW",)),
    }
    reasons = tuple(dict.fromkeys(r for f in fields.values() for r in f.review_reasons))
    if not product_type and source.name_es:
        reasons = tuple(dict.fromkeys((*reasons, "PRODUCT_TYPE_REVIEW")))
    readiness = "AUTO_READY" if all(f.status == "READY" for f in fields.values()) else "REVIEW_REQUIRED"
    return LocalizationPlan(source.sku, source.source_hash, fields, planned_facts, readiness, reasons, tuple(hits), False)
