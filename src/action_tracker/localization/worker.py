"""Field-level translation queue consumer.

The daily collector only registers immutable source versions and enqueues
unresolved fields.  This worker is the explicit bridge from that queue to the
single resolver: it claims atomically, resolves one field, records an
append-only registry revision, and then completes (or retries) the queue row.
It never writes ``product_localizations`` or any other PRIMARY projection.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .resolver import TranslationResolver
from .registry.repository import LocalizationRegistry


_SOURCE_TO_CANONICAL = {
    "name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2",
    "spec_es": "spec", "desc_es": "description", "details_es": "details",
}


@dataclass(frozen=True)
class QueueWorkerResult:
    claimed: int
    completed: int
    retried: int
    failed: int
    blocked: int

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "retried": self.retried,
            "failed": self.failed,
            "blocked": self.blocked,
            "production_writes": 0,
        }


class TranslationQueueWorker:
    def __init__(self, registry: LocalizationRegistry, resolver: TranslationResolver) -> None:
        self.registry = registry
        self.resolver = resolver

    def _source_record(self, item: Mapping[str, Any]) -> dict[str, Any]:
        from ..database.connection import connect

        with connect(self.registry.path) as db:
            row = db.execute(
                """SELECT source_fields_json FROM translation_source_versions
                   WHERE official_sku=? AND source_hash=? ORDER BY created_at DESC LIMIT 1""",
                (str(item["official_sku"]), str(item["source_hash"])),
            ).fetchone()
        if not row:
            raise ValueError("QUEUE_SOURCE_VERSION_MISSING")
        try:
            fields = json.loads(row[0] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("QUEUE_SOURCE_FIELDS_INVALID") from exc
        if not isinstance(fields, dict):
            raise ValueError("QUEUE_SOURCE_FIELDS_INVALID")
        record: dict[str, Any] = {"sku": str(item["official_sku"])}
        record.update(fields)
        return record

    @staticmethod
    def _requested_field(value: Any) -> str:
        raw = str(value or "").strip()
        if raw.startswith("["):
            try:
                values = json.loads(raw)
                raw = str(values[0] if isinstance(values, list) and values else "")
            except json.JSONDecodeError:
                pass
        return _SOURCE_TO_CANONICAL.get(raw, raw)

    def process_once(self, *, limit: int = 50, worker_id: str = "localization-worker") -> QueueWorkerResult:
        claimed = self.registry.claim_queue(limit=limit, worker_id=worker_id)
        completed = retried = failed = blocked = 0
        for item in claimed:
            queue_id = str(item["queue_id"])
            try:
                record = self._source_record(item)
                field_name = self._requested_field(item.get("requested_fields"))
                resolution = self.resolver.resolve_field(record, field_name, allow_provider=True)
                if not resolution.value:
                    self.registry.fail_queue(queue_id, "NO_RESOLUTION", retry=False)
                    blocked += 1
                    continue
                qa_status = "PASS" if resolution.approved else "PENDING"
                self.registry.record_revision_for_sku(
                    str(item["official_sku"]), field_name, resolution.value,
                    source_hash=str(item["source_hash"]), provider=resolution.source,
                    repair_reason="TRANSLATION_QUEUE_WORKER", qa_status=qa_status,
                )
                if self.registry.complete_queue(queue_id):
                    completed += 1
            except Exception as exc:  # provider/network/QA errors are retryable by policy
                retry = self.registry.fail_queue(queue_id, f"{type(exc).__name__}:{exc}", retry=True)
                if retry:
                    retried += 1
                else:
                    failed += 1
        return QueueWorkerResult(len(claimed), completed, retried, failed, blocked)


def process_translation_queue(registry: LocalizationRegistry, resolver: TranslationResolver, *, limit: int = 50, worker_id: str = "localization-worker") -> dict[str, int]:
    return TranslationQueueWorker(registry, resolver).process_once(limit=limit, worker_id=worker_id).as_dict()

