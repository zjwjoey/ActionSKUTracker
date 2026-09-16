from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...database.connection import connect
from ...database.schema import migrate_v2
from ..hashes import SOURCE_HASH_CONTRACT_VERSION, canonical_json, value_hash
from ..contracts import SourceFacts, source_hash


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalizationRegistry:
    """Append-only registry for source versions, revisions, TM and terms.

    This registry is intentionally independent from production Apply.  Adding
    a source or model revision is safe; a caller must still use the existing
    field-level approval gate before changing the PRIMARY projection.
    """

    def __init__(self, path: Path, *, role: str = "SHADOW"):
        self.path = Path(path)
        migrate_v2(self.path, role=role)

    def register_source(self, official_sku: str, fields: Mapping[str, Any], source_hash: str, *, observed_at: str, source_run_id: str | None = None, hash_contract_version: str = SOURCE_HASH_CONTRACT_VERSION, raw_fields: Mapping[str, Any] | None = None, normalized_fields: Mapping[str, Any] | None = None, source_quality_status: str = "UNKNOWN") -> str:
        source_id = str(uuid.uuid4())
        payload = canonical_json(dict(fields))
        raw_payload = canonical_json(dict(raw_fields)) if raw_fields is not None else None
        normalized_payload = canonical_json(dict(normalized_fields or fields))
        raw_hash = value_hash(raw_payload) if raw_payload is not None else None
        normalized_hash = value_hash(normalized_payload)
        with connect(self.path) as db:
            row = db.execute("SELECT source_version_id FROM translation_source_versions WHERE official_sku=? AND source_hash=? AND hash_contract_version=?", (official_sku, source_hash, hash_contract_version)).fetchone()
            if row:
                return str(row[0])
            db.execute("INSERT INTO translation_source_versions(source_version_id,official_sku,source_hash,hash_contract_version,source_fields_json,raw_fact_hash,normalized_fact_hash,raw_fact_json,normalized_fact_json,source_quality_status,observed_at,source_run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (source_id, official_sku, source_hash, hash_contract_version, payload, raw_hash, normalized_hash, raw_payload, normalized_payload, source_quality_status, observed_at, source_run_id, _now()))
            for field_name, value in fields.items():
                if value is None:
                    continue
                unit_id = str(uuid.uuid4())
                text = str(value)
                db.execute("INSERT OR IGNORE INTO translation_units(unit_id,source_version_id,field_name,source_text,source_text_hash,target_language,status,freshness_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (unit_id, source_id, str(field_name), text, value_hash(text), "zh", "PENDING", "FRESH", _now(), _now()))
        return source_id

    def record_provider_call(self, *, provider: str, model: str, request_hash: str, response_hash: str | None, source_hash: str, status: str, usage: Mapping[str, Any] | None = None, error_code: str | None = None, request_id: str | None = None, latency_ms: int | None = None, retry_count: int = 0, cost_estimate: float | None = None, artifact_ref: str | None = None) -> str:
        call_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO translation_provider_calls(call_id,provider,model,request_hash,response_hash,source_hash,status,usage_json,request_id,latency_ms,retry_count,cost_estimate,artifact_ref,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (call_id, provider, model, request_hash, response_hash, source_hash, status, json.dumps(dict(usage or {}), ensure_ascii=False, sort_keys=True), request_id, latency_ms, retry_count, cost_estimate, artifact_ref, error_code, _now()))
        return call_id

    def record_revision(self, *, unit_id: str, target_text: str, provider: str, model: str, request_hash: str, response_hash: str, qa_status: str = "PENDING", review_status: str = "PENDING", request_id: str | None = None, policy_version: str | None = None, terminology_version: str | None = None, tm_version: str | None = None, source_hash: str | None = None, parent_revision_id: str | None = None) -> str:
        revision_id = str(uuid.uuid4())
        with connect(self.path) as db:
            row = db.execute("SELECT COALESCE(MAX(revision),0) FROM translation_revisions WHERE unit_id=?", (unit_id,)).fetchone()
            revision = int(row[0]) + 1
            db.execute("INSERT INTO translation_revisions(revision_id,unit_id,revision,target_text,target_hash,provider,model,request_hash,response_hash,request_id,policy_version,terminology_version,tm_version,source_hash,parent_revision_id,qa_status,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (revision_id, unit_id, revision, target_text, value_hash(target_text), provider, model, request_hash, response_hash, request_id, policy_version, terminology_version, tm_version, source_hash, parent_revision_id, qa_status, review_status, _now()))
            db.execute("UPDATE translation_units SET current_revision_id=?,updated_at=? WHERE unit_id=?", (revision_id, _now(), unit_id))
        return revision_id

    def record_finding(self, revision_id: str, *, rule_id: str, severity: str, evidence: Mapping[str, Any], status: str = "OPEN") -> str:
        finding_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO translation_qa_findings(finding_id,revision_id,rule_id,severity,status,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)", (finding_id, revision_id, rule_id, severity, status, json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, default=str), _now()))
        return finding_id

    def record_response(self, *, official_sku: str, source_fields: Mapping[str, Any], source_hash_value: str, observed_at: str, source_run_id: str | None, response: Any, qa: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one provider call and field revisions into the shadow registry."""
        source_id = self.register_source(official_sku, source_fields, source_hash_value, observed_at=observed_at, source_run_id=source_run_id)
        self.record_provider_call(provider=response.provider, model=response.model, request_hash=response.request_hash, response_hash=response.response_hash, source_hash=source_hash_value, status="COMPLETED", usage=response.usage, request_id=response.request_id)
        revisions = []
        with connect(self.path) as db:
            units = {str(row["field_name"]): str(row["unit_id"]) for row in db.execute("SELECT unit_id,field_name FROM translation_units WHERE source_version_id=?", (source_id,)).fetchall()}
        findings = qa.get("findings") if isinstance(qa, Mapping) else []
        for field_name, value in response.fields.items():
            unit_id = units.get(field_name) or units.get({"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name))
            if not unit_id:
                continue
            revision_id = self.record_revision(unit_id=unit_id, target_text=str(value), provider=response.provider, model=response.model, request_hash=response.request_hash, response_hash=response.response_hash, request_id=response.request_id, source_hash=source_hash_value, qa_status=str(qa.get("status") or "FAIL"), review_status="PENDING")
            revisions.append(revision_id)
            for finding in findings or []:
                if str(finding.get("field_name") or "") in {field_name, ""}:
                    self.record_finding(revision_id, rule_id=str(finding.get("rule_id") or "UNKNOWN"), severity=str(finding.get("severity") or "HIGH"), evidence=dict(finding.get("evidence") or {}))
        return {"source_version_id": source_id, "revision_ids": revisions}

    def add_term(self, source_term: str, target_term: str, *, scope: str = "GLOBAL", approval_status: str = "PENDING", notes: str = "") -> str:
        term_id = str(uuid.uuid4())
        with connect(self.path) as db:
            row = db.execute("SELECT term_id FROM terminology_entries WHERE source_term=? AND target_language='zh' AND scope=?", (source_term, scope)).fetchone()
            if row:
                return str(row[0])
            db.execute("INSERT INTO terminology_entries(term_id,source_term,target_term,scope,approval_status,notes,created_at) VALUES(?,?,?,?,?,?,?)", (term_id, source_term, target_term, scope, approval_status, notes, _now()))
        return term_id

    def add_tm(self, source_text: str, target_text: str, *, field_name: str | None = None, context_key: str | None = None, approval_status: str = "PENDING") -> str:
        tm_id = str(uuid.uuid4())
        source_hash = value_hash(source_text)
        with connect(self.path) as db:
            row = db.execute("SELECT tm_id FROM translation_memory_entries WHERE source_language='es' AND target_language='zh' AND source_hash=? AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '')", (source_hash, field_name, context_key)).fetchone()
            if row:
                return str(row[0])
            db.execute("INSERT INTO translation_memory_entries(tm_id,source_language,target_language,source_text,target_text,source_hash,field_name,context_key,approval_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (tm_id, "es", "zh", source_text, target_text, source_hash, field_name, context_key, approval_status, _now()))
        return tm_id

    def ingest_records(self, records: Iterable[Mapping[str, Any]], *, source_run_id: str, observed_at: str) -> dict[str, int]:
        """Register only source versions and queue unresolved units.

        This is an explicit shadow operation. It never updates product facts,
        localizations, Master or dictionary files.
        """
        source_count = unit_count = queue_count = skipped = 0
        for record in records:
            facts = SourceFacts.from_record(record)
            if not facts.sku:
                skipped += 1
                continue
            fields = {"name_es": facts.name_es, "cat1_es": facts.cat1_es, "cat2_es": facts.cat2_es, "spec_es": facts.spec_es, "desc_es": facts.desc_es, "details_es": facts.details_es}
            source_id = self.register_source(facts.sku, fields, source_hash(facts.as_record()), observed_at=observed_at, source_run_id=source_run_id)
            source_count += 1
            with connect(self.path) as db:
                units = db.execute("SELECT unit_id,field_name FROM translation_units WHERE source_version_id=?", (source_id,)).fetchall()
                for unit in units:
                    unit_count += 1
                    queue_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"translation:{facts.sku}:{source_hash(facts.as_record())}:{unit['field_name']}"))
                    db.execute("INSERT OR IGNORE INTO translation_queue(queue_id,official_sku,language,source_hash,requested_fields,reason,priority,status,run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (queue_id, facts.sku, "zh", source_hash(facts.as_record()), unit["field_name"], "SOURCE_VERSION_NEW_OR_CHANGED", "NORMAL", "PENDING", source_run_id, _now()))
                    queue_count += 1
        return {"source_versions": source_count, "units": unit_count, "queue_insert_attempts": queue_count, "skipped": skipped}

    def approved_terms(self, *, source_terms: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with connect(self.path) as db:
            if source_terms:
                terms = tuple(source_terms)
                placeholders = ",".join("?" for _ in terms)
                rows = db.execute(f"SELECT source_term,target_term,scope FROM terminology_entries WHERE approval_status='APPROVED' AND source_term IN ({placeholders})", terms).fetchall()
            else:
                rows = db.execute("SELECT source_term,target_term,scope FROM terminology_entries WHERE approval_status='APPROVED' ORDER BY source_term").fetchall()
        return [dict(row) for row in rows]

    def approved_tm(self, source_hashes: Iterable[str]) -> list[dict[str, Any]]:
        hashes = tuple(source_hashes)
        if not hashes:
            return []
        placeholders = ",".join("?" for _ in hashes)
        with connect(self.path) as db:
            rows = db.execute(f"SELECT source_text,target_text,field_name,context_key,source_hash FROM translation_memory_entries WHERE approval_status='APPROVED' AND source_hash IN ({placeholders})", hashes).fetchall()
        return [dict(row) for row in rows]
