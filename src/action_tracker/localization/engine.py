from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from .contracts import LOCALIZATION_FIELDS, CANONICAL_TO_ZH, ZH_TO_CANONICAL, LocalizationField, LocalizationPlan, SourceFacts
from .formatter import format_unit_price
from .planner import plan_localization
from .semantic import parse_semantic_facts
from .validator import LocalizationValidation, validate_plan


class LocalizationEngine:
    """Single deterministic resolver used by dictionary and export callers.

    ``resolve`` never writes SQLite or calls a provider.  Unknown values are
    represented as review reasons; the optional AI adapter is invoked only by
    ``resolve_unknown`` in a separate, explicit step.
    """

    def __init__(self, *, knowledge: Mapping[str, Any] | None = None, policy_version: str | None = None):
        self.knowledge = dict(knowledge or {})
        self.policy_version = policy_version or "CHINESE_LOCALIZATION_STANDARD_V1"

    def source_facts(self, record: Mapping[str, Any]) -> SourceFacts:
        return SourceFacts.from_record(record)

    def resolve(self, record: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None) -> LocalizationPlan:
        source = self.source_facts(record)
        # Product dictionary values are SKU-scoped knowledge.  Merge the
        # current SKU's reviewed fields into the otherwise shared knowledge
        # snapshot before planning; without this, runtime Resolver/Canary
        # paths fall back to a generic product type (e.g. ``清洁布``) and drop
        # identity facts such as ``微纤维`` or ``地板`` from the name.
        record_knowledge = dict(self.knowledge)
        trusted_products = self.knowledge.get("trusted_product_by_sku")
        # Loaded knowledge snapshots expose the trust-gated view.  Legacy
        # in-memory fixtures without that view remain backward compatible.
        product_map = trusted_products if trusted_products is not None else (self.knowledge.get("product_by_sku") or {})
        product = product_map.get(source.sku, {})
        if trusted_products is not None and isinstance(product, Mapping):
            row_hash = str(product.get("source_hash") or "").strip()
            if row_hash and row_hash != source.source_hash:
                product = {}
                legacy = (self.knowledge.get("product_by_sku") or {}).get(source.sku, {})
                if isinstance(legacy, Mapping):
                    self.knowledge.setdefault("product_trust_by_sku", {})[source.sku] = "STALE_LOCKED_KNOWLEDGE" if str(legacy.get("locked") or "").strip().lower() in {"1", "true", "yes", "locked"} else "STALE"
        if isinstance(product, Mapping):
            record_knowledge.update({
                "name_zh": product.get("name_zh_standard") or product.get("name_zh") or "",
                "cat1_zh": product.get("cat1_zh_standard") or product.get("cat1_zh") or "",
                "cat2_zh": product.get("cat2_zh_standard") or product.get("cat2_zh") or "",
                "spec_zh": product.get("spec_zh_standard") or product.get("spec_zh") or "",
                "desc_zh": product.get("desc_zh") or product.get("description_zh") or "",
                "details_zh": product.get("details_zh") or product.get("details") or "",
            })
        known_brands = set(record_knowledge.get("brands") or ())
        facts = parse_semantic_facts(source, known_brands=known_brands, dictionaries=record_knowledge)
        plan = plan_localization(source, facts, knowledge=record_knowledge, existing=existing)
        from .product_family import build_translation_context
        from dataclasses import replace
        plan = replace(plan, context=build_translation_context(record, "", semantic_facts=plan.semantic_facts))
        existing_hash = str((existing or {}).get("source_hash") or "")
        if existing_hash and existing_hash != source.source_hash and not bool((existing or {}).get("retranslate")):
            # Daily observation may detect changed Spanish facts before a new
            # approved Chinese result exists.  Keep the old text/provenance
            # and make staleness explicit; never silently promote it to a
            # fresh CURRENT localization.
            stale_fields = {}
            for key, field in plan.fields.items():
                old = (existing or {}).get(key) or (existing or {}).get(key + "_zh")
                if old:
                    stale_fields[key] = LocalizationField(str(old), "existing_localization", "STALE", existing_hash, "STALE", field.policy_version, ("SOURCE_HASH_CHANGED",), field.provenance)
                else:
                    stale_fields[key] = field
            plan = LocalizationPlan(plan.sku, source.source_hash, stale_fields, plan.semantic_facts, "REVIEW_REQUIRED", tuple(dict.fromkeys((*plan.review_reasons, "SOURCE_HASH_CHANGED"))), plan.knowledge_hits, plan.ai_used, plan.context)
        return plan

    def validate(self, record: Mapping[str, Any], plan: LocalizationPlan) -> LocalizationValidation:
        source = self.source_facts(record)
        allowed = {f.value for f in plan.semantic_facts if f.semantic_type in {"BRAND", "SERIES", "IP_CHARACTER", "MODEL", "TECH_TOKEN", "STANDARD_UNIT"}}
        return validate_plan(source, plan, allowed_tokens=allowed)

    def resolve_many(self, records: Iterable[Mapping[str, Any]], *, existing: Mapping[str, Mapping[str, Any]] | None = None) -> list[tuple[LocalizationPlan, LocalizationValidation]]:
        output = []
        for record in records:
            plan = self.resolve(record, existing=(existing or {}).get(str(record.get("sku") or record.get("official_sku") or "")))
            output.append((plan, self.validate(record, plan)))
        return output

    def primary_export_plan(self, record: Mapping[str, Any]) -> LocalizationPlan:
        """Build a read-only plan from already-applied PRIMARY zh values.

        Export must not translate or guess.  This adapter gives the export
        layer the same field contract as enrichment while preserving the
        applied value, source and freshness metadata from SQLite.
        """
        source = self.source_facts(record)
        fields = {}
        mapping = (("name_zh", "name"), ("cat1_zh", "cat1"), ("cat2_zh", "cat2"), ("spec_zh", "spec"), ("unit_price_zh", "unit_price"), ("desc_zh", "description"), ("details_zh", "details"))
        for output_key, _ in mapping:
            value = str(record.get(output_key) or (format_unit_price(str(record.get("unit_price") or "")) if output_key == "unit_price_zh" else "") or "").strip()
            canonical = ZH_TO_CANONICAL[output_key]
            metadata = (record.get("zh_field_provenance") or {}).get(canonical) or {}
            source_name = "official_unit_price" if output_key == "unit_price_zh" else str(metadata.get("source") or record.get("zh_" + canonical + "_source") or "primary_localization")
            freshness = str(metadata.get("freshness_status") or record.get("zh_freshness_status") or "CURRENT").upper()
            review = str(metadata.get("review_status") or record.get("zh_review_status") or "").upper()
            approved = review in {"VERIFIED", "APPROVED", "HUMAN_REVIEWED"}
            source_absent = review == "APPROVED_SOURCE_ABSENT"
            status = "READY" if freshness == "CURRENT" and ((value and (approved or output_key == "unit_price_zh")) or source_absent) else ("STALE" if value and freshness == "STALE" else "REVIEW_REQUIRED")
            reasons = () if status == "READY" else (("STALE_LOCALIZATION",) if status == "STALE" else ("MISSING_LOCALIZATION",))
            fields[output_key] = LocalizationField(value, source_name, status, str(metadata.get("source_hash") or record.get("zh_source_hash") or ""), freshness, self.policy_version, reasons)
        reasons = tuple(dict.fromkeys(r for f in fields.values() for r in f.review_reasons))
        from .product_family import build_translation_context
        return LocalizationPlan(source.sku, source.source_hash, fields, (), "AUTO_READY" if not reasons else "REVIEW_REQUIRED", reasons, (), False, build_translation_context(record, ""))

    @staticmethod
    def field_values(plan: LocalizationPlan) -> dict[str, str]:
        return {key: plan.fields[key].value for key in LOCALIZATION_FIELDS}
