"""Single field-level translation resolver.

The resolver is deliberately side-effect free.  It decides *where* a value
comes from; registry writes, approvals and PRIMARY Apply remain explicit
operations in their existing services.  This prevents a provider response or
an unapproved fallback from silently becoming production Chinese.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import CANONICAL_AI_FIELDS, SourceFacts, source_field
from .engine import LocalizationEngine
from .memory.repository import TranslationMemoryRepository
from .providers.base import TranslationProvider, TranslationRequest
from .registry.repository import LocalizationRegistry
from .terminology.repository import TerminologyRepository
from .hashes import value_hash


@dataclass(frozen=True)
class Resolution:
    sku: str
    field_name: str
    source_text: str
    source_hash: str
    value: str
    source: str
    status: str
    approved: bool = False
    needs_provider: bool = False
    review_reasons: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)


class TranslationResolver:
    """唯一的字段解析入口，按正式优先级逐级收敛。"""

    def __init__(self, *, db_path=None, registry: LocalizationRegistry | None = None,
                 provider: TranslationProvider | None = None,
                 engine: LocalizationEngine | None = None,
                 manual_locks: Mapping[tuple[str, str], Any] | None = None):
        self.db_path = db_path or (registry.path if registry else None)
        self.registry = registry
        self.provider = provider
        self.engine = engine or LocalizationEngine()
        self.manual_locks = dict(manual_locks or {})
        self.tm = TranslationMemoryRepository(self.db_path) if self.db_path else None
        self.terminology = TerminologyRepository(self.db_path) if self.db_path else None

    def _approved_revision(self, sku: str, field_name: str, source_hash_value: str) -> str | None:
        if not self.db_path:
            return None
        from ..database.connection import connect
        with connect(self.db_path) as db:
            row = db.execute("""SELECT r.target_text FROM translation_revisions r
                JOIN translation_units u ON u.unit_id=r.unit_id
                WHERE u.official_sku=? AND u.field_name IN (?,?)
                  AND r.source_hash=? AND r.qa_status='PASS'
                  AND r.review_status IN ('APPROVED','HUMAN_REVIEWED','LOCKED')
                ORDER BY r.revision DESC LIMIT 1""", (sku, field_name, source_field(field_name), source_hash_value)).fetchone()
        return str(row[0]) if row else None

    def resolve_field(self, record: Mapping[str, Any], field_name: str, *, existing: Mapping[str, Any] | None = None,
                      allow_provider: bool = False, context_key: str | None = None) -> Resolution:
        source = SourceFacts.from_record(record)
        if field_name not in CANONICAL_AI_FIELDS:
            raise ValueError(f"UNSUPPORTED_TRANSLATION_FIELD:{field_name}")
        source_text = str(getattr(source, source_field(field_name), "") or "")
        key = (source.sku, field_name)
        source_hash_value = source.source_hash

        manual = self.manual_locks.get(key)
        if manual is not None:
            value = str(manual.get("value") if isinstance(manual, Mapping) else manual)
            return Resolution(source.sku, field_name, source_text, source_hash_value, value, "manual_field_lock", "APPROVED", True, False, provenance={"value_hash": value_hash(value)})

        approved = self._approved_revision(source.sku, field_name, source_hash_value)
        if approved is not None:
            return Resolution(source.sku, field_name, source_text, source_hash_value, approved, "approved_revision", "APPROVED", True, False, provenance={"value_hash": value_hash(approved)})

        if self.tm:
            match = self.tm.exact(source_text, field_name=field_name, context_key=context_key)
            if match:
                return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_exact", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash})
            match = self.tm.normalized_exact(source_text, field_name=field_name, context_key=context_key)
            if match:
                return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_normalized_exact", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash})
            if context_key:
                match = self.tm.exact(source_text, field_name=field_name, context_key=None)
                if match:
                    return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_context", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash})

        # Deterministic planning can resolve fixed categories and dictionary
        # hits.  It never turns a Spanish fallback into an approved Chinese
        # value.
        plan = self.engine.resolve(record, existing=existing)
        planned = plan.fields.get(f"{field_name}_zh")
        if planned and planned.value and planned.status in {"AUTO_READY", "READY", "APPROVED"} and planned.source not in {"missing", "fallback_es"}:
            return Resolution(source.sku, field_name, source_text, source_hash_value, planned.value, planned.source, "APPROVED", True, False, provenance={"policy_version": planned.policy_version})

        if allow_provider and self.provider and source_text:
            req = TranslationRequest(source.sku, {field_name: source_text}, (field_name,), source_hash_value, terms=tuple(self.registry.approved_terms() if self.registry else ()))
            response = self.provider.translate(req)
            value = str(response.fields.get(field_name) or "")
            if value:
                return Resolution(source.sku, field_name, source_text, source_hash_value, value, "qwen_mt", "PENDING", False, False, provenance={"provider": response.provider, "model": response.model, "request_hash": response.request_hash, "response_hash": response.response_hash})

        return Resolution(source.sku, field_name, source_text, source_hash_value, "", "missing", "PENDING", False, bool(source_text), ("NO_APPROVED_RESOLUTION",))

    def resolve(self, record: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None,
                allow_provider: bool = False, context_key: str | None = None) -> dict[str, Resolution]:
        return {field_name: self.resolve_field(record, field_name, existing=existing, allow_provider=allow_provider, context_key=context_key) for field_name in CANONICAL_AI_FIELDS}

