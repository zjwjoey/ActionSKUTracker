"""Field-only repair pipeline for failed translation candidates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import SourceFacts
from .normalization.target import normalize_target_text
from .providers.base import TranslationProvider, TranslationRequest
from .qa import guard_translation
from .policy import DISPLAY_POLICY_PROFILE, strip_forbidden_display_tokens


@dataclass(frozen=True)
class RepairResult:
    sku: str
    field_name: str
    value: str
    qa: Mapping[str, Any]
    repair_source: str
    repair_reason: str
    parent_revision_id: str | None = None
    revision_id: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


def repair_field(record: Mapping[str, Any], field_name: str, candidate: str, *, repair_reason: str,
                 provider: TranslationProvider | None = None, registry=None,
                 parent_revision_id: str | None = None,
                 terminology: tuple[Mapping[str, Any], ...] = (),
                 semantic_facts: tuple[Any, ...] = ()) -> RepairResult:
    source = SourceFacts.from_record(record)
    value = normalize_target_text(candidate)
    qa = guard_translation(source, {field_name: value}, (field_name,), terminology=terminology, semantic_facts=semantic_facts)
    repair_source = "deterministic"
    provenance: dict[str, Any] = {"resolution_source": "DETERMINISTIC_REPAIR"}
    if qa["status"] != "PASS" and provider is not None:
        provider_terms = list(terminology)
        if field_name == "name":
            for fact in semantic_facts:
                if getattr(fact, "semantic_type", "") in {"BRAND", "IP_CHARACTER"} and str(getattr(fact, "source_text", "") or "").strip():
                    token = str(fact.source_text).strip()
                    provider_terms.append({"source": token, "target": token})
        request = TranslationRequest(
            source.sku,
            {field_name: getattr(source, {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field_name])},
            (field_name,), source.source_hash, terms=tuple(provider_terms),
        )
        response = provider.translate(request)
        value = str(response.fields.get(field_name) or "")
        if field_name == "name":
            brand_tokens = [
                str(getattr(fact, "source_text", "") or "").strip()
                for fact in semantic_facts
                if getattr(fact, "semantic_type", "") in {"BRAND", "IP_CHARACTER"}
            ]
            value = strip_forbidden_display_tokens(value, brand_tokens)
        qa = guard_translation(source, {field_name: value}, (field_name,), terminology=terminology, semantic_facts=semantic_facts)
        repair_source = response.provider
        provenance = {
            "resolution_source": "QWEN_MT",
            "provider": response.provider, "model": response.model,
            "request_id": response.request_id,
            "request_hash": response.request_hash,
            "response_hash": response.response_hash,
            "usage": dict(response.usage or {}),
            "retry_count": int((response.usage or {}).get("retry_count", 0) or 0),
            "display_policy_profile": DISPLAY_POLICY_PROFILE,
        }
    revision_id = None
    if registry is not None:
        # The registry owns the new immutable revision.  The caller supplies
        # the unit's parent revision; no existing revision is overwritten.
        revision_id = registry.record_revision_for_sku(
            source.sku, field_name, value, source_hash=source.source_hash,
            provider=repair_source, model=provenance.get("model"),
            request_hash=provenance.get("request_hash"),
            response_hash=provenance.get("response_hash"),
            request_id=provenance.get("request_id"),
            provider_call_id=provenance.get("provider_call_id"),
            provenance=provenance, repair_reason=repair_reason,
            parent_revision_id=parent_revision_id,
            qa_status=str(qa.get("status") or "FAIL"),
        )
    return RepairResult(source.sku, field_name, value, qa, repair_source, repair_reason, parent_revision_id, revision_id, provenance)
