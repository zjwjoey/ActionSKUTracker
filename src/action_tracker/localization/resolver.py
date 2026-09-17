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
from .policy import DISPLAY_POLICY_PROFILE, strip_forbidden_display_tokens


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
        self._registry_tables_available = False
        if self.db_path:
            try:
                from ..database.connection import connect
                with connect(self.db_path) as db:
                    tables = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self._registry_tables_available = "translation_memory_entries" in tables or "translation_revisions" in tables
            except Exception:
                self._registry_tables_available = False
        self.tm = TranslationMemoryRepository(self.db_path) if self.db_path and "translation_memory_entries" in locals().get("tables", set()) else None
        self.terminology = TerminologyRepository(self.db_path) if self.db_path and "terminology_entries" in locals().get("tables", set()) else None

    def _approved_revision(self, sku: str, field_name: str, source_hash_value: str) -> str | None:
        if self.registry is not None:
            row = self.registry.get_current_approved_revision(sku, field_name, source_hash_value)
            return str(row.get("target_text")) if row else None
        if not self.db_path or not self._registry_tables_available:
            return None
        from ..database.connection import connect
        try:
            with connect(self.db_path) as db:
                row = db.execute("""SELECT r.target_text FROM translation_revisions r
                JOIN translation_units u ON u.unit_id=r.unit_id
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.official_sku=? AND u.field_name IN (?,?)
                  AND r.source_hash=? AND r.qa_status='PASS'
                  AND u.freshness_status='FRESH'
                  AND r.review_status IN ('APPROVED','HUMAN_REVIEWED','LOCKED')
                  AND NOT EXISTS (SELECT 1 FROM translation_qa_findings f
                                  WHERE f.revision_id=r.revision_id AND f.status='OPEN'
                                    AND f.severity IN ('BLOCKER','ERROR','HIGH'))
                ORDER BY r.revision DESC LIMIT 1""", (sku, field_name, source_field(field_name), source_hash_value)).fetchone()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            return None
        return str(row[0]) if row else None

    def resolve_field(self, record: Mapping[str, Any], field_name: str, *, existing: Mapping[str, Any] | None = None,
                      allow_provider: bool = False, context_key: str | None = None,
                      product_type: str | None = None) -> Resolution:
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
                return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_exact", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash, "tm_match_type": match.match_type, "normalization_version": match.normalization_version, "context_key": match.context_key})
            match = self.tm.normalized_exact(source_text, field_name=field_name, context_key=context_key)
            if match:
                return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_normalized_exact", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash, "tm_match_type": match.match_type, "normalization_version": match.normalization_version, "context_key": match.context_key})
            if context_key:
                match = self.tm.exact(source_text, field_name=field_name, context_key=None)
                if match:
                    return Resolution(source.sku, field_name, source_text, source_hash_value, match.target_text, "tm_context", "APPROVED", True, False, provenance={"tm_source_hash": match.source_hash, "tm_match_type": match.match_type, "normalization_version": match.normalization_version, "context_key": match.context_key})

        # Deterministic planning can resolve fixed categories and dictionary
        # hits.  It never turns a Spanish fallback into an approved Chinese
        # value.
        plan = self.engine.resolve(record, existing=existing)
        planned = plan.fields.get(f"{field_name}_zh")
        if planned and planned.value and planned.status in {"AUTO_READY", "READY", "APPROVED"} and planned.source not in {"missing", "fallback_es"}:
            return Resolution(source.sku, field_name, source_text, source_hash_value, planned.value, planned.source, "APPROVED", True, False, provenance={"policy_version": planned.policy_version})

        if allow_provider and self.provider and source_text:
            term_rows = list(self.terminology.as_qwen_options(
                source_text,
                field_name=field_name,
                cat1=source.cat1_es,
                cat2=source.cat2_es,
                product_type=product_type,
                context_key=context_key,
                limit=20,
            ) if self.terminology else ())
            # The dedicated Qwen-MT endpoint does not accept our generic
            # system prompt.  Put reviewed semantic facts on its terms list
            # instead, so facts such as ``gomas -> 橡皮筋`` cannot be silently
            # dropped by the provider.  Brand/IP spans are deliberately sent
            # source-to-source and removed from Chinese names afterwards.
            display_tokens: list[str] = []
            semantic_types = {"PRODUCT_TYPE", "FUNCTION", "MATERIAL", "COMPATIBILITY", "CARE", "NUTRITION", "VARIANT", "DETAIL_KEY"}
            term_by_source = {
                str(item.get("source") or item.get("source_term") or "").strip().casefold(): dict(item)
                for item in term_rows
                if str(item.get("source") or item.get("source_term") or "").strip()
            }
            for fact in getattr(plan, "semantic_facts", ()):
                token = str(fact.source_text or "").strip()
                if not token:
                    continue
                if fact.semantic_type in {"BRAND", "IP_CHARACTER"}:
                    if field_name == "name":
                        display_tokens.append(token)
                    target = token
                elif fact.semantic_type in semantic_types:
                    target = str(fact.canonical_value or fact.value or "").strip()
                    if not target or target.casefold() == token.casefold():
                        continue
                else:
                    continue
                term_by_source.setdefault(token.casefold(), {"source": token, "target": target})
            terms = tuple(term_by_source.values())
            req = TranslationRequest(source.sku, {field_name: source_text}, (field_name,), source_hash_value, terms=terms, domain=product_type or "e-commerce")
            response = self.provider.translate(req)
            value = str(response.fields.get(field_name) or "")
            raw_value = value
            if field_name == "name" and display_tokens:
                value = strip_forbidden_display_tokens(value, display_tokens)
            if value:
                return Resolution(source.sku, field_name, source_text, source_hash_value, value, "qwen_mt", "PENDING", False, False, provenance={
                    "resolution_source": "QWEN_MT", "provider": response.provider,
                    "model": response.model, "request_id": response.request_id,
                    "request_hash": response.request_hash,
                    "response_hash": response.response_hash,
                    "usage": dict(response.usage or {}),
                    "retry_count": int((response.usage or {}).get("retry_count", 0) or 0),
                    "request_count": int((response.usage or {}).get("request_count", 1) or 1),
                    "terminology": [dict(term) for term in terms],
                    "display_policy_profile": DISPLAY_POLICY_PROFILE,
                    "provider_raw_value": raw_value if raw_value != value else "",
                })

        return Resolution(source.sku, field_name, source_text, source_hash_value, "", "missing", "PENDING", False, bool(source_text), ("NO_APPROVED_RESOLUTION",))

    def resolve(self, record: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None,
                allow_provider: bool = False, context_key: str | None = None, product_type: str | None = None) -> dict[str, Resolution]:
        return {field_name: self.resolve_field(record, field_name, existing=existing, allow_provider=allow_provider, context_key=context_key, product_type=product_type) for field_name in CANONICAL_AI_FIELDS}
