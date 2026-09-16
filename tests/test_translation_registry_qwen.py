from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
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
from action_tracker.localization.contracts import SourceFacts, source_hash
from action_tracker.localization.ai import QwenMTCompatibleProvider, provider_from_config, provider_health
from action_tracker.localization.providers.base import TranslationResponse
from action_tracker.localization.normalization import normalize_source_text, parse_detail_fields, format_detail_fields
from action_tracker.localization.registry.migration import build_migration_preview
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.localization.worker import TranslationQueueWorker
from action_tracker.localization.runtime_builder import build_translation_runtime
from action_tracker.database.schema import migrate_v2
from action_tracker.knowledge.storage import KnowledgeStore
from action_tracker.exporting.dictionary_join import build_zh_rows_from_localized_source


def test_protected_tokens_round_trip_and_reject_missing():
    protected = protect_text("Auriculares USB-C 20 mg, SKU 3211585, https://example.test/x")
    assert "USB-C" not in protected.text
    assert restore_text(protected.text, protected).endswith("https://example.test/x")
    with pytest.raises(ProtectedTokenError):
        restore_text(protected.text.replace("[[PROTECTED_0000]]", ""), protected)


def test_protected_tokens_cover_battery_and_certification_facts():
    protected = protect_text("1000 mAh 18 V 9 W 50 Hz 500 ml 1.5 L 20 kg 80 x 50 cm 5-10 mm €2.99 15% USB-C E27 IP44 URL https://example.test/a FSC CE")
    assert "CAPACITY" in protected.token_types
    assert "CERTIFICATION" in protected.token_types
    assert "TECH" in protected.token_types
    assert restore_text(protected.text, protected) == "1000 mAh 18 V 9 W 50 Hz 500 ml 1.5 L 20 kg 80 x 50 cm 5-10 mm €2.99 15% USB-C E27 IP44 URL https://example.test/a FSC CE"


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
        # Empty official source fields are retained as evidence but do not
        # create translation work items.
        assert db.execute("SELECT COUNT(*) FROM translation_queue").fetchone()[0] == 5
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
    assert payload["translation_options"]["domains"]
    assert set(payload["translation_options"]["terms"][0]) == {"source", "target"}


def test_registry_reuses_unchanged_field_and_queues_only_changed_field(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    record1 = {"sku": "123456", "name_es": "Producto", "cat1_es": "", "cat2_es": "", "spec_es": "10 g", "desc_es": "", "details_es": ""}
    record2 = {**record1, "spec_es": "20 g"}
    from action_tracker.localization.contracts import source_hash
    first = registry.register_source("123456", {key: record1[key] for key in record1 if key != "sku"}, source_hash(record1), observed_at="2026-09-16")
    with connect(db_path) as db:
        units = {str(row["field_name"]): str(row["unit_id"]) for row in db.execute("SELECT unit_id,field_name FROM translation_units WHERE source_version_id=?", (first,)).fetchall()}
    name_revision = registry.record_revision(unit_id=units["name_es"], target_text="商品", provider="fake", model="fixture", request_hash="rq", response_hash="rs", source_hash="hash-1", qa_status="PASS", review_status="HUMAN_REVIEWED")
    assert registry.approve_revision(name_revision, actor="human:test") is True
    second = registry.register_source("123456", {key: record2[key] for key in record2 if key != "sku"}, source_hash(record2), observed_at="2026-09-17")
    with connect(db_path) as db:
        fresh = db.execute("""SELECT u.field_name,r.target_text,r.review_status,u.freshness_status
            FROM translation_units u LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id
            WHERE u.source_version_id=? ORDER BY u.field_name""", (second,)).fetchall()
    by_field = {str(row[0]): tuple(row[1:]) for row in fresh}
    assert by_field["name_es"] == ("商品", "APPROVED", "FRESH")
    assert by_field["spec_es"] == (None, None, "FRESH")
    result = registry.ingest_records([record2], source_run_id="r2", observed_at="2026-09-17")
    assert result["queue_insert_attempts"] == 1
    with connect(db_path) as db:
        queued = db.execute("SELECT requested_fields FROM translation_queue WHERE source_hash=?", (source_hash(record2),)).fetchall()
    # Only the changed spec should be pending in the subsequent source version.
    assert len(queued) == 1


@pytest.mark.parametrize(
    ("changes", "expected_queue"),
    [
        ({"spec_es": "200 ml"}, 1),
        ({"desc_es": "Descripción actualizada"}, 1),
        ({"cat2_es": "Baño"}, 1),
        ({"spec_es": "200 ml", "desc_es": "Descripción actualizada"}, 2),
        ({}, 0),
    ],
)
def test_field_incremental_diff_matrix(tmp_path: Path, changes: dict[str, str], expected_queue: int):
    db_path = tmp_path / "incremental-matrix.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    first = {"sku": "123456", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es": "100 ml", "desc_es": "Descripción", "details_es": "Número del artículo: 123456"}
    registry.ingest_records([first], source_run_id="run-1", observed_at="2026-09-16")
    first_hash = source_hash(first)
    with connect(db_path) as db:
        units = db.execute("SELECT unit_id,field_name FROM translation_units WHERE source_version_id=(SELECT source_version_id FROM translation_source_versions WHERE official_sku='123456')").fetchall()
    targets = {"name_es": "商品", "cat1_es": "家居", "cat2_es": "厨房", "spec_es": "100ml", "desc_es": "描述", "details_es": "商品编号：123456"}
    for row in units:
        registry.record_revision(unit_id=str(row[0]), target_text=targets[str(row[1])], provider="fake", model="fixture", request_hash="r", response_hash="s", source_hash=first_hash, qa_status="PASS", review_status="HUMAN_REVIEWED")
    with connect(db_path) as db:
        revision_ids = [str(row[0]) for row in db.execute("SELECT current_revision_id FROM translation_units WHERE source_version_id=(SELECT source_version_id FROM translation_source_versions WHERE official_sku='123456')").fetchall()]
    for revision_id in revision_ids:
        assert registry.approve_revision(revision_id, actor="human:matrix") is True
    second = {**first, **changes}
    result = registry.ingest_records([second], source_run_id="run-2", observed_at="2026-09-17")
    assert result["queue_insert_attempts"] == expected_queue


