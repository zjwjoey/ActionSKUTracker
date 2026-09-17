from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from dataclasses import replace
from typing import Any, Mapping

from .contracts import SourceFacts, source_hash
from .engine import LocalizationEngine
from .policy import DISPLAY_POLICY_PROFILE, strip_forbidden_display_tokens
from .qa import guard_translation
from .canonical_qa import canonical_guard
from .product_family import context_for_field, family_policy_for
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
    context = getattr(plan, "context", None)
    semantic_terms: list[dict[str, str]] = []
    display_tokens: list[str] = []
    semantic_types = {"PRODUCT_TYPE", "FUNCTION", "MATERIAL", "COMPATIBILITY", "CARE", "NUTRITION", "VARIANT", "DETAIL_KEY"}
    seen_terms: set[str] = set()
    for fact in semantic_facts:
        source_term = str(fact.source_text or "").strip()
        source_field = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}
        if requested_fields and fact.source_field and not any(fact.source_field == source_field.get(field, field) for field in requested_fields):
            continue
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
    if context is not None:
        policy = family_policy_for(context)
        if policy:
            source_text = str(getattr(source, {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(requested_fields[0] if requested_fields else "", ""), "") or "")
            for rule in policy.terms_for(requested_fields[0] if requested_fields else "", context.context_key):
                if rule.source_term.casefold() in source_text.casefold() and rule.source_term.casefold() not in seen_terms:
                    semantic_terms.append({"source": rule.source_term, "target": rule.target_term, "family_scope": context.family_id, "context_key": rule.context_key or ""})
                    seen_terms.add(rule.source_term.casefold())
    request = make_request(record, requested_fields, registry, extra_terms=tuple(semantic_terms))
    if context is not None:
        context = context_for_field(context, requested_fields[0] if requested_fields else "")
        request = replace(request, family_id=context.family_id, family_policy_version=context.family_policy_version, context_key=context.context_key, product_type=context.product_type, context=context.as_dict(), domain=(f"Retail e-commerce {context.family_id}" if context.family_id != "UNKNOWN" else "e-commerce"))
    response: TranslationResponse = provider.translate(request)
    fields = dict(response.fields)
    if "name" in fields:
        fields["name"] = strip_forbidden_display_tokens(fields["name"], display_tokens)
    qa = guard_translation(source, fields, requested_fields, semantic_facts=semantic_facts)
    canonical = canonical_guard(context, fields, production=False) if context is not None else {"status": "PASS", "findings": []}
    qa = {**qa, "canonical": canonical,
          "canonical_qa_status": canonical.get("status", "NOT_RUN"),
          "canonical_findings": canonical.get("findings", []),
          "family_id": context.family_id if context is not None else "UNKNOWN",
          "family_policy_version": context.family_policy_version if context is not None else "UNKNOWN",
          "context_key": context.context_key if context is not None else "",
          "overall_ready": qa.get("fact_status") == "PASS" and canonical.get("status") in {"PASS", "NOT_REQUIRED"}}
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
