"""SQLite persistence for Knowledge Production previews and audits.

Knowledge applies are still explicitly disabled by configuration in normal
production. If enabled by an authorized caller, this store stages immutable
field patches and delegates the single-transaction apply gate to the PRIMARY
database coordinator; it never performs a direct localization upsert.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..database.connection import connect
from ..database.immutable_patches import append_patch_event, create_localization_patch
from ..database.production import apply_approved_localization_patches
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
                """INSERT INTO translation_candidates(candidate_id,queue_id,official_sku,language,source_hash,model_provider,model_name,prompt_version,fields_json,confidence,validation_status,approval_status,approved_by,approved_at,approval_evidence,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(candidate_id) DO UPDATE SET fields_json=excluded.fields_json,confidence=excluded.confidence,
                   validation_status=excluded.validation_status,approval_status=excluded.approval_status,
                   approved_by=excluded.approved_by,approved_at=excluded.approved_at,approval_evidence=excluded.approval_evidence""",
                (candidate_id, candidate["queue_id"], candidate["sku"], candidate.get("language", "zh"), candidate["source_hash"],
                 candidate.get("model_provider"), candidate.get("model_name"), candidate["prompt_version"],
                 json.dumps(candidate.get("fields") or {}, ensure_ascii=False, sort_keys=True), candidate.get("confidence"),
                 candidate.get("validation_status", "PENDING"), candidate.get("approval_status", "PENDING"),
                 candidate.get("approved_by"), candidate.get("approved_at"),
                 json.dumps(candidate.get("approval_evidence") or {}, ensure_ascii=False, sort_keys=True),
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
                            enabled: bool = False, commit_id: str | None = None,
                            expected_base_commit_id: str | None = None) -> int:
        """Stage approved candidates as immutable patches, then apply them.

        The old direct ``product_localizations`` upsert path is deliberately
        gone.  A caller must provide the committed base explicitly; each
        candidate field becomes one patch and the production apply coordinator
        performs the single transaction with source-hash and old-value gates.
        ``commit_id`` is retained only for source compatibility and is not
        treated as an implicit base.
        """
        if not enabled:
            raise PermissionError("KNOWLEDGE_PRODUCTION_APPLY_DISABLED")
        if not expected_base_commit_id:
            raise PermissionError("KNOWLEDGE_APPLY_BASE_COMMIT_REQUIRED")
        patch_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for candidate in candidates:
            sku = str(candidate.get("sku") or "").strip()
            record = records.get(sku)
            if not record or str(candidate.get("source_hash") or "") != source_hash(record):
                raise ValueError("STALE_TRANSLATION_PREVIEW")
            if "approval_status" not in candidate:
                raise PermissionError("CANDIDATE_APPROVAL_STATUS_MISSING")
            approval_status = str(candidate.get("approval_status") or "").upper()
            if approval_status not in {"APPROVED", "HUMAN_APPROVED"}:
                raise PermissionError("CANDIDATE_NOT_APPROVED")
            approved_by = str(candidate.get("approved_by") or "").strip()
            approved_at = str(candidate.get("approved_at") or "").strip()
            approval_evidence = candidate.get("approval_evidence") or {}
            if not approved_by or not approved_at or not isinstance(approval_evidence, dict) or not approval_evidence:
                raise PermissionError("CANDIDATE_APPROVAL_METADATA_MISSING")
            if not (approved_by.startswith("human:") or approved_by.startswith("service:")):
                raise PermissionError("CANDIDATE_APPROVER_INVALID")
            try:
                datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise PermissionError("CANDIDATE_APPROVED_AT_INVALID") from exc
            provenance = str(candidate.get("provenance") or "human_approved_ai")
            fields = candidate.get("fields") or {}
            if not isinstance(fields, dict):
                raise ValueError("CANDIDATE_FIELDS_INVALID")
            with connect(self.path) as db:
                current_row = db.execute(
                    "SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='zh'",
                    (sku,),
                ).fetchone()
            current = dict(zip(("name", "cat1", "cat2", "spec", "description", "details"), current_row or (None,) * 6))
            for field, value in fields.items():
                if field not in current or not isinstance(value, str):
                    continue
                if current[field] == value:
                    continue
                patch_id = hashlib.sha256(
                    f"knowledge|{sku}|zh|{field}|{candidate['source_hash']}|{expected_base_commit_id}|{value}".encode("utf-8")
                ).hexdigest()
                create_localization_patch(
                    self.path, patch_id=patch_id, official_sku=sku, language="zh", field_name=field,
                    old_value=current[field], new_value=value, source_hash=str(candidate["source_hash"]),
                    source_allowlist=[provenance], created_by=approved_by,
                    evidence={"field_name": field, "base_commit_id": expected_base_commit_id,
                              "source_name": provenance, "approval_evidence": approval_evidence},
                    reason="knowledge_candidate_apply",
                )
                append_patch_event(
                    self.path, patch_id=patch_id, event_type="PATCH_APPROVED", actor=approved_by,
                    evidence={"field_name": field, "base_commit_id": expected_base_commit_id,
                              "source_name": provenance, "approval_evidence": approval_evidence},
                )
                patch_ids.append(patch_id)
        if not patch_ids:
            return 0
        result = apply_approved_localization_patches(
            self.path, patch_ids=patch_ids, expected_base_commit_id=expected_base_commit_id,
            actor="service:knowledge-apply", run_id=f"knowledge_apply_{commit_id or uuid.uuid4().hex[:12]}",
        )
        return int(result["applied_fields"])
