from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from .contracts import LOCALIZATION_FIELDS, LocalizationField, LocalizationPlan, SourceFacts
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
        known_brands = set(self.knowledge.get("brands") or ())
        facts = parse_semantic_facts(source, known_brands=known_brands, dictionaries=self.knowledge)
        plan = plan_localization(source, facts, knowledge=self.knowledge, existing=existing)
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
            plan = LocalizationPlan(plan.sku, source.source_hash, stale_fields, plan.semantic_facts, "REVIEW_REQUIRED", tuple(dict.fromkeys((*plan.review_reasons, "SOURCE_HASH_CHANGED"))), plan.knowledge_hits, plan.ai_used)
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
            value = str(record.get(output_key) or (record.get("unit_price") if output_key == "unit_price_zh" else "") or "").strip()
            source_name = "official_unit_price" if output_key == "unit_price_zh" else str(record.get("zh_" + output_key.removesuffix("_zh") + "_source") or "primary_localization")
            freshness = str(record.get("zh_freshness_status") or "CURRENT")
            status = "READY" if value and freshness != "STALE" else ("STALE" if value else "REVIEW_REQUIRED")
            fields[output_key] = LocalizationField(value, source_name, status, str(record.get("zh_source_hash") or ""), freshness, self.policy_version, () if status == "READY" else (("STALE_LOCALIZATION",) if status == "STALE" else ("MISSING_LOCALIZATION",)))
        reasons = tuple(dict.fromkeys(r for f in fields.values() for r in f.review_reasons))
        return LocalizationPlan(source.sku, source.source_hash, fields, (), "AUTO_READY" if not reasons else "REVIEW_REQUIRED", reasons, (), False)

    @staticmethod
    def field_values(plan: LocalizationPlan) -> dict[str, str]:
        return {key: plan.fields[key].value for key in LOCALIZATION_FIELDS}
