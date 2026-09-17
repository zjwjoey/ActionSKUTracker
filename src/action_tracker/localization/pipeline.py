from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import SourceFacts, source_hash
from .engine import LocalizationEngine
from .policy import DISPLAY_POLICY_PROFILE, strip_forbidden_display_tokens
from .qa import guard_translation
from .registry.repository import LocalizationRegistry
from .providers.base import TranslationProvider, TranslationRequest, TranslationResponse
from .resolver import TranslationResolver


@dataclass(frozen=True)
class TranslationCandidate:
    sku: str
    source_hash: str
    requested_fields: tuple[str, ...]
    fields: Mapping[str, str]
    provider: str
    model: str
    request_hash: str
    response_hash: str
    qa: Mapping[str, Any]


def make_request(record: Mapping[str, Any], requested_fields: tuple[str, ...], registry: LocalizationRegistry | None = None, *, extra_terms: tuple[Mapping[str, Any], ...] = ()) -> TranslationRequest:
    source = SourceFacts.from_record(record)
    source_fields = {field: str(getattr(source, {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]) or "") for field in requested_fields}
    terms = list(registry.approved_terms()) if registry else []
    terms.extend(extra_terms)
    return TranslationRequest(source.sku, source_fields, requested_fields, source_hash(source.as_record()), terms=tuple(terms))


def translate_candidate(record: Mapping[str, Any], requested_fields: tuple[str, ...], provider: TranslationProvider, registry: LocalizationRegistry | None = None, *, engine: LocalizationEngine | None = None) -> TranslationCandidate:
    source = SourceFacts.from_record(record)
    engine = engine or LocalizationEngine()
    plan = engine.resolve(record)
    semantic_facts = tuple(plan.semantic_facts)
    semantic_terms: list[dict[str, str]] = []
    display_tokens: list[str] = []
    semantic_types = {"PRODUCT_TYPE", "FUNCTION", "MATERIAL", "COMPATIBILITY", "CARE", "NUTRITION", "VARIANT", "DETAIL_KEY"}
    seen_terms: set[str] = set()
    for fact in semantic_facts:
        source_term = str(fact.source_text or "").strip()
        if not source_term or source_term.casefold() in seen_terms:
            continue
        if fact.semantic_type in {"BRAND", "IP_CHARACTER"}:
            display_tokens.append(source_term)
            target_term = source_term
        elif fact.semantic_type in semantic_types:
            target_term = str(fact.canonical_value or fact.value or "").strip()
            if not target_term or target_term.casefold() == source_term.casefold():
                continue
        else:
            continue
        semantic_terms.append({"source": source_term, "target": target_term})
        seen_terms.add(source_term.casefold())
    request = make_request(record, requested_fields, registry, extra_terms=tuple(semantic_terms))
    response: TranslationResponse = provider.translate(request)
    fields = dict(response.fields)
    if "name" in fields:
        fields["name"] = strip_forbidden_display_tokens(fields["name"], display_tokens)
    qa = guard_translation(source, fields, requested_fields, semantic_facts=semantic_facts)
    if registry is not None:
        registry.record_response(
            official_sku=source.sku,
            source_fields={"name_es": source.name_es, "cat1_es": source.cat1_es, "cat2_es": source.cat2_es, "spec_es": source.spec_es, "desc_es": source.desc_es, "details_es": source.details_es},
            source_hash_value=request.source_hash,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_run_id=source.source_run_id,
            response=response,
            qa=qa,
        )
    return TranslationCandidate(source.sku, request.source_hash, requested_fields, fields, response.provider, response.model, response.request_hash, response.response_hash, qa)


def resolve_or_translate(record: Mapping[str, Any], requested_fields: tuple[str, ...], *, resolver: TranslationResolver,
                         provider: TranslationProvider | None = None, registry: LocalizationRegistry | None = None,
                         allow_provider: bool = False) -> dict[str, Any]:
    """Single resolver entry used by the queue worker and canary paths.

    A deterministic/TM/approved hit is returned without calling a provider.
    Provider output remains a PENDING candidate until QA/Owner approval.
    """
    results = resolver.resolve(record, allow_provider=allow_provider and provider is not None)
    return {"sku": str(record.get("sku") or record.get("official_sku") or ""), "fields": {field: results[field].value for field in requested_fields}, "provenance": {field: results[field].provenance | {"source": results[field].source, "status": results[field].status} for field in requested_fields}, "ready": all(results[field].approved for field in requested_fields), "production_writes": False}
