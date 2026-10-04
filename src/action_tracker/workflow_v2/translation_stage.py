"""Translation V1 planning, provider calls and field-level QA adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..localization.contracts import SourceFacts, field_source_hash
from ..localization.providers.base import TranslationRequest
from ..localization.qa import guard_translation

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
    by_sku = {str(row.get("sku") or row.get("official_sku") or ""): dict(row) for row in records}
    grouped: dict[str, list[TranslationPlanItem]] = {}
    for item in plan:
        grouped.setdefault(item.sku, []).append(item)
    results: list[dict[str, Any]] = []
    for sku, items in grouped.items():
        row = by_sku[sku]
        source = SourceFacts.from_record(row)
        fields = tuple(item.field_name for item in items)
        request = TranslationRequest(sku=sku, fields=source.as_record(), requested_fields=fields, source_hash=source.source_hash, request_id=f"workflow-v2:{sku}:{source.source_hash[:12]}")
        try:
            response = provider.translate(request)
            candidate = dict(getattr(response, "fields", {}) or {})
            qa = guard_translation(source, candidate, fields, production=False)
            status = "PASS" if qa.get("overall_ready") else "REVIEW_REQUIRED"
            results.append({"sku": sku, "source_hash": source.source_hash, "requested_fields": list(fields),
                            "fields": candidate, "status": status, "qa": qa,
                            "provider": getattr(response, "provider", getattr(provider, "provider", "unknown")),
                            "model": getattr(response, "model", getattr(provider, "model", "unknown")),
                            "request_id": getattr(response, "request_id", request.request_id)})
        except Exception as exc:
            results.append({"sku": sku, "source_hash": source.source_hash, "requested_fields": list(fields),
                            "fields": {}, "status": "RETRY" if getattr(exc, "retryable", False) else "FAILED",
                            "qa": {"status": "NOT_RUN", "overall_ready": False, "error": str(exc)},
                            "provider": getattr(provider, "provider", "unknown"), "model": getattr(provider, "model", "unknown"),
                            "error": str(exc)})
    return results


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
    allowed = {str(item.get("sku")) for item in decisions if item.get("decision") == "AUTO_VALIDATED"}
    by_sku = {str(row.get("sku") or row.get("official_sku")): row for row in records}
    changed = 0
    for result in results:
        if str(result.get("sku")) not in allowed:
            continue
        row = by_sku.get(str(result.get("sku")))
        if not row: continue
        for field, value in (result.get("fields") or {}).items():
            row[{"description": "desc_zh", "details": "details_zh", "name": "name_zh", "cat1": "cat1_zh", "cat2": "cat2_zh", "spec": "spec_zh"}.get(field, f"{field}_zh")] = value
            changed += 1
        row["translation_status"] = "AUTO_VALIDATED"
    return changed
