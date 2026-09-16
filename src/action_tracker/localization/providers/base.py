from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping, Protocol


class ProviderError(RuntimeError):
    """A typed, retryable or terminal provider failure."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False,
                 provider: str | None = None, model: str | None = None,
                 request_hash: str | None = None, response_hash: str | None = None,
                 request_id: str | None = None, usage: Mapping[str, Any] | None = None,
                 retry_count: int = 0, latency_ms: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable
        self.provider = provider
        self.model = model
        self.request_hash = request_hash
        self.response_hash = response_hash
        self.request_id = request_id
        self.usage = dict(usage or {})
        self.retry_count = int(retry_count)
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class TranslationRequest:
    sku: str
    fields: Mapping[str, str]
    requested_fields: tuple[str, ...]
    source_hash: str
    target_language: str = "Chinese"
    terms: tuple[Mapping[str, Any], ...] = ()
    tm_entries: tuple[Mapping[str, Any], ...] = ()
    domain: str = "e-commerce"
    request_id: str = ""

    @property
    def field_name(self) -> str:
        return self.requested_fields[0] if self.requested_fields else ""

    @property
    def source_text(self) -> str:
        field = self.field_name
        source_key = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field, field)
        return str(self.fields.get(source_key, self.fields.get(field, "")) or "")

    @property
    def source_language(self) -> str:
        return "es"


@dataclass(frozen=True)
class TranslationResponse:
    fields: Mapping[str, str]
    provider: str
    model: str
    source_hash: str
    request_hash: str
    response_hash: str
    request_id: str
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw_response: Mapping[str, Any] = field(default_factory=dict)


class TranslationProvider(Protocol):
    provider: str
    model: str

    def translate(self, request: TranslationRequest) -> TranslationResponse: ...


@dataclass
class FakeTranslationProvider:
    """Deterministic fixture provider; never performs network I/O."""

    mapping: Mapping[str, str] = field(default_factory=dict)
    provider: str = "fake"
    model: str = "fixture"

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        values = {}
        for field_name in request.requested_fields:
            source_key = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name)
            values[field_name] = str(self.mapping.get(field_name, request.fields.get(source_key, request.fields.get(field_name, ""))))
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return TranslationResponse(values, self.provider, self.model, request.source_hash, digest, digest, request.request_id or "fake-request")
