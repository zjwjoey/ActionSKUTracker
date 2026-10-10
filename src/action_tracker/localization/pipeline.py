from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from dataclasses import replace
from typing import Any, Mapping
import hashlib
import json
from pathlib import Path
from dataclasses import asdict

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


def translate_pending_requests(requests: tuple[TranslationRequest, ...], provider: TranslationProvider,
                               checkpoint: Path) -> dict[str, Any]:
    """Resume a finite provider batch; successes remain unapproved candidates.

    The checkpoint binds the entire request plan and provider/model. It never
    writes Registry, PRIMARY or approval metadata. Provider failures propagate
    after completed responses have been atomically saved. Callers must retain
    source evidence and run the normal semantic review/QA/Apply chain.
    """
    legacy_plan = {"provider": provider.provider, "model": provider.model,
                   "requests": [asdict(request) for request in requests]}
    identity = getattr(provider, "finite_batch_identity", None)
    plan = {**legacy_plan, "checkpoint_contract": "FINITE_PROVIDER_BATCH_V2",
            "adapter_identity": identity() if callable(identity) else {
                "contract": "PROVIDER_MODEL_IDENTITY_V1"}}
    plan_hash = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()
    identities = [(request.sku, request.field_name) for request in requests]
    if len(set(identities)) != len(identities):
        raise ValueError("CANDIDATE_BATCH_DUPLICATE_FIELD")
    if any(len(request.requested_fields) != 1 or not request.source_text.strip() for request in requests):
        raise ValueError("CANDIDATE_BATCH_SINGLE_TRUSTED_SOURCE_REQUIRED")
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        legacy = "checkpoint_contract" not in (state.get("plan") or {})
        expected_plan = legacy_plan if legacy else plan
        expected_hash = hashlib.sha256(json.dumps(expected_plan, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode("utf-8")).hexdigest()
        if state.get("plan_hash") != expected_hash or state.get("plan") != json.loads(json.dumps(expected_plan)):
            raise ValueError("CANDIDATE_BATCH_PLAN_CHANGED")
        if legacy and len(state.get("responses", [])) != len(requests):
            raise ValueError("CANDIDATE_BATCH_LEGACY_CONFIG_REVIEW_REQUIRED")
    else:
        legacy = False
        state = {"plan_hash": plan_hash, "plan": plan, "responses": []}
    responses = state["responses"]
    if len(responses) > len(requests):
        raise ValueError("CANDIDATE_BATCH_INVALID_CHECKPOINT")
    for index, row in enumerate(responses):
        request = requests[index]
        if (row.get("sku"), row.get("field"), row.get("source_hash")) != (
                request.sku, request.field_name, request.source_hash):
            raise ValueError("CANDIDATE_BATCH_INVALID_CHECKPOINT")
        if row.get("decision") != "PENDING_SEMANTIC_REVIEW" or row.get("semantic_status") != "PENDING":
            raise ValueError("CANDIDATE_BATCH_INVALID_CHECKPOINT")
        if (row.get("source") != request.source_text or not str(row.get("after") or "").strip()
                or row.get("provider") != provider.provider or row.get("model") != provider.model):
            raise ValueError("CANDIDATE_BATCH_INVALID_CHECKPOINT")
    if legacy:
        # A completed old plan remains evidence, not proof that its adapter
        # configuration matches today. Never rewrite it or make new calls.
        return {"responses": responses, "provider_calls": 0, "reused_responses": len(responses),
                "production_writes": False, "plan_hash": state["plan_hash"],
                "cache_configuration_status": "LEGACY_CONFIGURATION_UNVERIFIED"}
    def save():
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(checkpoint.suffix + ".writing")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(checkpoint)
    save()  # Persist the finite plan even when the first provider call fails.
    skipped = len(responses)
    for request in requests[skipped:]:
        response = provider.translate(request)
        target = str(response.fields.get(request.field_name) or "").strip()
        if not target or response.source_hash != request.source_hash:
            raise ValueError("CANDIDATE_BATCH_INVALID_PROVIDER_RESPONSE")
        responses.append({"sku": request.sku, "field": request.field_name, "source": request.source_text,
                          "source_hash": request.source_hash, "after": target,
                          "decision": "PENDING_SEMANTIC_REVIEW", "semantic_status": "PENDING",
                          "provider": response.provider, "model": response.model,
                          "request_id": response.request_id, "request_hash": response.request_hash,
                          "response_hash": response.response_hash})
        save()
    return {"responses": responses, "provider_calls": len(responses) - skipped,
            "reused_responses": skipped, "production_writes": False, "plan_hash": plan_hash,
            "cache_configuration_status": "BOUND_ADAPTER_IDENTITY" if callable(identity) else "PROVIDER_MODEL_ONLY"}


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
    for fact in engine.generation_semantic_facts(source):
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
