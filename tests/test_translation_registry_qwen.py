from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from action_tracker.database.connection import connect
from action_tracker.localization.hashes import localization_source_hash_v1, source_hash_v2
from action_tracker.services.hashing import localization_source_hash
from action_tracker.localization.providers.base import ProviderError, TranslationRequest
from action_tracker.localization.providers.qwen_mt import QwenMTProvider
from action_tracker.localization.protection.tokens import ProtectedTokenError, protect_text, restore_text
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.pipeline import make_request
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.ai import QwenMTCompatibleProvider
from action_tracker.localization.providers.base import TranslationResponse
from action_tracker.localization.normalization import normalize_source_text, parse_detail_fields, format_detail_fields
from action_tracker.localization.registry.migration import build_migration_preview


def test_protected_tokens_round_trip_and_reject_missing():
    protected = protect_text("Auriculares USB-C 20 mg, SKU 3211585, https://example.test/x")
    assert "USB-C" not in protected.text
    assert restore_text(protected.text, protected).endswith("https://example.test/x")
    with pytest.raises(ProtectedTokenError):
        restore_text(protected.text.replace("[[PROTECTED_0000]]", ""), protected)


def test_hash_contract_is_deterministic_and_distinct():
    fields = {"name_es": "A", "cat1_es": "B", "cat2_es": "C", "spec_es": "D", "desc_es": "E", "details_es": "F"}
    assert localization_source_hash_v1(fields) == localization_source_hash_v1(dict(reversed(list(fields.items()))))
    assert localization_source_hash_v1(fields) == localization_source_hash(fields)
    assert source_hash_v2(fields) != source_hash_v2({**fields, "details_es": "G"})


