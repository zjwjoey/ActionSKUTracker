from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.request
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .contracts import (
    POLICY_VERSION, SourceFacts, CANONICAL_AI_FIELDS, CANONICAL_TO_SOURCE,
    ZH_TO_CANONICAL,
)
from .policy import FIXED_CAT1, has_ordinary_spanish
from .validator import _NUMBER


def _system_prompt() -> str:
    return (
        "你是商品结构化中文标准化引擎。只返回一个 JSON 对象，禁止 Markdown、代码围栏、解释和思考过程。"
        "JSON 顶层必须包含 sku、source_hash、fields、semantic_items、product_type_candidate、detail_key_candidates、tech_token_candidates、confidence；"
        "fields 必须是对象，只填写 requested_fields 中的字段。不得改变 SKU、source_hash、价格、URL 或官方西语事实。"
        "普通西班牙语必须翻译成中文；品牌、型号、技术词和标准单位按原文保留。必须保留所有数字。"
        "未知或无法核实的内容放入 review_notes，不得臆造。"
    )


def _user_prompt(source: SourceFacts, requested_fields: tuple[str, ...]) -> str:
    return json.dumps({
        "policy_version": POLICY_VERSION,
        "sku": source.sku, "canonical_id": source.canonical_id,
        "name_es": source.name_es, "cat1_es": source.cat1_es, "cat2_es": source.cat2_es,
        "spec_es": source.spec_es, "desc_es": source.desc_es, "details_es": source.details_es,
        "requested_fields": list(requested_fields),
        "response_contract": {
            "sku": source.sku, "source_hash": source.source_hash,
            "fields": {field: "中文字符串" for field in requested_fields},
            "semantic_items": [], "product_type_candidate": None,
            "detail_key_candidates": [], "tech_token_candidates": [],
            "confidence": 0.0, "review_notes": "",
        },
    }, ensure_ascii=False)


_KNOWN_TECH_TOKENS = (
    "USB-C", "USB", "LED", "HDMI", "E27", "IP44", "A3", "AAA",
    "Bluetooth", "Wi-Fi",
)


def extract_technical_tokens(text: str) -> tuple[str, ...]:
    """Extract source technical identifiers that must survive translation.

    Known identifiers are preferred over heuristic all-caps matching.  The
    fallback keeps model/unit tokens containing digits or a hyphen while
    avoiding ordinary Spanish words.
    """
    text = str(text or "")
    found: list[str] = []
    for token in _KNOWN_TECH_TOKENS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            found.append(token)
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+(?![A-Za-z0-9])", text):
        if token.casefold() not in {item.casefold() for item in found}:
            found.append(token)
    for token in re.findall(r"(?<![A-Za-z])(?:[A-Z]{2,}\d*|[A-Z]\d+|\d+[A-Za-z]+)(?![A-Za-z0-9])", text):
        if token.casefold() not in {item.casefold() for item in found}:
            found.append(token)
    return tuple(found)


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
        payload = {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": _user_prompt(source, requested_fields)}], "response_format": {"type": "json_object"}}
        request = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310 - explicit configured endpoint
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        result = json.loads(content)
        if not isinstance(result, Mapping): raise ValueError("LOCALIZATION_AI_SCHEMA_INVALID")
        return result


@dataclass
class LocalOpenAICompatibleProvider(OpenAICompatibleProvider):
    """OpenAI-compatible local model endpoint (Ollama/LM Studio/vLLM).

    Local servers normally do not require credentials.  The endpoint and
    model remain configuration, so the provider is not coupled to Qwen or to
    a particular local runtime.
    """
    api_key_env: str | None = None
    provider: str = "local_openai_compatible"
    provider_options: Mapping[str, Any] = field(default_factory=dict)

    def complete(self, source: SourceFacts, requested_fields: tuple[str, ...]) -> Mapping[str, Any]:
        payload = {"model": self.model, "temperature": 0, "stream": False, "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(source, requested_fields)},
        ], "response_format": {"type": "json_object"}}
        # Optional capability-specific knobs are never sent to generic
        # providers.  Ollama Qwen deployments can set think=false explicitly
        # when supported; an unset option keeps the adapter portable.
        if "think" in self.provider_options:
            payload["think"] = bool(self.provider_options["think"])
        if isinstance(self.provider_options.get("response_format"), Mapping):
            payload["response_format"] = dict(self.provider_options["response_format"])
        headers = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310 - explicit configured endpoint
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "") if isinstance(part, Mapping) else str(part) for part in content)
        text = str(content).lstrip("\ufeff").strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        result = json.loads(text)
        if not isinstance(result, Mapping):
            raise ValueError("LOCALIZATION_AI_SCHEMA_INVALID")
        return result


