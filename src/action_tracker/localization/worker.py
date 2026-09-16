"""Field-level translation queue consumer.

The daily collector only registers immutable source versions and enqueues
unresolved fields.  This worker is the explicit bridge from that queue to the
single resolver: it claims atomically, resolves one field, records an
append-only registry revision, and then completes (or retries) the queue row.
It never writes ``product_localizations`` or any other PRIMARY projection.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import SourceFacts, source_hash
from .qa import guard_translation
from .repair import repair_field
from .resolver import TranslationResolver
from .registry.repository import LocalizationRegistry
from .providers.base import ProviderError
from .protection.tokens import ProtectedTokenError


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
        if source_hash(record) != str(item["source_hash"]):
            raise ValueError("QUEUE_SOURCE_HASH_MISMATCH")
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

    def _record_provider_failure(self, exc: BaseException, item: Mapping[str, Any]) -> None:
        """Persist a real provider attempt when the provider exposed identity."""
        request_hash = getattr(exc, "request_hash", None)
        if not request_hash:
            # Missing API keys and local validation failures happen before an
            # HTTP request; there is no provider call to record.
            return
        self.registry.record_provider_call(
            provider=str(getattr(exc, "provider", None) or getattr(self.resolver.provider, "provider", "unknown")),
            model=str(getattr(exc, "model", None) or getattr(self.resolver.provider, "model", "unknown")),
            request_hash=str(request_hash),
            response_hash=getattr(exc, "response_hash", None),
            source_hash=str(item.get("source_hash") or ""),
            status="FAILED",
            usage=getattr(exc, "usage", {}) or {},
            request_id=getattr(exc, "request_id", None),
            latency_ms=getattr(exc, "latency_ms", None),
            retry_count=int(getattr(exc, "retry_count", 0) or 0),
            error_code=str(getattr(exc, "code", type(exc).__name__)),
        )

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
                    self.registry.block_queue(queue_id, "NO_RESOLUTION")
                    blocked += 1
                    continue
                terminology = tuple(resolution.provenance.get("terminology") or ())
                qa = guard_translation(SourceFacts.from_record(record), {field_name: resolution.value}, (field_name,), terminology=terminology)
                repair_source = resolution.source
                if qa["status"] != "PASS":
                    repaired = repair_field(
                        record, field_name, resolution.value,
                        repair_reason="TRANSLATION_QUEUE_QA_REPAIR",
                        provider=getattr(self.resolver, "provider", None),
                        terminology=terminology,
                    )
                    resolution_value = repaired.value
                    qa = repaired.qa
                    repair_source = repaired.repair_source
                    if repaired.provenance:
                        resolution_provenance = dict(repaired.provenance)
                    else:
                        resolution_provenance = dict(resolution.provenance or {})
                else:
                    resolution_value = resolution.value
                    resolution_provenance = dict(resolution.provenance or {})
                if qa["status"] != "PASS":
                    blocking = [f for f in qa.get("findings", []) if str(f.get("severity") or "").upper() in {"BLOCKER", "ERROR"}]
                    if blocking:
                        self.registry.block_queue(queue_id, ";".join(str(f.get("rule_id") or "QA_FAILURE") for f in blocking))
                        blocked += 1
                    else:
                        self.registry.fail_queue(queue_id, "QA_RETRY_REQUIRED", retry=True)
                        retried += 1
                    continue
                provider_call_id = None
                provenance = resolution_provenance
                provenance["resolution_source"] = str(repair_source).upper()
                provider_name = str(provenance.get("provider") or repair_source)
                model = provenance.get("model")
                request_hash = provenance.get("request_hash")
                response_hash = provenance.get("response_hash")
                request_id = provenance.get("request_id")
                if str(repair_source).lower() == "qwen_mt" and request_hash:
                    provider_call_id = self.registry.record_provider_call(
                        provider=provider_name, model=str(model or provider_name),
                        request_hash=str(request_hash), response_hash=str(response_hash or ""),
                        source_hash=str(item["source_hash"]), status="COMPLETED",
                        usage=provenance.get("usage") or {}, request_id=request_id,
                        retry_count=int(provenance.get("retry_count", 0) or 0),
                    )
                self.registry.record_revision_for_sku(
                    str(item["official_sku"]), field_name, resolution_value,
                    source_hash=str(item["source_hash"]), provider=provider_name,
                    model=str(model) if model else None,
                    request_hash=str(request_hash) if request_hash else None,
                    response_hash=str(response_hash) if response_hash else None,
                    request_id=str(request_id) if request_id else None,
                    provider_call_id=provider_call_id,
                    provenance={**provenance, "resolution_source": str(repair_source).upper()},
                    terminology_version=str(provenance.get("terminology_version") or "") or None,
                    repair_reason="TRANSLATION_QUEUE_WORKER", qa_status="PASS",
                )
                if self.registry.complete_queue(queue_id):
                    completed += 1
            except ProviderError as exc:
                self._record_provider_failure(exc, item)
                if "PROTECTED_TOKEN" in str(exc.code).upper():
                    self.registry.block_queue(queue_id, f"{exc.code}:{exc}")
                    blocked += 1
                    continue
                retry = self.registry.fail_queue(queue_id, f"{exc.code}:{exc}", retry=bool(exc.retryable))
                if exc.retryable and retry:
                    retried += 1
                else:
                    failed += 1
            except ProtectedTokenError as exc:
                self.registry.block_queue(queue_id, f"PROTECTED_TOKEN:{exc}")
                blocked += 1
            except (ValueError, KeyError) as exc:
                # Missing source, stale source and deterministic data/QA
                # blockers must not be retried forever.
                self.registry.block_queue(queue_id, f"{type(exc).__name__}:{exc}")
                blocked += 1
            except sqlite3.OperationalError as exc:
                retry = self.registry.fail_queue(queue_id, f"SQLITE_TRANSIENT:{exc}", retry=True)
                if retry:
                    retried += 1
                else:
                    failed += 1
            except Exception as exc:
                # Unknown program errors are terminal FAILED, not data RETRY.
                self.registry.fail_queue(queue_id, f"{type(exc).__name__}:{exc}", retry=False)
                failed += 1
        return QueueWorkerResult(len(claimed), completed, retried, failed, blocked)


def process_translation_queue(registry: LocalizationRegistry, resolver: TranslationResolver, *, limit: int = 50, worker_id: str = "localization-worker") -> dict[str, int]:
    return TranslationQueueWorker(registry, resolver).process_once(limit=limit, worker_id=worker_id).as_dict()
