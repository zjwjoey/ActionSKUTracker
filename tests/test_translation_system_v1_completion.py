from __future__ import annotations

import json
from pathlib import Path

from action_tracker.localization.protection.tokens import ProtectedTokenError, protect_text, validate_roundtrip
from action_tracker.localization.providers.qwen_mt import QwenMTProvider
from action_tracker.localization.providers.base import FakeTranslationProvider, TranslationRequest
from action_tracker.localization.registry.migration import apply_migration_preview
from action_tracker.localization.runtime import shadow_run
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.terminology.repository import TerminologyRepository


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
    assert compatible["messages"][0]["content"].startswith("FIELD=name")
    assert "fields" not in compatible["messages"][0]["content"]
    assert "messages" in native["input"]
    assert "translation_options" in native


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
