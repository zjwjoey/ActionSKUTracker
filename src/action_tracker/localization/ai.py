from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .contracts import POLICY_VERSION, SourceFacts


class LocalizationAIProvider(Protocol):
    provider: str
    model: str
    def complete(self, source: SourceFacts, requested_fields: tuple[str, ...]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DisabledProvider:
    provider: str = "disabled"
    model: str = ""
    def complete(self, source: SourceFacts, requested_fields: tuple[str, ...]) -> Mapping[str, Any]:
        raise RuntimeError("LOCALIZATION_AI_DISABLED")


@dataclass
class FakeProvider:
    responses: Mapping[str, Mapping[str, Any]]
    provider: str = "fake"
    model: str = "fixture"
    calls: int = 0
    def complete(self, source: SourceFacts, requested_fields: tuple[str, ...]) -> Mapping[str, Any]:
        self.calls += 1
        return dict(self.responses.get(source.sku, {}))


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key_env: str = "ACTION_AI_API_KEY"
    timeout: int = 60
    provider: str = "openai_compatible"
    def complete(self, source: SourceFacts, requested_fields: tuple[str, ...]) -> Mapping[str, Any]:
        key = os.environ.get(self.api_key_env)
        if not key: raise RuntimeError("LOCALIZATION_AI_API_KEY_MISSING")
        payload = {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": "Return strict JSON only. Translate unknown Action product facts to Chinese."}, {"role": "user", "content": json.dumps({"source": source.as_record(), "requested_fields": requested_fields, "policy_version": POLICY_VERSION}, ensure_ascii=False)}]}
        request = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310 - explicit configured endpoint
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        result = json.loads(content)
        if not isinstance(result, Mapping): raise ValueError("LOCALIZATION_AI_SCHEMA_INVALID")
        return result


def validate_ai_response(payload: Mapping[str, Any], source: SourceFacts, requested_fields: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    """Validate the provider envelope before it becomes a candidate."""
    reasons: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ("AI_RESPONSE_NOT_OBJECT",)
    if set(payload) - {"fields", "product_type_candidate", "semantic_items", "detail_key_candidates", "tech_token_candidates", "placement", "confidence", "review_notes"}:
        reasons.append("AI_RESPONSE_UNKNOWN_KEY")
    fields = payload.get("fields")
    if not isinstance(fields, Mapping): reasons.append("AI_FIELDS_NOT_OBJECT")
    else:
        unknown = set(fields) - set(requested_fields)
        if unknown: reasons.append("AI_FIELD_NOT_REQUESTED")
        for key, value in fields.items():
            if not isinstance(value, str): reasons.append(f"AI_{str(key).upper()}_NOT_STRING")
    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            if not 0 <= float(confidence) <= 1: reasons.append("AI_CONFIDENCE_OUT_OF_RANGE")
        except (TypeError, ValueError): reasons.append("AI_CONFIDENCE_INVALID")
    return not reasons, tuple(dict.fromkeys(reasons))


def provider_from_config(config: Mapping[str, Any] | None) -> LocalizationAIProvider:
    config = config or {}
    if not bool(config.get("enabled") or config.get("ai_enabled")):
        return DisabledProvider()
    provider = str(config.get("provider") or "openai_compatible").lower()
    if provider in {"fake", "test"}: return FakeProvider({})
    return OpenAICompatibleProvider(str(config.get("base_url") or ""), str(config.get("model") or ""), str(config.get("api_key_env") or "ACTION_AI_API_KEY"))


def resolve_unknown(engine, record: Mapping[str, Any], plan, provider: LocalizationAIProvider, *, prompt_version: str = "localization_v1") -> dict[str, Any] | None:
    """Ask a provider only when deterministic localization is not ready.

    The return value is an auditable candidate, never a production row.  A
    ready plan short-circuits without invoking ``provider.complete``.
    """
    if plan.readiness in {"AUTO_READY", "READY"}:
        return None
    source = SourceFacts.from_record(record)
    requested = tuple(key.removesuffix("_zh") for key, field in plan.fields.items() if field.status != "READY")
    result = dict(provider.complete(source, requested))
    schema_ok, schema_reasons = validate_ai_response(result, source, requested)
    fields = result.get("fields") if isinstance(result.get("fields"), Mapping) else result
    candidate = {"sku": source.sku, "canonical_id": source.canonical_id, "source_hash": source.source_hash,
                 "policy_version": POLICY_VERSION, "prompt_version": prompt_version,
                 "provider": getattr(provider, "provider", type(provider).__name__),
                 "model": getattr(provider, "model", ""), "requested_fields": requested,
                 "fields": dict(fields), "confidence": result.get("confidence"),
                 "review_notes": result.get("review_notes", ""),
                 "schema_status": "PASS" if schema_ok else "FAIL",
                 "schema_reasons": schema_reasons}
    return candidate
