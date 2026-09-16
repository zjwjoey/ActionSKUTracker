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
    max_batch_size: int = 20
    max_characters_per_request: int = 12000
    rate_limit_per_second: float = 0.0
    provider: str = "qwen_mt"

    def _source_field(self, request: TranslationRequest, field_name: str) -> str:
        return {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name)

    def _options(self, request: TranslationRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"source_lang": "Spanish", "target_lang": request.target_language, "domains": [request.domain]}
        if request.terms:
            options["terms"] = [dict(item) for item in request.terms]
        if request.tm_entries:
            options["tm_list"] = [dict(item) for item in request.tm_entries]
        return options

    def _content(self, request: TranslationRequest, field_name: str | None = None) -> tuple[str, dict[str, Any]]:
        field = field_name or request.requested_fields[0]
        source_field = self._source_field(request, field)
        protected = protect_text(request.fields.get(source_field, request.fields.get(field, "")))
        rules = (
            "Translate only the supplied Spanish text to Simplified Chinese.",
            "Return translated text only; never JSON, commentary, or markdown.",
            "Preserve every protected placeholder exactly once and in order.",
            "Do not add brand/IP names or the Chinese suffix 牌; retain model, interface, technical tokens, numbers and units.",
            "Do not invent facts or marketing claims.",
        )
        content = "\n".join((f"FIELD={field}", f"RULES={' | '.join(rules)}", f"TEXT={protected.text}"))
        return content, {"protected": protected, "source_field": source_field}

    def _build_native_payload(self, request: TranslationRequest, field_name: str | None = None) -> tuple[dict[str, Any], Any]:
        content, meta = self._content(request, field_name)
        payload = {"model": self.model, "input": {"messages": [{"role": "user", "content": content}]}, "translation_options": self._options(request)}
        return payload, meta

    def _build_compatible_payload(self, request: TranslationRequest, field_name: str | None = None) -> tuple[dict[str, Any], Any]:
        content, meta = self._content(request, field_name)
        payload = {"model": self.model, "messages": [{"role": "user", "content": content}], "translation_options": self._options(request), "temperature": 0}
        return payload, meta

    def _payload(self, request: TranslationRequest) -> dict[str, Any]:
        """Backward-compatible payload helper; now uses text-level translation."""
        if "/compatible-mode/" in self.base_url or self.base_url.rstrip("/").endswith("/v1"):
            return self._build_compatible_payload(request)[0]
        return self._build_native_payload(request)[0]

    @staticmethod
    def _response_text(body: Mapping[str, Any], compatible: bool) -> str:
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
        return text

    def _translate_one(self, request: TranslationRequest, field_name: str) -> tuple[str, str, str, str, Mapping[str, Any], int]:
        compatible = "/compatible-mode/" in self.base_url or self.base_url.rstrip("/").endswith("/v1")
        payload, meta = (self._build_compatible_payload(request, field_name) if compatible else self._build_native_payload(request, field_name))
        request_json = _canonical(payload)
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        url = self.base_url.rstrip("/")
        if compatible and not url.endswith("/chat/completions"):
            url += "/chat/completions"
        elif not compatible and not url.endswith("/services/aigc/text-generation/generation"):
            url += "/services/aigc/text-generation/generation"
        http_request = urllib.request.Request(url, data=request_json.encode("utf-8"), method="POST", headers={"Authorization": f"Bearer {os.environ.get(self.api_key_env)}", "Content-Type": "application/json", "X-DashScope-WorkSpace": os.environ.get("DASHSCOPE_WORKSPACE", "")})
        last_error: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                started = time.monotonic()
                with urllib.request.urlopen(http_request, timeout=self.timeout) as response:  # nosec B310 - configured endpoint
                    body = json.loads(response.read().decode("utf-8"))
                text = self._response_text(body, compatible)
                # Accept legacy JSON fixtures but make plain text the contract.
                if text.startswith("{"):
                    try:
                        parsed = json.loads(text)
                        text = str((parsed.get("fields") or {}).get(field_name, text))
                    except json.JSONDecodeError:
                        pass
                restored = restore_text(text, meta["protected"])
                return restored, request_hash, hashlib.sha256(text.encode("utf-8")).hexdigest(), str(body.get("request_id") or request.request_id or uuid.uuid4()), body.get("usage") if isinstance(body.get("usage"), Mapping) else {}, attempt
            except urllib.error.HTTPError as exc:
                last_error = ProviderError(f"QWEN_HTTP_{exc.code}", retryable=exc.code == 429 or exc.code >= 500)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = ProviderError("QWEN_NETWORK_ERROR", str(exc), retryable=True)
            except json.JSONDecodeError as exc:
                last_error = ProviderError("QWEN_RESPONSE_JSON_INVALID", str(exc))
            except (ProviderError, ProtectedTokenError) as exc:
                last_error = exc if isinstance(exc, ProviderError) else ProviderError("QWEN_PROTECTED_TOKEN_MISMATCH", str(exc))
            if not last_error.retryable or attempt >= self.max_retries:
                break
            time.sleep(self.backoff_seconds * (2 ** attempt))
        raise last_error or ProviderError("QWEN_UNKNOWN_ERROR")

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError("QWEN_API_KEY_MISSING")
        normalized: dict[str, str] = {}
        request_hashes: list[str] = []
        response_hashes: list[str] = []
        usage: dict[str, Any] = {}
        request_id = request.request_id or str(uuid.uuid4())
        retries = 0
        for field_name in request.requested_fields:
            if self.rate_limit_per_second and normalized:
                time.sleep(1.0 / self.rate_limit_per_second)
            value, rq, rs, rid, used, retry_count = self._translate_one(request, field_name)
            normalized[field_name] = value
            request_hashes.append(rq); response_hashes.append(rs); retries += retry_count
            for key, val in used.items():
                if isinstance(val, (int, float)):
                    usage[key] = usage.get(key, 0) + val
                else:
                    usage[key] = val
            request_id = rid or request_id
        return TranslationResponse(normalized, self.provider, self.model, request.source_hash, hashlib.sha256("|".join(request_hashes).encode()).hexdigest(), hashlib.sha256("|".join(response_hashes).encode()).hexdigest(), request_id, {**usage, "retry_count": retries, "request_count": len(request.requested_fields)}, {})

    def translate_batch(self, requests: list[TranslationRequest], *, max_batch_size: int | None = None) -> list[TranslationResponse]:
        """Deterministic bounded batch helper; each field remains text-level."""
        max_batch_size = int(max_batch_size or self.max_batch_size)
        if len(requests) > max_batch_size:
            raise ProviderError("QWEN_BATCH_LIMIT_EXCEEDED")
        if sum(len(item.source_text) for item in requests) > self.max_characters_per_request:
            raise ProviderError("QWEN_REQUEST_CHARACTER_LIMIT_EXCEEDED")
        return [self.translate(request) for request in requests]
