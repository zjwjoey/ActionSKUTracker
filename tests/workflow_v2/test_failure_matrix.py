"""CI-safe Workflow V2 translation failure matrix.

All cases use an isolated SQLite registry and a deterministic provider; no
Action site, API key, production DB or network is reachable from this module.
"""
from __future__ import annotations

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.localization.contracts import SourceFacts
from action_tracker.localization.providers.base import ProviderError, TranslationRequest
from action_tracker.localization.qa import guard_translation
from action_tracker.localization.registry.repository import LocalizationRegistry
from action_tracker.localization.resolver import TranslationResolver
from action_tracker.localization.worker import TranslationQueueWorker


class ErrorProvider:
    provider = "fake-error"
    model = "fixture"

    def __init__(self, code: str, retryable: bool):
        self.code = code
        self.retryable = retryable

    def translate(self, request: TranslationRequest):
        raise ProviderError(self.code, self.code, retryable=self.retryable, provider=self.provider, model=self.model)


def _worker(tmp_path, provider):
    db = tmp_path / "failure.sqlite"
    migrate_v2(db, role="SHADOW")
    record = {"sku": "100", "canonical_id": "ACT100", "name_es": "Mesa", "cat1_es": "Hogar", "spec_es": "10 cm", "desc_es": "Mesa", "details_es": "Número: 100"}
    with connect(db) as handle:
        handle.execute("INSERT INTO products(canonical_id,official_sku,status) VALUES(?,?,?)", ("ACT100", "100", "CURRENT"))
    registry = LocalizationRegistry(db, role="SHADOW")
    registry.ingest_records([record], source_run_id="failure-run", observed_at="2026-10-04")
    resolver = TranslationResolver(db_path=db, registry=registry, provider=provider)
    return TranslationQueueWorker(registry, resolver), registry


@pytest.mark.parametrize("code", ["QWEN_429", "QWEN_500", "QWEN_TIMEOUT"])
def test_qwen_retryable_failures_remain_retryable(tmp_path, code):
    worker, registry = _worker(tmp_path, ErrorProvider(code, True))
    result = worker.process_once(limit=1, worker_id="failure-matrix")
    assert result.retried == 1
    assert registry.queue_status().get("RETRY") == 1


@pytest.mark.parametrize("code", ["QWEN_400", "QWEN_401", "QWEN_403"])
def test_qwen_terminal_failures_do_not_retry_forever(tmp_path, code):
    worker, registry = _worker(tmp_path, ErrorProvider(code, False))
    result = worker.process_once(limit=1, worker_id="failure-matrix")
    assert result.failed == 1
    assert registry.queue_status().get("FAILED") == 1


@pytest.mark.parametrize(
    ("field", "candidate", "reason"),
    [("name", "", "empty"), ("spec", "11 cm", "hallucinated_number"), ("spec", "10 kg", "unit_loss"), ("name", "Mesa para hogar", "spanish_residue")],
)
def test_qwen_candidate_contract_is_fail_closed(source_row, field, candidate, reason):
    facts = SourceFacts.from_record(source_row)
    result = guard_translation(facts, {field: candidate}, (field,), production=False)
    assert result["status"] == "FAIL", reason
