"""SQLite persistence for Knowledge Production previews and audits.

This store intentionally has no production-apply method.  It records
resolution/queue/candidate/audit artifacts; applying Chinese values remains
behind the existing production gates until the SQLite Primary cutover.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..database.connection import connect
from ..database.schema import migrate_v2
from .approval import ApprovalDecision
from .contracts import Resolution, source_hash


class KnowledgeStore:
    def __init__(self, path: Path, *, role: str = "SHADOW") -> None:
        self.path = Path(path)
        if role not in {"SHADOW", "PRIMARY"}:
            raise ValueError("DB_ROLE_INVALID")
        migrate_v2(self.path, role=role)

    def save_resolutions(self, resolutions: Iterable[Resolution]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with connect(self.path) as db:
            for resolution in resolutions:
                rid = hashlib.sha256(f"{resolution.sku}|zh|{resolution.source_hash}".encode()).hexdigest()
                payload = resolution.as_jsonable()
                db.execute(
                    """INSERT INTO translation_resolution(resolution_id,official_sku,language,source_hash,base_commit_id,dictionary_hash,readiness,fields_json,reasons_json,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(official_sku,language,source_hash) DO UPDATE SET resolution_id=excluded.resolution_id,
                       base_commit_id=excluded.base_commit_id,dictionary_hash=excluded.dictionary_hash,readiness=excluded.readiness,
                       fields_json=excluded.fields_json,reasons_json=excluded.reasons_json,created_at=excluded.created_at""",
                    (rid, resolution.sku, "zh", resolution.source_hash, resolution.base_commit_id,
                     resolution.dictionary_hash, resolution.readiness, json.dumps(payload["fields"], ensure_ascii=False, sort_keys=True),
                     json.dumps(payload["reasons"], ensure_ascii=False), now),
                )
                count += 1
        return count

    def enqueue(self, rows: Iterable[dict[str, Any]]) -> int:
        count = 0
        with connect(self.path) as db:
            for row in rows:
                fields = tuple(row.get("requested_fields") or ())
                db.execute(
                    """INSERT INTO translation_queue(queue_id,official_sku,language,source_hash,requested_fields,reason,priority,status,retry_count,run_id,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(official_sku,language,source_hash,requested_fields) DO UPDATE SET reason=excluded.reason,
                       priority=excluded.priority,run_id=excluded.run_id""",
                    (row["queue_id"], row["sku"], row.get("language", "zh"), row["source_hash"],
                     json.dumps(fields, ensure_ascii=False), row["reason"], row["priority"], row.get("status", "PENDING"),
                     int(row.get("retry_count", 0)), row.get("run_id"), row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                )
                count += 1
        return count

    def save_candidate(self, candidate: dict[str, Any]) -> str:
        candidate_id = str(candidate.get("candidate_id") or hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        with connect(self.path) as db:
            db.execute(
                """INSERT INTO translation_candidates(candidate_id,queue_id,official_sku,language,source_hash,model_provider,model_name,prompt_version,fields_json,confidence,validation_status,approval_status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(candidate_id) DO UPDATE SET fields_json=excluded.fields_json,confidence=excluded.confidence,
                   validation_status=excluded.validation_status,approval_status=excluded.approval_status""",
                (candidate_id, candidate["queue_id"], candidate["sku"], candidate.get("language", "zh"), candidate["source_hash"],
                 candidate.get("model_provider"), candidate.get("model_name"), candidate["prompt_version"],
                 json.dumps(candidate.get("fields") or {}, ensure_ascii=False, sort_keys=True), candidate.get("confidence"),
                 candidate.get("validation_status", "PENDING"), candidate.get("approval_status", "PENDING"),
                 candidate.get("created_at") or datetime.now(timezone.utc).isoformat()),
            )
        return candidate_id

    def save_approval_audit(self, candidate_id: str, sku: str, source_hash: str, decisions: Iterable[ApprovalDecision]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with connect(self.path) as db:
            for decision in decisions:
                did = hashlib.sha256(f"{candidate_id}|{decision.field}|{decision.policy_version}".encode()).hexdigest()
                db.execute(
                    """INSERT INTO translation_approval_audit(decision_id,candidate_id,official_sku,field_name,decision,policy_version,rules_passed,rules_failed,source_hash,decided_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(candidate_id,field_name,policy_version) DO UPDATE SET decision=excluded.decision,
                       rules_passed=excluded.rules_passed,rules_failed=excluded.rules_failed,decided_at=excluded.decided_at""",
                    (did, candidate_id, sku, decision.field, decision.decision, decision.policy_version,
                     json.dumps(decision.rules_passed, ensure_ascii=False), json.dumps(decision.rules_failed, ensure_ascii=False), source_hash, now),
                )
                count += 1
        return count

    def preview_apply(self, candidates: Iterable[dict[str, Any]], records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Create a field-level, read-only apply plan against current facts."""
        preview: list[dict[str, Any]] = []
        with connect(self.path) as db:
            for candidate in candidates:
                sku = str(candidate.get("sku") or "")
                record = records.get(sku)
                if not record or str(candidate.get("source_hash") or "") != source_hash(record):
                    preview.append({"sku": sku, "decision": "REJECT", "reason": "STALE_TRANSLATION_PREVIEW"})
                    continue
                old = db.execute("SELECT * FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
                for field, value in (candidate.get("fields") or {}).items():
                    if field not in {"name", "cat1", "cat2", "spec", "description", "details"} or not isinstance(value, str):
                        continue
                    preview.append({"sku": sku, "field": field, "old_value": (old[field] if old else None),
                                    "new_value": value, "source_hash": candidate["source_hash"],
                                    "provenance": candidate.get("provenance", "human_approved_ai"),
                                    "decision": "APPLY"})
        return preview

    def apply_localizations(self, candidates: Iterable[dict[str, Any]], records: dict[str, dict[str, Any]], *,
                            enabled: bool = False, commit_id: str | None = None) -> int:
        """Apply approved candidate fields to PRIMARY SQLite only.

        This is intentionally explicit and field-level; it never touches
        products, lifecycle, prices, events, Master.xlsx, or exports.
        """
        if not enabled:
            raise PermissionError("KNOWLEDGE_PRODUCTION_APPLY_DISABLED")
        with connect(self.path) as db:
            role = db.execute("SELECT value FROM schema_metadata WHERE key='database_role'").fetchone()
            if not role or role[0] != "PRIMARY":
                raise PermissionError("KNOWLEDGE_APPLY_REQUIRES_PRIMARY")
            applied = 0
            for candidate in candidates:
                sku = str(candidate.get("sku") or "")
                record = records.get(sku)
                if not record or str(candidate.get("source_hash") or "") != source_hash(record):
                    raise ValueError("STALE_TRANSLATION_PREVIEW")
                if str(candidate.get("approval_status") or "APPROVED").upper() not in {"APPROVED", "HUMAN_APPROVED", "AUTO_APPROVED"}:
                    raise PermissionError("CANDIDATE_NOT_APPROVED")
                existing = db.execute("SELECT * FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
                current = dict(existing) if existing else {}
                values = {field: current.get(field) for field in ("name", "cat1", "cat2", "spec", "description", "details")}
                sources = {field: current.get(f"{field}_source") for field in values}
                for field, value in (candidate.get("fields") or {}).items():
                    if field in values and isinstance(value, str):
                        values[field] = value
                        sources[field] = candidate.get("provenance", "human_approved_ai")
                db.execute("""INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at,last_commit_id,source_hash,resolution_status,name_source,cat1_source,cat2_source,spec_source,description_source,details_source,freshness_status,approved_by,approved_at,applied_commit_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(official_sku,language) DO UPDATE SET name=excluded.name,cat1=excluded.cat1,cat2=excluded.cat2,spec=excluded.spec,description=excluded.description,details=excluded.details,source=excluded.source,review_status=excluded.review_status,updated_at=excluded.updated_at,last_commit_id=excluded.last_commit_id,source_hash=excluded.source_hash,resolution_status=excluded.resolution_status,name_source=excluded.name_source,cat1_source=excluded.cat1_source,cat2_source=excluded.cat2_source,spec_source=excluded.spec_source,description_source=excluded.description_source,details_source=excluded.details_source,freshness_status=excluded.freshness_status,approved_by=excluded.approved_by,approved_at=excluded.approved_at,applied_commit_id=excluded.applied_commit_id""",
                    (sku, "zh", values["name"], values["cat1"], values["cat2"], values["spec"], values["description"], values["details"], "KNOWLEDGE", "APPROVED", datetime.now(timezone.utc).isoformat(), commit_id, candidate["source_hash"], "APPLIED", sources["name"], sources["cat1"], sources["cat2"], sources["spec"], sources["description"], sources["details"], "CURRENT", "MANUAL", datetime.now(timezone.utc).isoformat(), commit_id))
                applied += 1
            return applied
