from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...database.connection import connect
from ...database.schema import migrate_v2
from ..hashes import SOURCE_HASH_CONTRACT_VERSION, canonical_json, value_hash
from ..memory.repository import normalize_memory_source
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
            # Freshness is field-level.  A changed spec must not invalidate an
            # already approved name/category translation.  Capture the latest
            # prior unit/revision per field before inserting the immutable new
            # source version, then stale only fields whose source_text_hash
            # actually changed.
            prior_rows = db.execute("""SELECT u.field_name,u.source_text_hash,u.current_revision_id,
                    r.target_text,r.target_hash,r.provider,r.model,r.request_hash,r.response_hash,
                    r.request_id,r.policy_version,r.terminology_version,r.tm_version,r.repair_reason,
                    r.qa_status,r.review_status,r.approved_by,r.approved_at
                FROM translation_units u
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id
                WHERE s.official_sku=?
                  AND s.source_version_id=(SELECT source_version_id FROM translation_source_versions
                    WHERE official_sku=? ORDER BY created_at DESC LIMIT 1)""", (official_sku, official_sku)).fetchall()
            prior_by_field = {str(row[0]): row for row in prior_rows}
            db.execute("INSERT INTO translation_source_versions(source_version_id,official_sku,source_hash,hash_contract_version,source_fields_json,raw_fact_hash,normalized_fact_hash,raw_fact_json,normalized_fact_json,source_quality_status,observed_at,source_run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (source_id, official_sku, source_hash, hash_contract_version, payload, raw_hash, normalized_hash, raw_payload, normalized_payload, source_quality_status, observed_at, source_run_id, _now()))
            for field_name, value in fields.items():
                if value is None:
                    continue
                unit_id = str(uuid.uuid4())
                text = str(value)
                field_key = str(field_name)
                text_hash = value_hash(text)
                prior = prior_by_field.get(field_key)
                reusable = bool(prior and str(prior[1]) == text_hash and prior[3] is not None
                                and str(prior[14] or "").upper() == "PASS"
                                and str(prior[15] or "").upper() in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"})
                db.execute("INSERT INTO translation_units(unit_id,source_version_id,field_name,source_text,source_text_hash,target_language,status,current_revision_id,freshness_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (unit_id, source_id, field_key, text, text_hash, "zh", "APPROVED" if reusable else "PENDING", None, "FRESH", _now(), _now()))
                if reusable:
                    revision_id = str(uuid.uuid4())
                    db.execute("""INSERT INTO translation_revisions(
                        revision_id,unit_id,revision,target_text,target_hash,provider,model,
                        request_hash,response_hash,request_id,policy_version,terminology_version,
                        tm_version,source_hash,parent_revision_id,repair_reason,qa_status,review_status,
                        approved_by,approved_at,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        revision_id, unit_id, 1, prior[3], prior[4], prior[5], prior[6], prior[7],
                        prior[8], prior[9], prior[10], prior[11], prior[12], source_hash,
                        str(prior[2]) if prior[2] else None, "FIELD_SOURCE_UNCHANGED_REUSE",
                        prior[14], prior[15], prior[16], prior[17], _now()))
                    db.execute("UPDATE translation_units SET current_revision_id=? WHERE unit_id=?", (revision_id, unit_id))
                    db.execute("INSERT INTO translation_revision_events(event_id,revision_id,event_type,actor,evidence_json,occurred_at) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), revision_id, "REUSED", "system:field-freshness", json.dumps({"source_hash": source_hash, "field_name": field_key}, ensure_ascii=False, sort_keys=True), _now()))

            # Mark only changed fields stale across prior source versions.
            for field_key, prior in prior_by_field.items():
                new_value = fields.get(field_key)
                new_hash = value_hash(str(new_value)) if new_value is not None else None
                if new_hash == str(prior[1]):
                    continue
                db.execute("""UPDATE translation_units SET freshness_status='STALE',status='STALE',updated_at=?
                    WHERE field_name=? AND source_version_id IN
                    (SELECT source_version_id FROM translation_source_versions WHERE official_sku=? AND source_version_id<>?)""", (_now(), field_key, official_sku, source_id))
                db.execute("""UPDATE translation_revisions SET review_status='STALE'
                    WHERE unit_id IN (SELECT unit_id FROM translation_units WHERE field_name=?
                        AND source_version_id IN (SELECT source_version_id FROM translation_source_versions WHERE official_sku=? AND source_version_id<>?))
                      AND review_status IN ('APPROVED','HUMAN_REVIEWED','LOCKED')""", (field_key, official_sku, source_id))
        return source_id

    def record_provider_call(self, *, provider: str, model: str, request_hash: str, response_hash: str | None, source_hash: str, status: str, usage: Mapping[str, Any] | None = None, error_code: str | None = None, request_id: str | None = None, latency_ms: int | None = None, retry_count: int = 0, cost_estimate: float | None = None, artifact_ref: str | None = None) -> str:
        call_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO translation_provider_calls(call_id,provider,model,request_hash,response_hash,source_hash,status,usage_json,request_id,latency_ms,retry_count,cost_estimate,artifact_ref,error_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (call_id, provider, model, request_hash, response_hash, source_hash, status, json.dumps(dict(usage or {}), ensure_ascii=False, sort_keys=True), request_id, latency_ms, retry_count, cost_estimate, artifact_ref, error_code, _now()))
        return call_id

    def record_revision(self, *, unit_id: str, target_text: str, provider: str, model: str, request_hash: str, response_hash: str, qa_status: str = "PENDING", review_status: str = "PENDING", request_id: str | None = None, policy_version: str | None = None, terminology_version: str | None = None, tm_version: str | None = None, source_hash: str | None = None, parent_revision_id: str | None = None, repair_reason: str | None = None) -> str:
        revision_id = str(uuid.uuid4())
        with connect(self.path) as db:
            row = db.execute("SELECT COALESCE(MAX(revision),0) FROM translation_revisions WHERE unit_id=?", (unit_id,)).fetchone()
            revision = int(row[0]) + 1
            db.execute("INSERT INTO translation_revisions(revision_id,unit_id,revision,target_text,target_hash,provider,model,request_hash,response_hash,request_id,policy_version,terminology_version,tm_version,source_hash,parent_revision_id,repair_reason,qa_status,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (revision_id, unit_id, revision, target_text, value_hash(target_text), provider, model, request_hash, response_hash, request_id, policy_version, terminology_version, tm_version, source_hash, parent_revision_id, repair_reason, qa_status, review_status, _now()))
            unit_status = "APPROVED" if str(review_status).upper() in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"} else ("REVIEW_REQUIRED" if str(qa_status).upper() == "PASS" else "BLOCKED")
            db.execute("UPDATE translation_units SET current_revision_id=?,status=?,updated_at=? WHERE unit_id=?", (revision_id, unit_status, _now(), unit_id))
        return revision_id

    def record_revision_for_sku(self, official_sku: str, field_name: str, target_text: str, *, source_hash: str,
                                provider: str, repair_reason: str, parent_revision_id: str | None = None,
                                qa_status: str = "PENDING") -> str:
        """Create a field-only repair revision without overwriting its parent."""
        canonical = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name)
        with connect(self.path) as db:
            row = db.execute("""SELECT u.unit_id FROM translation_units u
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.official_sku=? AND s.source_hash=? AND u.field_name IN (?,?) LIMIT 1""", (official_sku, source_hash, field_name, canonical)).fetchone()
        if not row:
            raise ValueError("TRANSLATION_UNIT_NOT_FOUND_FOR_REPAIR")
        digest = value_hash(target_text)
        return self.record_revision(unit_id=str(row[0]), target_text=target_text, provider=provider, model=provider, request_hash=digest, response_hash=digest, source_hash=source_hash, parent_revision_id=parent_revision_id, repair_reason=repair_reason, qa_status=qa_status)

    def record_finding(self, revision_id: str, *, rule_id: str, severity: str, evidence: Mapping[str, Any], status: str = "OPEN") -> str:
        finding_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO translation_qa_findings(finding_id,revision_id,rule_id,severity,status,evidence_json,created_at) VALUES(?,?,?,?,?,?,?)", (finding_id, revision_id, rule_id, severity, status, json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, default=str), _now()))
        return finding_id

    def _revision_event(self, revision_id: str, event_type: str, actor: str, evidence: Mapping[str, Any] | None = None) -> None:
        with connect(self.path) as db:
            db.execute("INSERT INTO translation_revision_events(event_id,revision_id,event_type,actor,evidence_json,occurred_at) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), revision_id, event_type, actor, json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True, default=str), _now()))

    def approve_revision(self, revision_id: str, *, actor: str, auto: bool = False) -> bool:
        if auto:
            with connect(self.path) as db:
                row = db.execute("SELECT value FROM schema_metadata WHERE key='auto_approval_enabled'").fetchone()
            if not row or str(row[0]).lower() not in {"1", "true", "yes"}:
                raise ValueError("AUTO_APPROVAL_DISABLED")
        with connect(self.path) as db:
            row = db.execute("SELECT qa_status,review_status FROM translation_revisions WHERE revision_id=?", (revision_id,)).fetchone()
            if not row or str(row[0]).upper() != "PASS":
                return False
            blockers = db.execute("SELECT COUNT(*) FROM translation_qa_findings WHERE revision_id=? AND status='OPEN' AND severity IN ('BLOCKER','ERROR','HIGH')", (revision_id,)).fetchone()[0]
            if blockers:
                return False
            approved_at = _now()
            db.execute("UPDATE translation_revisions SET review_status='APPROVED',approved_by=?,approved_at=? WHERE revision_id=? AND review_status NOT IN ('REJECTED','SUPERSEDED','STALE')", (actor, approved_at, revision_id))
            db.execute("UPDATE translation_units SET status='APPROVED',updated_at=? WHERE current_revision_id=?", (approved_at, revision_id))
        self._revision_event(revision_id, "AUTO_APPROVED" if auto else "APPROVED", actor)
        return True

    def reject_revision(self, revision_id: str, *, actor: str, reason: str = "") -> bool:
        with connect(self.path) as db:
            cur = db.execute("UPDATE translation_revisions SET review_status='REJECTED' WHERE revision_id=? AND review_status NOT IN ('APPROVED','SUPERSEDED')", (revision_id,))
        if cur.rowcount:
            self._revision_event(revision_id, "REJECTED", actor, {"reason": reason})
        return cur.rowcount == 1

    def get_current_approved_revision(self, official_sku: str, field_name: str, source_hash: str) -> dict[str, Any] | None:
        canonical = {"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}.get(field_name, field_name)
        with connect(self.path) as db:
            row = db.execute("""SELECT r.* FROM translation_revisions r JOIN translation_units u ON u.unit_id=r.unit_id
                JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                WHERE s.official_sku=? AND u.field_name IN (?,?) AND r.source_hash=?
                  AND u.freshness_status='FRESH'
                  AND r.review_status IN ('APPROVED','HUMAN_REVIEWED','LOCKED') AND r.qa_status='PASS'
                  AND NOT EXISTS (SELECT 1 FROM translation_qa_findings f WHERE f.revision_id=r.revision_id AND f.status='OPEN' AND f.severity IN ('BLOCKER','ERROR','HIGH'))
                ORDER BY r.revision DESC LIMIT 1""", (official_sku, field_name, canonical, source_hash)).fetchone()
        return dict(row) if row else None

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

    def add_term(self, source_term: str, target_term: str, *, scope: str = "GLOBAL", approval_status: str = "PENDING", notes: str = "",
                 term_type: str = "TERM", field_scope: str | None = None, cat1_scope: str | None = None,
                 cat2_scope: str | None = None, product_type_scope: str | None = None,
                 context_key: str | None = None, priority: int = 0, match_mode: str = "SUBSTRING",
                 case_sensitive: bool = False, do_not_translate: bool = False, keep_original: bool = False,
                 forbidden_target: str | None = None, source: str | None = None, evidence: str | None = None,
                 approved_by: str | None = None) -> str:
        term_id = str(uuid.uuid4())
        with connect(self.path) as db:
            row = db.execute("SELECT term_id FROM terminology_entries WHERE source_term=? AND target_language='zh' AND scope=?", (source_term, scope)).fetchone()
            if row:
                return str(row[0])
            db.execute("""INSERT INTO terminology_entries(term_id,source_term,target_term,scope,approval_status,notes,created_at,
                term_type,field_scope,cat1_scope,cat2_scope,product_type_scope,context_key,priority,match_mode,
                case_sensitive,do_not_translate,keep_original,forbidden_target,source,evidence,approved_by,approved_at,updated_at)
                VALUES(?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (term_id, source_term, target_term, scope, approval_status, notes, _now(), term_type, field_scope, cat1_scope, cat2_scope, product_type_scope, context_key, int(priority), match_mode, int(case_sensitive), int(do_not_translate), int(keep_original), forbidden_target, source, evidence, approved_by, _now() if approved_by else None, _now()))
        return term_id

    def add_tm(self, source_text: str, target_text: str, *, field_name: str | None = None, context_key: str | None = None, approval_status: str = "PENDING", match_type: str = "EXACT", normalization_version: str = "TM_NORMALIZATION_V1", source_revision_id: str | None = None) -> str:
        tm_id = str(uuid.uuid4())
        source_hash = value_hash(source_text)
        with connect(self.path) as db:
            row = db.execute("SELECT tm_id FROM translation_memory_entries WHERE source_language='es' AND target_language='zh' AND source_hash=? AND COALESCE(field_name,'')=COALESCE(?, '') AND COALESCE(context_key,'')=COALESCE(?, '')", (source_hash, field_name, context_key)).fetchone()
            if row:
                return str(row[0])
            db.execute("INSERT INTO translation_memory_entries(tm_id,source_language,target_language,source_text,target_text,source_hash,normalized_source_hash,match_type,normalization_version,field_name,context_key,approval_status,source_revision_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tm_id, "es", "zh", source_text, target_text, source_hash, value_hash(normalize_memory_source(source_text)), match_type, normalization_version, field_name, context_key, approval_status, source_revision_id, _now()))
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
                units = db.execute("""SELECT u.unit_id,u.field_name,u.source_text,u.current_revision_id,
                    r.qa_status,r.review_status FROM translation_units u
                    LEFT JOIN translation_revisions r ON r.revision_id=u.current_revision_id
                    WHERE u.source_version_id=?""", (source_id,)).fetchall()
                for unit in units:
                    unit_count += 1
                    if not str(unit["source_text"] or "").strip():
                        continue
                    # Unchanged fields with a fresh approved revision are
                    # reused and must not be re-enqueued.  Only missing,
                    # pending or changed-field units enter the worker queue.
                    if unit["current_revision_id"] and str(unit["qa_status"] or "").upper() == "PASS" and str(unit["review_status"] or "").upper() in {"APPROVED", "HUMAN_REVIEWED", "LOCKED"}:
                        continue
                    canonical = {"name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2", "spec_es": "spec", "desc_es": "description", "details_es": "details"}.get(str(unit["field_name"]), str(unit["field_name"]))
                    queue_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"translation:{facts.sku}:{source_hash(facts.as_record())}:{canonical}"))
                    db.execute("INSERT OR IGNORE INTO translation_queue(queue_id,official_sku,language,source_hash,requested_fields,reason,priority,status,run_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (queue_id, facts.sku, "zh", source_hash(facts.as_record()), canonical, "SOURCE_VERSION_NEW_OR_CHANGED", "NORMAL", "PENDING", source_run_id, _now()))
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

    def claim_queue(self, *, limit: int = 50, worker_id: str = "localization-worker") -> list[dict[str, Any]]:
        """Atomically claim pending/retry units so a worker cannot double-consume."""
        now = _now()
        claimed: list[dict[str, Any]] = []
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT queue_id,official_sku,language,source_hash,requested_fields,retry_count,run_id FROM translation_queue WHERE status IN ('PENDING','RETRY') ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END,created_at LIMIT ?", (int(limit),)).fetchall()
            for row in rows:
                cur = db.execute("UPDATE translation_queue SET status='CLAIMED',claimed_at=?,last_error=? WHERE queue_id=? AND status IN ('PENDING','RETRY')", (now, worker_id, row[0]))
                if cur.rowcount == 1:
                    claimed.append(dict(row))
            db.commit()
        return claimed

    def complete_queue(self, queue_id: str) -> bool:
        with connect(self.path) as db:
            cur = db.execute("UPDATE translation_queue SET status='COMPLETED',completed_at=? WHERE queue_id=? AND status='CLAIMED'", (_now(), queue_id))
            return cur.rowcount == 1

    def fail_queue(self, queue_id: str, error: str, *, retry: bool = True, max_retries: int = 3) -> bool:
        with connect(self.path) as db:
            row = db.execute("SELECT retry_count FROM translation_queue WHERE queue_id=? AND status='CLAIMED'", (queue_id,)).fetchone()
            if not row:
                return False
            count = int(row[0] or 0) + 1
            status = "RETRY" if retry and count <= max_retries else "FAILED"
            cur = db.execute("UPDATE translation_queue SET status=?,retry_count=?,last_error=? WHERE queue_id=? AND status='CLAIMED'", (status, count, str(error)[:1000], queue_id))
            return cur.rowcount == 1

    def block_queue(self, queue_id: str, error: str) -> bool:
        """Terminally block a queue item after a deterministic QA blocker."""
        with connect(self.path) as db:
            cur = db.execute("UPDATE translation_queue SET status='BLOCKED',last_error=? WHERE queue_id=? AND status='CLAIMED'", (str(error)[:1000], queue_id))
            return cur.rowcount == 1

    def queue_status(self) -> dict[str, int]:
        with connect(self.path) as db:
            return {str(row[0]): int(row[1]) for row in db.execute("SELECT status,COUNT(*) FROM translation_queue GROUP BY status").fetchall()}
