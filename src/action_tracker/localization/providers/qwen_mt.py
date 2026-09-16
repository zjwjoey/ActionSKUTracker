from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .base import ProviderError, TranslationRequest, TranslationResponse
from ..protection.tokens import ProtectedTokenError, protect_text, restore_text


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


_SPANISH_MARKERS = re.compile(
    r"\b(?:de|del|la|el|los|las|para|con|sin|una|uno|un|y|en|por|más|color|colores|tamaño|unidades|piezas|pack|set)\b",
    re.IGNORECASE,
)


def _looks_like_untranslated_spanish(source: str, target: str) -> bool:
    """Return true only for a clearly unchanged Spanish response.

    A product may legitimately consist solely of an alphanumeric model or
    technical token.  Requiring a Spanish function-word marker and no CJK
    output keeps the language guard narrow and avoids rejecting those names.
    """
    source_text = str(source or "").strip()
    target_text = str(target or "").strip()
    if not source_text or not target_text or re.search(r"[\u3400-\u9fff]", target_text):
        return False
    return bool(_SPANISH_MARKERS.search(source_text)) and target_text.casefold() == source_text.casefold()


def to_qwen_term(item: Mapping[str, Any]) -> dict[str, str] | None:
    """Serialize internal terminology metadata to the MT wire schema."""
    source = str(item.get("source") or item.get("source_term") or "").strip()
    target = str(item.get("target") or item.get("target_term") or "").strip()
    return {"source": source, "target": target} if source and target else None


def to_qwen_tm(item: Mapping[str, Any]) -> dict[str, str] | None:
    """Serialize internal TM provenance to the MT wire schema."""
    source = str(item.get("source") or item.get("source_text") or "").strip()
    target = str(item.get("target") or item.get("target_text") or "").strip()
    return {"source": source, "target": target} if source and target else None


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
        # qwen-mt's ``domains`` contract is a single English prompt string,
        # not a list of labels.  Keep the prompt deterministic so request
        # hashes remain stable across retries and providers.
        domain = str(request.domain or "e-commerce").strip() or "e-commerce"
        domain_prompt = (
            "The content is from an e-commerce retail product catalog. "
            "Translate product names, specifications and descriptions accurately and concisely."
            if domain.casefold() in {"e-commerce", "ecommerce", "retail"}
            else f"The content is from the {domain} domain. Translate the supplied text accurately and concisely."
        )
        options: dict[str, Any] = {"source_lang": "Spanish", "target_lang": request.target_language, "domains": domain_prompt}
        if request.terms:
            # The dedicated MT endpoint only accepts source/target pairs in
            # ``terms``.  Scope, priority and match-mode are resolver-side
            # selection metadata and must never leak into the wire contract.
            options["terms"] = [item for raw in request.terms if (item := to_qwen_term(raw)) is not None]
        if request.tm_entries:
            options["tm_list"] = [item for raw in request.tm_entries if (item := to_qwen_tm(raw)) is not None]
        return options

    def _content(self, request: TranslationRequest, field_name: str | None = None) -> tuple[str, dict[str, Any]]:
        field = field_name or request.requested_fields[0]
        source_field = self._source_field(request, field)
        protected = protect_text(request.fields.get(source_field, request.fields.get(field, "")))
        # Qwen-MT is a dedicated text translation endpoint.  Its single user
        # message must contain the text to translate, not a chat-style prompt
        # with FIELD/RULES/TEXT wrappers.  Protected tokens and translation
        # options carry the machine-readable constraints.
        content = protected.text
        return content, {"protected": protected, "source_field": source_field}

    def _build_native_payload(self, request: TranslationRequest, field_name: str | None = None) -> tuple[dict[str, Any], Any]:
        content, meta = self._content(request, field_name)
        # DashScope's native REST contract nests generation controls under
        # ``parameters``.  The OpenAI-compatible endpoint accepts the same
        # options as an ``extra_body``/top-level extension, so the two
        # adapters must not share one guessed payload shape.
        payload = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"translation_options": self._options(request)},
        }
        return payload, meta

    def _build_compatible_payload(self, request: TranslationRequest, field_name: str | None = None) -> tuple[dict[str, Any], Any]:
        content, meta = self._content(request, field_name)
        payload = {"model": self.model, "messages": [{"role": "user", "content": content}], "translation_options": self._options(request), "temperature": 0}
        return payload, meta

    def _payload(self, request: TranslationRequest) -> dict[str, Any]:
        """Backward-compatible payload helper; now uses text-level translation."""
        if "/compatible-mode/" in self.base_url:
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
        compatible = "/compatible-mode/" in self.base_url
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
                if not text.strip():
                    raise ProviderError("QWEN_RESPONSE_EMPTY")
                # qwen-mt is a translation engine, so an unchanged Spanish
                # sentence is an unusable response rather than a successful
                # translation.  Technical-only strings (USB-C, LED, model
                # codes) are allowed; the guard requires a Spanish lexical
                # marker before failing closed.
                source_value = str(request.fields.get(meta["source_field"], request.fields.get(field_name, "")) or "")
                if request.target_language.lower().startswith("chinese") and _looks_like_untranslated_spanish(source_value, text):
                    raise ProviderError("QWEN_UNEXPECTED_LANGUAGE")
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
