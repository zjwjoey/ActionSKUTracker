from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import SourceFacts, source_hash
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


def make_request(record: Mapping[str, Any], requested_fields: tuple[str, ...], registry: LocalizationRegistry | None = None) -> TranslationRequest:
    source = SourceFacts.from_record(record)
    source_fields = {field: str(getattr(source, {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]) or "") for field in requested_fields}
    terms = tuple(registry.approved_terms()) if registry else ()
    return TranslationRequest(source.sku, source_fields, requested_fields, source_hash(source.as_record()), terms=terms)


def translate_candidate(record: Mapping[str, Any], requested_fields: tuple[str, ...], provider: TranslationProvider, registry: LocalizationRegistry | None = None) -> TranslationCandidate:
    source = SourceFacts.from_record(record)
    request = make_request(record, requested_fields, registry)
    response: TranslationResponse = provider.translate(request)
    qa = guard_translation(source, response.fields, requested_fields)
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
    return TranslationCandidate(source.sku, request.source_hash, requested_fields, response.fields, response.provider, response.model, response.request_hash, response.response_hash, qa)


def resolve_or_translate(record: Mapping[str, Any], requested_fields: tuple[str, ...], *, resolver: TranslationResolver,
                         provider: TranslationProvider | None = None, registry: LocalizationRegistry | None = None,
                         allow_provider: bool = False) -> dict[str, Any]:
    """Single resolver entry used by future workers and canary paths.

    A deterministic/TM/approved hit is returned without calling a provider.
    Provider output remains a PENDING candidate until QA/Owner approval.
    """
    results = resolver.resolve(record, allow_provider=allow_provider and provider is not None)
    return {"sku": str(record.get("sku") or record.get("official_sku") or ""), "fields": {field: results[field].value for field in requested_fields}, "provenance": {field: results[field].provenance | {"source": results[field].source, "status": results[field].status} for field in requested_fields}, "ready": all(results[field].approved for field in requested_fields), "production_writes": False}