def test_registry_keeps_source_and_revisions_append_only(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    source_id = registry.register_source("123456", {"name_es": "Producto", "details_es": "Número del artículo: 123456"}, "hash-1", observed_at="2026-09-16")
    assert registry.register_source("123456", {"name_es": "Producto"}, "hash-1", observed_at="2026-09-16") == source_id
    with connect(db_path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE source_version_id=? AND field_name='name_es'", (source_id,)).fetchone()[0]
    revision_id = registry.record_revision(unit_id=unit_id, target_text="商品", provider="fake", model="fixture", request_hash="rq", response_hash="rs")
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM translation_source_versions").fetchone()[0] == 1
        assert db.execute("SELECT target_text FROM translation_revisions WHERE revision_id=?", (revision_id,)).fetchone()[0] == "商品"


def test_registry_source_change_stales_old_revision_and_blocks_reuse(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    first = registry.register_source("123456", {"name_es": "Producto"}, "hash-1", observed_at="2026-09-16")
    with connect(db_path) as db:
        unit_id = db.execute("SELECT unit_id FROM translation_units WHERE source_version_id=?", (first,)).fetchone()[0]
    revision_id = registry.record_revision(unit_id=unit_id, target_text="商品", provider="fake", model="fixture", request_hash="rq", response_hash="rs", source_hash="hash-1", qa_status="PASS", review_status="HUMAN_REVIEWED")
    assert registry.approve_revision(revision_id, actor="human:test") is True
    registry.register_source("123456", {"name_es": "Producto nuevo"}, "hash-2", observed_at="2026-09-17")
    assert registry.get_current_approved_revision("123456", "name_es", "hash-1") is None


def test_registry_ingest_is_shadow_and_queues_field_units(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    result = registry.ingest_records([{"sku": "123456", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es": "20 g", "desc_es": "", "details_es": "Número del artículo: 123456"}], source_run_id="run-1", observed_at="2026-09-16")
    assert result["source_versions"] == 1
    assert result["units"] == 6
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM translation_queue").fetchone()[0] == 6
        assert db.execute("SELECT COUNT(*) FROM product_localizations").fetchone()[0] == 0


def test_registry_response_creates_revision_and_findings(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    response = TranslationResponse({"name": "耳机"}, "fake", "fixture", "h", "rq", "rs", "call")
    result = registry.record_response(official_sku="123456", source_fields={"name_es": "Auriculares"}, source_hash_value="h", observed_at="2026-09-16", source_run_id="r1", response=response, qa={"status": "PASS", "findings": []})
    assert result["revision_ids"]
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM translation_revisions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM translation_provider_calls").fetchone()[0] == 1


def test_qwen_payload_has_single_user_message_and_translation_options(monkeypatch):
    provider = QwenMTProvider("https://example.test/compatible-mode/v1")
    request = TranslationRequest("123456", {"name_es": "Producto"}, ("name",), "source-hash", terms=({"source": "Producto", "target": "商品"},))
    payload = provider._payload(request)
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert "system" not in {m["role"] for m in payload["messages"]}
    assert payload["translation_options"]["terms"]


def test_qwen_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    provider = QwenMTProvider("https://example.test/compatible-mode/v1")
    request = TranslationRequest("123456", {"name_es": "Producto"}, ("name",), "source-hash")
    with pytest.raises(ProviderError, match="QWEN_API_KEY_MISSING"):
        provider.translate(request)


def test_qwen_legacy_localization_bridge(monkeypatch):
    def fake_translate(self, request):
        return TranslationResponse({"name": "耳机"}, "qwen_mt", "qwen-mt-flash", request.source_hash, "rq", "rs", "id")
    monkeypatch.setattr("action_tracker.localization.ai.QwenMTProvider.translate", fake_translate)
    source = SourceFacts.from_record({"sku": "123456", "name_es": "Auriculares"})
    result = QwenMTCompatibleProvider("https://example.test/compatible-mode/v1", api_key_env="KEY").complete(source, ("name",))
    assert result["fields"] == {"name": "耳机"}
    assert result["source_hash"]


def test_normalization_is_mechanical_and_preserves_duplicate_details():
    assert normalize_source_text(" Material:: Plástico&nbsp; ") == "Material:: Plástico"
    fields = parse_detail_fields("Potencia:: 3.6; Potencia: 3.6 vatio; Número del artículo: 123")
    assert fields == [("Potencia", "3.6"), ("Potencia", "3.6 vatio"), ("Número del artículo", "123")]
    assert format_detail_fields(fields) == "Potencia: 3.6; Potencia: 3.6 vatio; Número del artículo: 123"


def test_migration_preview_separates_eligible_rejected_and_conflicts(tmp_path: Path):
    source = tmp_path / "tm.csv"
    source.write_text("sku,field_name,source_hash,target_text,status\n1,name,h1,商品,APPROVE\n1,name,h1,产品,APPROVE\n2,name,,商品,APPROVE\n3,name,h3,,PENDING\n4,name,h4,物品,APPROVE\n", encoding="utf-8")
    result = build_migration_preview([source], tmp_path / "preview")
    assert result["eligible_count"] == 1
    assert result["conflict_count"] == 2
    assert result["rejected_count"] == 2
    assert result["production_writes"] is False
    manifest = json.loads((tmp_path / "preview" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_hash"]


def test_migration_preview_expands_owner_gold(tmp_path: Path):
    source = tmp_path / "gold.jsonl"
    source.write_text(json.dumps({"sku": "123456", "source_hash": "h", "source_run_id": "r", "source": {"name": "Producto", "spec": "20 g"}, "proposed_zh": {"name": "商品", "spec": "20g"}, "owner_disposition": {"name": "ACCEPT_AS_IS", "spec": "REJECT"}}) + "\n", encoding="utf-8")
    result = build_migration_preview([source], tmp_path / "preview")
    assert result["eligible_count"] == 1
    rows = (tmp_path / "preview" / "eligible.csv").read_text(encoding="utf-8-sig")
    assert ",name," in rows
    assert ",spec," not in rows


def test_request_uses_canonical_source_hash_and_qc_is_fail_closed():
    record = {"sku": "123456", "name_es": "Auriculares USB-C", "cat1_es": "Multimedia", "cat2_es": "Audio", "spec_es": "20 mg", "desc_es": "", "details_es": "Número del artículo: 123456"}
    request = make_request(record, ("name", "spec"))
    assert request.source_hash
    result = guard_translation(SourceFacts.from_record(record), {"name": "USB-C耳机", "spec": "20mg"}, ("name", "spec"))
    assert result["status"] == "PASS"
    bad = guard_translation(SourceFacts.from_record(record), {"name": "Auriculares USB-C", "spec": ""}, ("name", "spec"))
    assert bad["status"] == "FAIL"
