"""Explicit Owner delegation for reviewed localization revisions.

No default service is authorized. Grants bind an exact actor, expiry and finite
revision manifest. Historical and current grants use distinct identities;
current grants additionally bind a committed, complete Listing observation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import SOURCE_TO_CANONICAL, CANONICAL_TO_SOURCE
from .hashes import value_hash
from ..services.hashing import localization_source_hash

ACTOR = "service:historical-localization-review"
VERSION = "HISTORICAL_OWNER_DELEGATION_V1"
CURRENT_ACTOR = "service:current-localization-review"
CURRENT_VERSION = "CURRENT_OWNER_DELEGATION_V1"


def delegation_hash(grant: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(grant), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def validate_delegation(grant, actor):
    if (not isinstance(grant, Mapping)
            or (grant.get("contract"), actor) not in {(VERSION, ACTOR), (CURRENT_VERSION, CURRENT_ACTOR)}
            or grant.get("actor") != actor
            or not str(grant.get("authorization_ref") or "").strip()
            or not str(grant.get("authorization_text") or "").strip()
            or not isinstance(grant.get("reviews"), Mapping) or not grant["reviews"]):
        raise PermissionError("DELEGATION_INVALID")
    if grant["contract"] == CURRENT_VERSION:
        if (not str(grant.get("collection_run_id") or "").strip()
                or not str(grant.get("business_date") or "").strip()
                or not isinstance(grant.get("current_skus"), list)
                or not grant["current_skus"]):
            raise PermissionError("DELEGATION_CURRENT_SCOPE_REQUIRED")
    try:
        expires = datetime.fromisoformat(str(grant["expires_at"]))
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ValueError("expired")
    except (ValueError, KeyError):
        raise PermissionError("DELEGATION_EXPIRED_OR_INVALID") from None
    return delegation_hash(grant)


def validate_delegated_revision(db, grant, *, actor, revision_id, require_approved=True, allow_stale_unit=False):
    grant_hash = validate_delegation(grant, actor)
    review = grant["reviews"].get(revision_id)
    if not isinstance(review, Mapping) or review.get("semantic_status") != "PASS":
        raise PermissionError("DELEGATION_REVISION_NOT_REVIEWED")
    if review.get("source_evidence_status") != "VERIFIED":
        raise PermissionError("DELEGATION_SOURCE_NOT_VERIFIED")
    if not review.get("evidence_ref") or not review.get("evidence_sha256"):
        raise PermissionError("DELEGATION_SOURCE_EVIDENCE_MISSING")
    evidence_file = Path(str(review["evidence_ref"]))
    if not evidence_file.is_file() or hashlib.sha256(evidence_file.read_bytes()).hexdigest() != review["evidence_sha256"]:
        raise PermissionError("DELEGATION_EVIDENCE_HASH_MISMATCH")
    row = db.execute("""SELECT s.official_sku,s.source_hash,u.field_name,u.source_text,
        u.current_revision_id,u.freshness_status,r.target_text,r.target_hash,
        r.qa_status,r.canonical_qa_status,r.review_status,r.approved_by,r.source_hash
        FROM translation_revisions r JOIN translation_units u ON u.unit_id=r.unit_id
        JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
        WHERE r.revision_id=?""", (revision_id,)).fetchone()
    if row is None:
        raise PermissionError("DELEGATION_REVISION_MISSING")
    sku, source_hash, raw_field, source_text, current, fresh, target, target_hash, qa, canonical, status, approver, revision_source = row
    field = SOURCE_TO_CANONICAL.get(str(raw_field), str(raw_field))
    product = db.execute("SELECT status FROM products WHERE official_sku=?", (sku,)).fetchone()
    if grant["contract"] == CURRENT_VERSION:
        if not product or str(product[0]) != "CURRENT" or str(sku) not in grant["current_skus"]:
            raise PermissionError("DELEGATION_CURRENT_SCOPE_REQUIRED")
        run = db.execute("SELECT run_date,status,qa_state,dry_run FROM runs WHERE run_id=?",
                         (grant["collection_run_id"],)).fetchone()
        present = db.execute("SELECT observation_date,presence_state,listing_present,observation_complete FROM observations WHERE run_id=? AND official_sku=?",
                             (grant["collection_run_id"], sku)).fetchone()
        if (not run or tuple(run) != (grant["business_date"], "COMMITTED", "PASS", 0)
                or not present or tuple(present) != (grant["business_date"], "PRESENT", 1, 1)):
            raise PermissionError("DELEGATION_CURRENT_OBSERVATION_REQUIRED")
    elif not product or str(product[0]) not in {"HISTORICAL", "ABSENT", "MISSING", "OFFLINE"}:
        raise PermissionError("DELEGATION_HISTORICAL_SCOPE_REQUIRED")
    if (str(sku) != review.get("sku") or field != review.get("field")
            or source_hash != review.get("source_hash") or target_hash != review.get("target_hash")
            or target_hash != value_hash(str(target)) or revision_source != source_hash):
        raise PermissionError("DELEGATION_REVIEW_BINDING_MISMATCH")
    if field not in {"name", "cat1", "cat2", "spec", "description", "details"}:
        raise PermissionError("DELEGATION_FIELD_NOT_ALLOWED")
    permitted_freshness = fresh == "FRESH" or (allow_stale_unit and grant["contract"] == CURRENT_VERSION and fresh == "STALE")
    if (current != revision_id or not permitted_freshness or qa != "PASS"
            or canonical not in {"PASS", "NOT_REQUIRED"} or not str(source_text or "").strip()
            or status in {"REJECTED", "SUPERSEDED", "STALE"}):
        raise PermissionError("DELEGATION_QA_OR_FRESHNESS_FAILED")
    if require_approved and (status != "APPROVED" or approver != actor):
        raise PermissionError("DELEGATION_APPROVAL_NOT_VALID")
    if require_approved:
        event = db.execute("SELECT evidence_json FROM translation_revision_events WHERE revision_id=? AND event_type='OWNER_DELEGATED_APPROVED' ORDER BY rowid DESC LIMIT 1", (revision_id,)).fetchone()
        if event is None or json.loads(event[0] or "{}").get("grant_hash") != grant_hash:
            raise PermissionError("DELEGATION_APPROVAL_GRANT_MISMATCH")
    blockers = db.execute("SELECT COUNT(*) FROM translation_qa_findings WHERE revision_id=? AND status='OPEN' AND severity IN ('BLOCKER','ERROR','HIGH')", (revision_id,)).fetchone()[0]
    if blockers:
        raise PermissionError("DELEGATION_OPEN_QA_BLOCKER")
    es = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
    if es is None:
        raise PermissionError("DELEGATION_PRIMARY_SOURCE_MISSING")
    facts = dict(zip(("name_es", "cat1_es", "cat2_es", "spec_es", "desc_es", "details_es"), es))
    if localization_source_hash(facts) != source_hash or str(facts[CANONICAL_TO_SOURCE[field]] or "") != str(source_text):
        raise PermissionError("DELEGATION_CURRENT_SOURCE_MISMATCH")
    return {"grant_hash": grant_hash, "actor": actor, "revision_id": revision_id,
            "sku": str(sku), "field": field, "source_hash": source_hash, "target_hash": target_hash,
            "authorization_ref": grant["authorization_ref"]}
