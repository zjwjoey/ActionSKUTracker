from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .base import ProviderError, TranslationRequest, TranslationResponse
from ..protection.tokens import ProtectedTokenError, protect_text, restore_text


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class QwenMTProvider:
    """Alibaba Model Studio qwen-mt-flash adapter.

    qwen-mt-flash is a dedicated MT endpoint: it accepts a single user
    message and translation_options, but does not support system messages or
    response_format.  We therefore validate the plain-text JSON envelope
    locally and never pretend this endpoint is a general chat model.
    """

    base_url: str
    model: str = "qwen-mt-flash"
    api_key_env: str = "DASHSCOPE_API_KEY"
    timeout: int = 60
    max_retries: int = 2
    backoff_seconds: float = 1.5
    provider: str = "qwen_mt"

    def _payload(self, request: TranslationRequest) -> dict[str, Any]:
        envelope = {
            "task": "translate_product_fields",
            "sku": request.sku,
            "source_hash": request.source_hash,
            "target_language": request.target_language,
            "requested_fields": list(request.requested_fields),
            "source_fields": dict(request.fields),
            "output_contract": {"fields": {field: "string" for field in request.requested_fields}},
            "rules": [
                "Return one JSON object only.",
                "Translate ordinary Spanish into Simplified Chinese.",
                "Preserve SKU, model, technical tokens, numbers, units and URLs exactly.",
                "Do not output brand or IP names in Chinese display fields and never add the suffix 牌.",
                "Do not invent missing facts; use an empty string and mark review when uncertain.",
            ],
        }
        options: dict[str, Any] = {"source_lang": "Spanish", "target_lang": request.target_language, "domains": [request.domain]}
        if request.terms:
            options["terms"] = [dict(item) for item in request.terms]
        if request.tm_entries:
            options["tm_list"] = [dict(item) for item in request.tm_entries]
        if "/compatible-mode/" in self.base_url or self.base_url.rstrip("/").endswith("/v1"):
            # Compatible-mode accepts Chat Completions JSON.  qwen-mt-flash
            # still receives exactly one user message and the MT options in
            # the provider-specific body; no system/response_format fields.
            return {"model": self.model, "messages": [{"role": "user", "content": _canonical(envelope)}], "translation_options": options, "temperature": 0}
        return {"model": self.model, "input": {"messages": [{"role": "user", "content": _canonical(envelope)}]}, "translation_options": options}

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError("QWEN_API_KEY_MISSING")
        protected_fields = {name: protect_text(request.fields.get(name, "")) for name in request.requested_fields}
        protected_request = TranslationRequest(
            request.sku,
            {name: protected_fields[name].text for name in request.requested_fields},
            request.requested_fields,
            request.source_hash,
            request.target_language,
            request.terms,
            request.tm_entries,
            request.domain,
            request.request_id,
        )
        payload = self._payload(protected_request)
        request_json = _canonical(payload)
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        request_id = request.request_id or str(uuid.uuid4())
        url = self.base_url.rstrip("/")
        compatible = "/compatible-mode/" in self.base_url or self.base_url.rstrip("/").endswith("/v1")
        if compatible and not url.endswith("/chat/completions"):
            url += "/chat/completions"
        elif not compatible and not url.endswith("/services/aigc/text-generation/generation"):
            url += "/services/aigc/text-generation/generation"
        http_request = urllib.request.Request(url, data=request_json.encode("utf-8"), method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-DashScope-WorkSpace": os.environ.get("DASHSCOPE_WORKSPACE", "")})
        last_error: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout) as response:  # nosec B310 - configured endpoint
                    body = json.loads(response.read().decode("utf-8"))
                if compatible:
                    content = body.get("choices", [{}])[0].get("message", {}).get("content")
                else:
                    content = body.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content")
                    if content is None:
                        content = body.get("output", {}).get("text")
                if isinstance(content, list):
                    content = "".join(str(part.get("text", "")) if isinstance(part, Mapping) else str(part) for part in content)
                if not isinstance(content, str):
                    raise ProviderError("QWEN_RESPONSE_CONTENT_MISSING")
                text = content.strip().lstrip("\ufeff")
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text
                    text = text.rsplit("```", 1)[0].strip()
                result = json.loads(text)
                fields = result.get("fields") if isinstance(result, Mapping) else None
                if not isinstance(fields, Mapping):
                    raise ProviderError("QWEN_RESPONSE_SCHEMA_INVALID")
                try:
                    normalized = {name: restore_text(str(fields.get(name, "")), protected_fields[name]) for name in request.requested_fields}
                except ProtectedTokenError as exc:
                    raise ProviderError("QWEN_PROTECTED_TOKEN_MISMATCH", str(exc)) from exc
                response_hash = hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
                usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
                return TranslationResponse(normalized, self.provider, self.model, request.source_hash, request_hash, response_hash, request_id, usage, body)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                last_error = ProviderError(f"QWEN_HTTP_{exc.code}", retryable=retryable)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = ProviderError("QWEN_NETWORK_ERROR", str(exc), retryable=True)
            except json.JSONDecodeError as exc:
                last_error = ProviderError("QWEN_RESPONSE_JSON_INVALID", str(exc))
            except ProviderError as exc:
                last_error = exc
            if not last_error.retryable or attempt >= self.max_retries:
                break
            time.sleep(self.backoff_seconds * (2 ** attempt))
        raise last_error or ProviderError("QWEN_UNKNOWN_ERROR")
