from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from action_tracker.localization.protection.tokens import ProtectedTokenError, protect_text, validate_roundtrip
from action_tracker.localization.providers.qwen_mt import QwenMTProvider
from action_tracker.localization.providers.base import FakeTranslationProvider, ProviderError, TranslationRequest
from action_tracker.localization.registry.migration import apply_migration_preview
from action_tracker.localization.runtime import shadow_run, canary
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.terminology.repository import TerminologyRepository
from action_tracker.localization.evaluation import evaluate_frozen_gold
import pytest


def test_typed_protection_preserves_sequence_and_multiplicity():
    protected = protect_text("20 mg / 20 mg USB-C")
    assert protected.token_types
    validate_roundtrip(protected.text, protected)
    try:
        validate_roundtrip(protected.text.replace("[[PROTECTED_0000]]", "[[PROTECTED_0000]] [[PROTECTED_0000]]"), protected)
    except ProtectedTokenError as exc:
        assert "ROUNDTRIP" in str(exc)
    else:
        raise AssertionError("duplicate placeholder must fail closed")


def test_qwen_builders_are_distinct_and_text_level():
    request = TranslationRequest("1", {"name": "Pack LED 9 W"}, ("name",), "h")
    provider = QwenMTProvider("https://example.test/compatible-mode/v1")
    compatible, _ = provider._build_compatible_payload(request)
    native, _ = QwenMTProvider("https://example.test")._build_native_payload(request)
    assert compatible["messages"][0]["content"] == "Pack [[PROTECTED_0000]] [[PROTECTED_0001]]"
    assert "fields" not in compatible["messages"][0]["content"]
    assert "messages" in native["input"]
    assert native["parameters"]["translation_options"]["source_lang"] == "Spanish"
    assert isinstance(native["parameters"]["translation_options"]["domains"], str)


def test_migration_apply_requires_manifest_and_explicit_commit(tmp_path: Path):
    out = tmp_path / "preview"; out.mkdir()
    manifest = {"manifest_hash": "abc", "eligible_count": 0}
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    class FakeRegistry:
        def add_tm(self, *args, **kwargs): raise AssertionError("no write in preview")
    result = apply_migration_preview(FakeRegistry(), out, manifest_hash="abc", commit=False)
    assert result["status"] == "PREVIEW_ONLY"


def test_shadow_run_is_read_only(tmp_path: Path):
    result = shadow_run([{"sku": "1", "name_es": "Auriculares", "cat1_es": "Hogar", "cat2_es": "Audio", "spec_es": "20 mg", "desc_es": "", "details_es": "Número del artículo: 1"}], output_dir=tmp_path)
    assert result["production_writes"] is False
    assert (tmp_path / "translation_run_summary.json").exists()


def test_canary_explicit_provider_flag_reaches_fake_provider(tmp_path: Path):
    resolver = TranslationResolver(provider=FakeTranslationProvider({"name": "耳机"}))
    # Keep this fixture at the provider boundary: no deterministic dictionary
    # hit is allowed to mask the explicit --provider behavior being tested.
    resolver.engine.resolve = lambda *args, **kwargs: SimpleNamespace(fields={})
    result = canary(
        [{"sku": "1", "name_es": "Auriculares", "cat1_es": "Hogar", "cat2_es": "Audio", "spec_es": "", "desc_es": "", "details_es": ""}],
        output_dir=tmp_path, field_name="name", resolver=resolver, allow_provider=True,
    )
    assert result["qwen_translated"] == 1
    assert result["production_writes"] is False


def test_fake_provider_is_deterministic_and_field_level():
    request = TranslationRequest("1", {"name": "Auriculares"}, ("name",), "h")
    result = FakeTranslationProvider({"name": "耳机"}).translate(request)
    assert result.fields == {"name": "耳机"}
    assert result.request_hash == result.response_hash


def test_terminology_scope_and_priority_are_resolved_without_global_dump(tmp_path: Path):
    registry = LocalizationRegistry(tmp_path / "terms.sqlite")
    registry.add_term("LED", "LED", approval_status="APPROVED", field_scope="name", priority=1)
    registry.add_term("LED light", "发光二极管灯", approval_status="APPROVED", product_type_scope="lighting", priority=9)
    hints = TerminologyRepository(tmp_path / "terms.sqlite").resolve("LED light", field_name="name", product_type="lighting")
    assert hints and hints[0].target_term == "发光二极管灯"


class _HTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_qwen_native_http_response_restores_typed_token(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _HTTPResponse({"output": {"choices": [{"message": {"content": "耳机 [[PROTECTED_0000]]"}}]}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = QwenMTProvider("https://example.test/api/v1", max_retries=0)
    request = TranslationRequest("1", {"name_es": "Auriculares USB-C"}, ("name",), "h")
    result = provider.translate(request)
    assert result.fields["name"] == "耳机 USB-C"
    assert calls and json.loads(calls[0].data)["parameters"]["translation_options"]["target_lang"] == "Chinese"
    native_payload = json.loads(calls[0].data)
    assert native_payload["input"]["messages"][0]["content"] == "Auriculares [[PROTECTED_0000]]"


def test_qwen_retries_429_but_not_400(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    attempts = []

    def fake_429(request, timeout):
        attempts.append(429)
        raise urllib.error.HTTPError(request.full_url, 429, "busy", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_429)
    provider = QwenMTProvider("https://example.test/compatible-mode/v1", max_retries=2, backoff_seconds=0)
    request = TranslationRequest("1", {"name_es": "Producto"}, ("name",), "h")
    with pytest.raises(ProviderError, match="QWEN_HTTP_429"):
        provider.translate(request)
    assert len(attempts) == 3


def test_qwen_empty_and_untranslated_responses_fail_closed(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    payloads = iter([
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "Set de cocina"}}]},
    ])
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: _HTTPResponse(next(payloads)))
    provider = QwenMTProvider("https://example.test/compatible-mode/v1", max_retries=0)
    request = TranslationRequest("1", {"name_es": "Set de cocina"}, ("name",), "h")
    with pytest.raises(ProviderError, match="QWEN_RESPONSE_EMPTY"):
        provider.translate(request)
    with pytest.raises(ProviderError, match="QWEN_UNEXPECTED_LANGUAGE"):
        provider.translate(request)


def test_frozen_gold_evaluation_reports_fact_metrics_without_writes():
    result = evaluate_frozen_gold([{
        "sku": "1", "field_name": "name", "source": "Auriculares USB-C 20 mg",
        "prediction": "USB-C耳机 20mg", "gold": "USB-C耳机 20mg",
    }])
    assert result["status"] == "PASS"
    assert result["records_checked"] == 1
    assert result["metrics"]["field_exact"] == 1