def validate_ai_response(payload: Mapping[str, Any], source: SourceFacts, requested_fields: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    """Validate the provider envelope before it becomes a candidate."""
    reasons: list[str] = []
    requested_fields = tuple(requested_fields)
    if not set(requested_fields) <= set(CANONICAL_AI_FIELDS):
        reasons.append("AI_REQUESTED_FIELD_NON_CANONICAL")
    if not isinstance(payload, Mapping):
        return False, ("AI_RESPONSE_NOT_OBJECT",)
    allowed_top = {"fields", "product_type_candidate", "semantic_items", "detail_key_candidates", "tech_token_candidates", "placement", "confidence", "review_notes", "sku", "canonical_id", "product_url", "current_price", "original_price", "source_hash"}
    if set(payload) - allowed_top:
        reasons.append("AI_RESPONSE_UNKNOWN_KEY")
    if "sku" in payload and str(payload.get("sku") or "").strip() != source.sku:
        reasons.append("AI_SKU_MISMATCH")
    if "canonical_id" in payload and str(payload.get("canonical_id") or "").strip() != source.canonical_id:
        reasons.append("AI_CANONICAL_ID_MISMATCH")
    if "product_url" in payload and str(payload.get("product_url") or "").strip() != source.product_url:
        reasons.append("AI_URL_MISMATCH")
    if "source_hash" in payload and str(payload.get("source_hash") or "") != source.source_hash:
        reasons.append("AI_SOURCE_HASH_MISMATCH")
    for key in ("current_price", "original_price"):
        if key in payload and str(payload.get(key)) != str(getattr(source, key)):
            reasons.append(f"AI_{key.upper()}_MISMATCH")
    for key in ("semantic_items", "detail_key_candidates", "tech_token_candidates"):
        if key in payload and not isinstance(payload[key], list):
            reasons.append(f"AI_{key.upper()}_NOT_LIST")
    for key in ("product_type_candidate", "placement"):
        if key in payload and payload[key] is not None and not isinstance(payload[key], Mapping):
            reasons.append(f"AI_{key.upper()}_NOT_OBJECT")
    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        reasons.append("AI_FIELDS_NOT_OBJECT")
        fields = {}
    else:
        unknown = set(fields) - set(requested_fields)
        if unknown:
            reasons.append("AI_FIELD_NOT_REQUESTED")
    if not fields:
        reasons.append("AI_FIELDS_EMPTY")
    source_tokens = set()
    for source_text in (source.name_es, source.spec_es, source.desc_es, source.details_es):
        source_tokens.update(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ-]*", source_text or ""))
    # Preserve only tokens that are plausibly technical identifiers/units.  A
    # sentence-initial Spanish word such as ``Lámpara`` is capitalized too,
    # but must still be rejected as residual Spanish; therefore do not use a
    # simple ``any(ch.isupper())`` test here.
    technical_units = {"mAh", "mg", "mcg", "ml", "mm", "cm", "kg", "Hz", "V", "W", "L", "g"}
    technical_tokens = {
        token for token in source_tokens
        if any(ch.isdigit() for ch in token)
        or "-" in token
        or token in technical_units
        or (token.isupper() and len(token) >= 2)
    }
    for key, value in fields.items():
        if not isinstance(value, str): reasons.append(f"AI_{str(key).upper()}_NOT_STRING")
        elif key == "unit_price":
            reasons.append("AI_UNIT_PRICE_FORBIDDEN")
        elif has_ordinary_spanish(value, allowed_tokens=technical_tokens):
            reasons.append(f"AI_{str(key).upper()}_SPANISH_RESIDUAL")
        elif key == "cat1" and value not in FIXED_CAT1:
            reasons.append("AI_CATEGORY_NOT_FIXED")
        elif key in CANONICAL_AI_FIELDS:
            source_field = CANONICAL_TO_SOURCE.get(key)
            if source_field and source_field != "unit_price_es":
                source_text = str(getattr(source, source_field) or "")
                expected = {_n.replace(",", ".") for _n in _NUMBER.findall(str(getattr(source, source_field) or ""))}
                found = {_n.replace(",", ".") for _n in _NUMBER.findall(value)}
                if expected - found:
                    reasons.append(f"AI_{str(key).upper()}_NUMBER_DROPPED")
                expected_tokens = extract_technical_tokens(source_text)
                lowered = value.casefold()
                if any(token.casefold() not in lowered for token in expected_tokens):
                    reasons.append("AI_TECH_TOKEN_DROPPED")
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
    if provider in {"local_openai_compatible", "local", "ollama", "qwen"}:
        key_env = str(config.get("api_key_env") or "").strip() or None
        return LocalOpenAICompatibleProvider(
            str(config.get("base_url") or ""), str(config.get("model") or ""), key_env,
            int(config.get("timeout") or 120),
            provider_options=dict(config.get("provider_options") or {}),
        )
    return OpenAICompatibleProvider(str(config.get("base_url") or ""), str(config.get("model") or ""), str(config.get("api_key_env") or "ACTION_AI_API_KEY"))


def provider_health(provider: LocalizationAIProvider) -> dict[str, Any]:
    """Perform a non-mutating endpoint/model check for an explicit CLI call."""
    if isinstance(provider, DisabledProvider):
        return {"status": "DISABLED", "provider": provider.provider, "model": provider.model}
    base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
    if not base_url:
        return {"status": "INVALID_CONFIG", "provider": getattr(provider, "provider", ""), "model": getattr(provider, "model", "")}
    request = urllib.request.Request(base_url + "/models", headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=int(getattr(provider, "timeout", 60))) as response:  # nosec B310 - explicit configured endpoint
            body = json.loads(response.read().decode() or "{}")
        configured = str(getattr(provider, "model", "") or "").strip()
        models = body.get("data") if isinstance(body, Mapping) else None
        if configured and isinstance(models, list):
            ids = {str(item.get("id") or item.get("name") or "") for item in models if isinstance(item, Mapping)}
            if ids and configured not in ids and not any(configured in model_id for model_id in ids):
                return {"status": "MODEL_NOT_FOUND", "provider": getattr(provider, "provider", ""), "model": configured, "models_response": body}
        return {"status": "PASS", "provider": getattr(provider, "provider", ""), "model": configured, "models_response": body}
    except Exception as exc:
        return {"status": "LOCAL_PROVIDER_NOT_VERIFIED", "provider": getattr(provider, "provider", ""), "model": getattr(provider, "model", ""), "error": str(exc)}


def resolve_unknown(engine, record: Mapping[str, Any], plan, provider: LocalizationAIProvider, *, requested_fields: tuple[str, ...] | None = None, prompt_version: str = "localization_v1") -> dict[str, Any] | None:
    """Ask a provider only when deterministic localization is not ready.

    The return value is an auditable candidate, never a production row.  A
    ready plan short-circuits without invoking ``provider.complete``.
    """
    if plan.readiness in {"AUTO_READY", "READY"}:
        return None
    source = SourceFacts.from_record(record)
    requested = requested_fields or tuple(
        ZH_TO_CANONICAL[key] for key, field in plan.fields.items()
        if field.status != "READY" and ZH_TO_CANONICAL[key] in CANONICAL_AI_FIELDS
    )
    request_body = _user_prompt(source, requested)
    result = dict(provider.complete(source, requested))
    schema_ok, schema_reasons = validate_ai_response(result, source, requested)
    fields = result.get("fields") if isinstance(result.get("fields"), Mapping) else result
    candidate = {"sku": source.sku, "canonical_id": source.canonical_id, "source_hash": source.source_hash,
                 "policy_version": POLICY_VERSION, "prompt_version": prompt_version,
                 "provider": getattr(provider, "provider", type(provider).__name__),
                 "model": getattr(provider, "model", ""), "requested_fields": requested,
                 "fields": dict(fields), "semantic_items": result.get("semantic_items") or [],
                 "product_type_candidate": result.get("product_type_candidate"),
                 "detail_key_candidates": result.get("detail_key_candidates") or [],
                 "tech_token_candidates": result.get("tech_token_candidates") or [],
                 "confidence": result.get("confidence"),
                 "review_notes": result.get("review_notes", ""),
                 "request_hash": hashlib.sha256(request_body.encode("utf-8")).hexdigest(),
                 "response_hash": hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
                 "generated_at": datetime.now(timezone.utc).isoformat(),
                 "schema_status": "PASS" if schema_ok else "FAIL",
                 "schema_reasons": schema_reasons}
    return candidate
