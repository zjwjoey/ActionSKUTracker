from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.localization.contracts import source_hash
from action_tracker.localization.providers.base import FakeTranslationProvider, ProviderError
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.localization.worker import TranslationQueueWorker


def _record(sku="123456", desc="Paño de microfiber"):
    return {
        "sku": sku, "name_es": "Producto", "cat1_es": "Hogar",
        "cat2_es": "Cocina", "spec_es": "20 g", "desc_es": desc,
        "details_es": f"Número del artículo: {sku}",
    }


def _seed_product(registry, sku="123456"):
    with connect(registry.path) as conn:
        conn.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES(?,?,?)", (f"c-{sku}", sku, "ACTIVE"))


def test_worker_persists_provider_call_and_revision_provenance(tmp_path: Path):
    db = tmp_path / "provenance.sqlite"
    registry = LocalizationRegistry(db)
    record = _record(desc="Descripción clara")
    _seed_product(registry)
    registry.ingest_records([record], source_run_id="run-1", observed_at="2026-09-16")
    resolver = TranslationResolver(db_path=db, registry=registry, provider=FakeTranslationProvider({
        "name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "20g",
        "description": "清晰描述", "details": f"商品编号：{record['sku']}",
    }))
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1)
    assert result.completed == 1
    with connect(db) as conn:
        row = conn.execute("SELECT provider_call_id,provider,model,request_hash,response_hash,provenance_json FROM translation_revisions").fetchone()
        call = conn.execute("SELECT call_id,provider,model,request_hash,response_hash,status FROM translation_provider_calls").fetchone()
    assert row and call
    assert row[0] == call[0]
    assert tuple(row[1:5]) == tuple(call[1:5])
    assert '"resolution_source": "QWEN_MT"' in row[5]


class _FailureProvider:
    provider = "fixture_provider"
    model = "fixture-model"

    def __init__(self, code, retryable):
        self.code = code
        self.retryable = retryable

    def translate(self, request):
        exc = ProviderError(self.code, retryable=self.retryable,
                            provider=self.provider, model=self.model,
                            request_hash="real-request-hash", request_id="req-1")
        raise exc


def test_worker_terminal_provider_error_is_failed_and_not_requeued(tmp_path: Path):
    db = tmp_path / "terminal.sqlite"
    registry = LocalizationRegistry(db)
    record = _record(desc="Descripción clara")
    _seed_product(registry)
    registry.ingest_records([record], source_run_id="run-1", observed_at="2026-09-16")
    resolver = TranslationResolver(db_path=db, registry=registry, provider=_FailureProvider("HTTP_401", False))
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1)
    assert result.failed == 1 and result.retried == 0
    with connect(db) as conn:
        assert conn.execute("SELECT status FROM translation_queue").fetchone()[0] == "FAILED"
        row = conn.execute("SELECT status,error_code FROM translation_provider_calls").fetchone()
        assert (row[0], row[1]) == ("FAILED", "HTTP_401")


def test_worker_protected_token_error_is_blocked(tmp_path: Path):
    db = tmp_path / "blocked.sqlite"
    registry = LocalizationRegistry(db)
    record = _record(desc="Descripción clara")
    _seed_product(registry)
    registry.ingest_records([record], source_run_id="run-1", observed_at="2026-09-16")
    resolver = TranslationResolver(db_path=db, registry=registry, provider=_FailureProvider("QWEN_PROTECTED_TOKEN_MISMATCH", False))
    result = TranslationQueueWorker(registry, resolver).process_once(limit=1)
    assert result.blocked == 1
    with connect(db) as conn:
        assert conn.execute("SELECT status FROM translation_queue").fetchone()[0] == "BLOCKED"


def test_selected_terminology_is_reused_by_worker_qa(tmp_path: Path):
    db = tmp_path / "terminology.sqlite"
    registry = LocalizationRegistry(db)
    registry.add_term("microfiber", "超细纤维", approval_status="APPROVED", field_scope="description")
    record = _record()
    _seed_product(registry)
    registry.ingest_records([record], source_run_id="run-1", observed_at="2026-09-16")
    resolver = TranslationResolver(db_path=db, registry=registry, provider=FakeTranslationProvider({
        "name": "商品", "cat1": "家居布置", "cat2": "厨房", "spec": "20g",
        "description": "微纤维清洁布", "details": f"商品编号：{record['sku']}",
    }))
    result = TranslationQueueWorker(registry, resolver).process_once(limit=6)
    assert result.blocked >= 1
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM translation_queue WHERE status='BLOCKED' AND last_error LIKE '%TERMINOLOGY_VIOLATION%'").fetchone()[0] == 1