def test_new_sku_queues_all_nonempty_fields(tmp_path: Path):
    db_path = tmp_path / "incremental-new-sku.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c2','654321','ACTIVE')")
    record = {"sku": "654321", "name_es": "Producto nuevo", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es": "1 unidad", "desc_es": "Descripción", "details_es": "Número del artículo: 654321"}
    result = registry.ingest_records([record], source_run_id="run-new", observed_at="2026-09-17")
    assert result["source_versions"] == 1
    assert result["queue_insert_attempts"] == 6


def test_translation_queue_worker_claims_resolves_and_completes_shadow_only(tmp_path: Path):
    db_path = tmp_path / "registry.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    record = {"sku": "123456", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es": "20 g", "desc_es": "Descripción", "details_es": "Número del artículo: 123456"}
    registry.ingest_records([record], source_run_id="run-1", observed_at="2026-09-16")
    from action_tracker.localization.providers.base import FakeTranslationProvider
    resolver = TranslationResolver(db_path=db_path, registry=registry, provider=FakeTranslationProvider({
        "name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "20g",
        "description": "描述", "details": "商品编号: 123456",
    }))
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1, worker_id="test-worker")
    assert result.claimed == 1
    assert result.completed == 1
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM product_localizations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM translation_revisions").fetchone()[0] == 1
        assert db.execute("SELECT status FROM translation_queue WHERE status='COMPLETED'").fetchone()[0] == "COMPLETED"


def test_runtime_builder_shares_registry_resolver_and_worker(tmp_path: Path):
    cfg = {
        "project_root": tmp_path,
        "storage": {"db_path": str(tmp_path / "runtime.sqlite")},
        "paths": {"dictionary": tmp_path / "dictionary"},
        "localization": {"policy_version": "TEST", "ai": {"enabled": False}},
    }
    runtime = build_translation_runtime(cfg)
    assert runtime.resolver.registry is runtime.registry
    assert runtime.worker.registry is runtime.registry
    assert getattr(runtime.provider, "provider", "") == "disabled"


def test_tm_normalized_lookup_uses_indexed_hash(tmp_path: Path):
    registry = LocalizationRegistry(tmp_path / "terms.sqlite")
    registry.add_tm("  LED   light ", "LED灯", field_name="name", approval_status="APPROVED")
    from action_tracker.localization.memory.repository import TranslationMemoryRepository
    match = TranslationMemoryRepository(tmp_path / "terms.sqlite").normalized_exact("LED light", field_name="name")
    assert match and match.target_text == "LED灯"
    with connect(tmp_path / "terms.sqlite") as db:
        assert db.execute("SELECT normalized_source_hash FROM translation_memory_entries").fetchone()[0]


def test_qwen_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    provider = QwenMTProvider("https://example.test/compatible-mode/v1")
    request = TranslationRequest("123456", {"name_es": "Producto"}, ("name",), "source-hash")
    with pytest.raises(ProviderError, match="QWEN_API_KEY_MISSING"):
        provider.translate(request)


