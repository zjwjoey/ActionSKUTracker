from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


class ProviderError(RuntimeError):
    """A typed, retryable or terminal provider failure."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable


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
