"""Deprecated V0.1 fixture helpers.

Workflow V2 V1 no longer imports this module.  Production-shaped execution
must use ``LocalizationRegistry`` -> ``TranslationQueueWorker`` ->
``TranslationResolver`` -> the existing QA and immutable apply path.  The
helpers remain only as a migration reference for old offline fixtures and are
not a second queue, registry, provider or apply implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..localization.contracts import SourceFacts, field_source_hash

SOURCE_BY_FIELD = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}


@dataclass(frozen=True)
class TranslationPlanItem:
    sku: str
    field_name: str
    source_hash: str
    source_text: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def plan_translations(records: Iterable[Mapping[str, Any]], *, approved: Mapping[str, Mapping[str, str]] | None = None) -> list[TranslationPlanItem]:
    approved = approved or {}
    result: list[TranslationPlanItem] = []
    for row in records:
        source = SourceFacts.from_record(row)
        by_field = approved.get(source.sku, {})
        for field, source_key in SOURCE_BY_FIELD.items():
            value = str(row.get(source_key) or "").strip()
            if not value:
                continue
            digest = field_source_hash(source.as_record(), field)
            if str(by_field.get(field) or "") == digest:
                continue
            reason = "NEW_SKU" if source.sku not in approved else "SOURCE_CHANGED_OR_UNAPPROVED"
            result.append(TranslationPlanItem(source.sku, field, source.source_hash, value, reason))
    return result


def translate_plan(records: Iterable[Mapping[str, Any]], plan: Iterable[TranslationPlanItem], provider: Any) -> list[dict[str, Any]]:
    raise RuntimeError("WORKFLOW_V2_V01_TRANSLATION_STAGE_DEPRECATED_USE_TRANSLATION_QUEUE_WORKER")


def auto_validate(results: Iterable[Mapping[str, Any]], *, source_ready: bool, fact_committed: bool) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for result in results:
        ready = bool(source_ready and fact_committed and result.get("status") == "PASS" and (result.get("qa") or {}).get("overall_ready"))
        decisions.append({"sku": result.get("sku"), "fields": list((result.get("fields") or {}).keys()),
                          "decision": "AUTO_VALIDATED" if ready else "REVIEW_REQUIRED",
                          "policy_id": "QWEN_AUTO_VALIDATE_V1", "policy_version": "QWEN_AUTO_VALIDATE_V1",
                          "source_hash": result.get("source_hash"), "qa_status": (result.get("qa") or {}).get("status", "NOT_RUN")})
    return decisions


def apply_to_fixture(records: list[dict[str, Any]], results: Iterable[Mapping[str, Any]], decisions: Iterable[Mapping[str, Any]]) -> int:
    raise RuntimeError("WORKFLOW_V2_V01_APPLY_DEPRECATED_USE_IMMUTABLE_LOCALIZATION_PATCH")