def test_qwen_endpoint_uses_runtime_environment_override(monkeypatch):
    monkeypatch.setenv("QWEN_MT_BASE_URL", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    provider = provider_from_config({"enabled": True, "provider": "qwen_mt", "base_url": None})
    assert provider.base_url == "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def test_qwen_provider_health_fails_fast_with_explicit_environment_errors(monkeypatch):
    monkeypatch.delenv("QWEN_MT_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    provider = provider_from_config({"enabled": True, "provider": "qwen_mt", "base_url": None})
    assert provider_health(provider)["error"] == "QWEN_BASE_URL_MISSING"

    provider = provider_from_config({
        "enabled": True,
        "provider": "qwen_mt",
        "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    })
    assert provider_health(provider)["error"] == "QWEN_API_KEY_MISSING"


def test_qwen_provider_health_does_not_assume_models_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    provider = provider_from_config({
        "enabled": True,
        "provider": "qwen_mt",
        "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    })
    health = provider_health(provider)
    assert health["status"] == "PASS"
    assert health["health_check"] == "CONFIG_ONLY_QWEN_MT"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_qwen_client_errors_are_not_retried(monkeypatch, status):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-key")
    attempts = []

    def fail(request, timeout):
        attempts.append(status)
        raise urllib.error.HTTPError(request.full_url, status, "bad", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    provider = QwenMTProvider("https://example.test/compatible-mode/v1", max_retries=3, backoff_seconds=0)
    request = TranslationRequest("123456", {"name_es": "Producto"}, ("name",), "source-hash")
    with pytest.raises(ProviderError, match=f"QWEN_HTTP_{status}"):
        provider.translate(request)
    assert attempts == [status]


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


def test_typed_qa_detects_model_unit_and_terminology_violations():
    source = SourceFacts.from_record({"sku": "123456", "name_es": "Auriculares SL-300", "spec_es": "1000 mAh 18 V", "desc_es": "", "details_es": ""})
    result = guard_translation(source, {"name": "耳机 SL-301", "spec": "1000 Ah 18 V"}, ("name", "spec"), terminology=({"source": "Auriculares", "target": "耳机"},))
    rule_ids = {item["rule_id"] for item in result["findings"]}
    assert "MODEL_CHANGED" in rule_ids
    assert "UNIT_DROPPED" in rule_ids or "PROTECTED_TOKEN_CHANGED" in rule_ids


def test_translation_registry_to_primary_and_export_e2e(tmp_path: Path):
    """Exercise the complete fail-closed path without a live provider."""
    db_path = tmp_path / "e2e-primary.sqlite"
    migrate_v2(db_path, role="PRIMARY")
    record = {
        "sku": "123456",
        "name_es": "Auriculares",
        "cat1_es": "Hogar",
        "cat2_es": "Cocina",
        "spec_es": "20 mg",
        "desc_es": "Sonido claro",
        "details_es": "Número del artículo: 123456",
    }
    source_value = source_hash(record)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status,current_price,product_url,image_url) VALUES('c1','123456','CURRENT',4.99,'https://example.test/123456','https://example.test/123456.jpg')")
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("123456", "es", record["name_es"], record["cat1_es"], record["cat2_es"], record["spec_es"], record["desc_es"], record["details_es"], "OFFICIAL_FACT", "VERIFIED", "now", source_value))
        db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("123456", "zh", "", "", "", "", "", "", "", "PENDING", "now", source_value))
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('base','2026-09-16','COMMITTED','PASS',0,'now','now','2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('BASE','base','h','2.0.0','now','now','COMMITTED')")

    registry = LocalizationRegistry(db_path, role="PRIMARY")
    result = registry.ingest_records([record], source_run_id="daily-1", observed_at="2026-09-16")
    assert result["queue_insert_attempts"] == 6
    from action_tracker.localization.providers.base import FakeTranslationProvider
    resolver = TranslationResolver(
        db_path=db_path,
        registry=registry,
        provider=FakeTranslationProvider({
            "name": "耳机", "cat1": "家居布置", "cat2": "厨房",
            "spec": "20mg", "description": "声音清晰", "details": "商品编号：123456",
        }),
    )
    worker_result = TranslationQueueWorker(registry, resolver).process_once(limit=10, worker_id="e2e-worker")
    assert worker_result.completed == 6
    with connect(db_path) as db:
        revision_ids = [str(row[0]) for row in db.execute("SELECT u.current_revision_id FROM translation_units u JOIN translation_source_versions s ON s.source_version_id=u.source_version_id WHERE s.official_sku='123456'").fetchall()]
    assert len(revision_ids) == 6
    for revision_id in revision_ids:
        assert registry.approve_revision(revision_id, actor="human:e2e") is True

    store = KnowledgeStore(db_path, role="PRIMARY")
    preview = store.preview_approved_registry_apply()
    assert len(preview) == 6
    assert {row["decision"] for row in preview} == {"WOULD_UPDATE"}
    staged = store.stage_approved_registry_patches(expected_base_commit_id="BASE", actor="human:e2e")
    assert staged["staged_fields"] == 6
    from action_tracker.database.production import apply_approved_localization_patches
    applied = apply_approved_localization_patches(db_path, patch_ids=staged["patch_ids"], expected_base_commit_id="BASE", actor="service:translation-apply")
    assert applied["applied_fields"] == 6

    with connect(db_path) as db:
        zh = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku='123456' AND language='zh'").fetchone()
        es = db.execute("SELECT name,spec,details FROM product_localizations WHERE official_sku='123456' AND language='es'").fetchone()
    assert tuple(zh) == ("耳机", "家居布置", "厨房", "20mg", "声音清晰", "商品编号：123456")
    assert tuple(es) == (record["name_es"], record["spec_es"], record["details_es"])
    export_records = [
        {
            "sku": "123456", "name_es": record["name_es"], "cat1_es": record["cat1_es"], "cat2_es": record["cat2_es"],
            "spec_es": record["spec_es"], "desc_es": record["desc_es"], "details_es": record["details_es"],
            "name_zh": zh[0], "cat1_zh": zh[1], "cat2_zh": zh[2], "spec_zh": zh[3],
            "desc_zh": zh[4], "details_zh": zh[5], "zh_review_status": "APPROVED", "zh_freshness_status": "CURRENT",
            "zh_source_hash": source_value, "current_price": 4.99, "unit_price": "1 kg", "product_url": "https://example.test/123456", "image_url": "https://example.test/123456.jpg",
        }
    ]
    rows, _ = build_zh_rows_from_localized_source(export_records)
    assert rows[0]["标题"] == "耳机"
    assert rows[0]["产品详情"] == "商品编号：123456"


def test_source_change_e2e_reuses_name_and_queues_only_spec(tmp_path: Path):
    db_path = tmp_path / "source-change.sqlite"
    registry = LocalizationRegistry(db_path)
    with connect(db_path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES('c1','123456','ACTIVE')")
    run1 = {"sku": "123456", "name_es": "Producto", "cat1_es": "Hogar", "cat2_es": "Cocina", "spec_es": "100 ml", "desc_es": "Descripción", "details_es": "Número del artículo: 123456"}
    first = registry.ingest_records([run1], source_run_id="run-1", observed_at="2026-09-16")
    assert first["queue_insert_attempts"] == 6
    with connect(db_path) as db:
        units = {str(row["field_name"]): str(row["unit_id"]) for row in db.execute("SELECT field_name,unit_id FROM translation_units WHERE source_version_id=(SELECT source_version_id FROM translation_source_versions WHERE official_sku='123456')").fetchall()}
    for field_name, unit_id in units.items():
        registry.record_revision(unit_id=unit_id, target_text={"name_es": "商品", "cat1_es": "家居", "cat2_es": "厨房", "spec_es": "100ml", "desc_es": "描述", "details_es": "商品编号：123456"}[field_name], provider="fake", model="fixture", request_hash="r", response_hash="s", source_hash=source_hash(run1), qa_status="PASS", review_status="HUMAN_REVIEWED")
    with connect(db_path) as db:
        revs = [str(row[0]) for row in db.execute("SELECT current_revision_id FROM translation_units WHERE source_version_id=(SELECT source_version_id FROM translation_source_versions WHERE official_sku='123456')").fetchall()]
    for rev in revs:
        assert registry.approve_revision(rev, actor="human:source-change") is True
    run2 = {**run1, "spec_es": "200 ml"}
    result = registry.ingest_records([run2], source_run_id="run-2", observed_at="2026-09-17")
    assert result["queue_insert_attempts"] == 1
    second_hash = source_hash(run2)
    with connect(db_path) as db:
        rows = db.execute("SELECT u.field_name,u.freshness_status,r.target_text,r.review_status FROM translation_units u LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id WHERE u.source_version_id=(SELECT source_version_id FROM translation_source_versions WHERE official_sku='123456' AND source_hash=? ORDER BY created_at DESC LIMIT 1)", (second_hash,)).fetchall()
    by_field = {str(row[0]): tuple(row[1:]) for row in rows}
    assert by_field["name_es"] == ("FRESH", "商品", "APPROVED")
    assert by_field["spec_es"] == ("FRESH", None, None)
