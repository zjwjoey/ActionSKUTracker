"""Exact two-source fact acknowledgements in the existing commit evidence store.

An acknowledgement preserves a reviewed difference. It never selects a winning
fact, waives QA, approves Chinese, or grants Presence. Sources must already be
in the sole canonical fact-version table; importing an external source remains
the responsibility of the normal, source-verified historical fact writer.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from ...database.immutable_patches import canonical_fact_version_table, validate_patch_approval_actor

CONTRACT = "FACT_VARIANCE_ACK_V1"
FIELDS = frozenset({"name", "cat1", "cat2", "spec", "description", "details"})
EFFECTS = {"does_not_waive_qa": True, "does_not_grant_fact_write": True,
           "does_not_approve_translation": True, "does_not_grant_presence": True}
OPERATIONS = {"FACT_VARIANCE_ACK", "FACT_VARIANCE_REVOKE"}


class FactVarianceError(ValueError):
    pass


def typed_hash(value):
    """ACK-only digest; do not change legacy source/value hash contracts."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _row(db, query, args=()):
    cursor = db.execute(query, args)
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row)) if row is not None else None


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timezone required")
        return result
    except (TypeError, AttributeError, ValueError) as exc:
        raise FactVarianceError("FACT_VARIANCE_TIME_INVALID") from exc


def source_descriptor(db, *, sku, fact_id, field):
    """Bind one actual canonical version/field without inventing source dates."""
    if field not in FIELDS:
        raise FactVarianceError("FACT_VARIANCE_SOURCE_FIELD_INVALID")
    table = canonical_fact_version_table(db)
    stored = _row(db, f"SELECT * FROM {table} WHERE fact_id=? AND official_sku=?", (fact_id, sku))
    if not stored:
        raise FactVarianceError("FACT_VARIANCE_SOURCE_VERSION_MISSING")
    if table == "product_fact_versions":
        if stored.get("raw_fact_available") != 1:
            raise FactVarianceError("FACT_VARIANCE_RAW_SOURCE_UNAVAILABLE")
        try:
            raw_map = json.loads(stored["raw_fact_json"])
            normalized_map = json.loads(stored["normalized_fact_json"])
            if not isinstance(raw_map, dict) or not isinstance(normalized_map, dict):
                raise ValueError("object required")
            if typed_hash(raw_map) != stored["raw_fact_hash"] or typed_hash(normalized_map) != stored["normalized_fact_hash"]:
                raise ValueError("source hash changed")
            raw, normalized = raw_map[field], normalized_map[field]
        except (ValueError, KeyError, TypeError) as exc:
            raise FactVarianceError("FACT_VARIANCE_SOURCE_HASH_INVALID") from exc
        normalization_version = stored["normalization_version"]
        observed_at = None  # created_at is import time, not original observation.
    else:
        if stored["field_name"] != field:
            raise FactVarianceError("FACT_VARIANCE_SOURCE_FIELD_INVALID")
        raw, normalized = stored["raw_value"], stored["normalized_value"]
        for value, key in ((raw, "raw_hash"), (normalized, "normalized_hash")):
            legacy = hashlib.sha256(("" if value is None else str(value)).encode("utf-8")).hexdigest()
            if legacy != stored[key]:
                raise FactVarianceError("FACT_VARIANCE_SOURCE_HASH_INVALID")
        normalization_version = stored["source_name"]
        observed_at = stored.get("observed_at")
    if not normalization_version:
        raise FactVarianceError("FACT_VARIANCE_NORMALIZATION_VERSION_MISSING")
    precision = "UNKNOWN"
    if observed_at:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_at):
            try:
                datetime.strptime(observed_at, "%Y-%m-%d")
                precision = "DATE"
            except ValueError:
                observed_at = None
        else:
            try:
                _time(observed_at)
                precision = "TIMESTAMP"
            except FactVarianceError:
                observed_at = None
    # A later third version of this field requires review again, even if the
    # current projection still happens to contain the old selected value.
    # No new source of a different SKU/field broadens this exact binding.
    frontier = []
    cursor = db.execute(f"SELECT * FROM {table} WHERE official_sku=? ORDER BY fact_id", (sku,))
    columns = [column[0] for column in cursor.description]
    for values in cursor:
        version = dict(zip(columns, values))
        if table == "product_fact_versions":
            try:
                raw_fields = json.loads(version["raw_fact_json"])
                normalized_fields = json.loads(version["normalized_fact_json"])
                if not isinstance(raw_fields, dict) or not isinstance(normalized_fields, dict):
                    raise ValueError("object required")
            except (ValueError, TypeError) as exc:
                raise FactVarianceError("FACT_VARIANCE_FRONTIER_SOURCE_INVALID") from exc
            if field not in raw_fields or field not in normalized_fields:
                continue
            frontier.append([version["fact_id"], raw_fields[field], normalized_fields[field], version["normalization_version"]])
        elif version["field_name"] == field:
            frontier.append([version["fact_id"], version["raw_hash"], version["normalized_hash"], version["source_name"]])
    return {"source_ref": f"PRIMARY:{table}:{fact_id}", "source_version_id": fact_id,
        "source_run_id": stored["run_id"], "source_field": field,
        "record_ref": field, "language": "es", "asset_sha256": typed_hash(stored),
        "raw_value": raw, "raw_hash": typed_hash(raw), "normalized_value": normalized,
        "normalized_hash": typed_hash(normalized), "normalization_version": normalization_version,
        "source_hash_contract": "CANONICAL_FACT_VERSION_AND_TYPED_VALUE_V1",
        "observed_at": observed_at, "observed_precision": precision,
        "recorded_at": stored["created_at"], "field_version_frontier_hash": typed_hash(frontier)}


def _target(db, sku, field):
    if field not in FIELDS or not isinstance(sku, str) or not re.fullmatch(r"\d+", sku):
        raise FactVarianceError("FACT_VARIANCE_IDENTITY_INVALID")
    identity = _row(db, "SELECT canonical_id FROM products WHERE official_sku=?", (sku,))
    if not identity or not identity["canonical_id"]:
        raise FactVarianceError("FACT_VARIANCE_IDENTITY_MISSING")
    row = _row(db, f"SELECT {field} AS value FROM product_localizations WHERE official_sku=? AND language='es'", (sku,))
    return identity["canonical_id"], row["value"] if row else None


def _records(db):
    # Issues are mutable projections; only committed run evidence is authority.
    # This literal prefilter is only routing, never authority. Parse and check
    # exact operation/contract below. It avoids decoding every huge collector
    # report in Python just to retrieve the few ACK receipts.
    for (payload,) in db.execute("SELECT e.evidence_json FROM run_evidence e JOIN commit_batches c ON c.run_id=e.run_id WHERE c.status='COMMITTED' AND (instr(e.evidence_json, '\"FACT_VARIANCE_ACK\"')>0 OR instr(e.evidence_json, '\"FACT_VARIANCE_REVOKE\"')>0) ORDER BY c.committed_at,c.commit_id"):
        try:
            record = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("operation") in OPERATIONS:
            yield record


def _state(db, ack_id):
    found = None
    revoked = False
    for record in _records(db):
        payload = record.get("fact_variance_ack") if record["operation"] == "FACT_VARIANCE_ACK" else record.get("fact_variance_revocation")
        if isinstance(payload, dict) and payload.get("ack_id") == ack_id:
            if record["operation"] == "FACT_VARIANCE_ACK":
                found = payload
            else:
                revoked = True
    return found, revoked


def seal_ack(payload):
    """Seal an externally reviewed payload; this function grants no approval."""
    result = dict(payload)
    result.pop("ack_id", None)
    result["ack_id"] = typed_hash(result)
    return result


def validate_ack(db, ack, *, now=None, authorized_service_actors=()):
    now = now or datetime.now(timezone.utc)
    if not isinstance(ack, dict) or ack.get("contract") != CONTRACT or ack.get("hash_version") != "TYPED_JSON_SHA256_V1":
        raise FactVarianceError("FACT_VARIANCE_CONTRACT_INVALID")
    if set(ack) != {"contract", "hash_version", "ack_id", "sku", "canonical_id", "field", "action",
            "policy", "status", "effects", "base_commit_id", "current_value_hash", "sources", "review"}:
        raise FactVarianceError("FACT_VARIANCE_CONTRACT_KEYS_INVALID")
    expected = seal_ack(ack)["ack_id"]
    if ack.get("ack_id") != expected:
        raise FactVarianceError("FACT_VARIANCE_ACK_HASH_INVALID")
    if (ack.get("action") != "ACK_SOURCE_VARIANCE" or ack.get("policy") != "KEEP_BOTH"
            or ack.get("status") != "CONFIRMED" or typed_hash(ack.get("effects")) != typed_hash(EFFECTS)):
        raise FactVarianceError("FACT_VARIANCE_EFFECT_NOT_PERMITTED")
    review = ack.get("review") or {}
    validate_patch_approval_actor(review.get("reviewed_by"), authorized_service_actors=authorized_service_actors)
    if (not isinstance(review.get("reason"), str) or not review["reason"].strip()
            or not isinstance(review.get("evidence_refs"), list) or not review["evidence_refs"]
            or not all(isinstance(ref, str) and ref.strip() for ref in review["evidence_refs"])):
        raise FactVarianceError("FACT_VARIANCE_REVIEW_EVIDENCE_MISSING")
    if not _time(review.get("reviewed_at")) <= now < _time(review.get("expires_at")):
        raise FactVarianceError("FACT_VARIANCE_REVIEW_EXPIRED_OR_FUTURE")
    sku, field = ack.get("sku"), ack.get("field")
    canonical_id, current = _target(db, sku, field)
    if ack.get("canonical_id") != canonical_id or ack.get("current_value_hash") != typed_hash(current):
        raise FactVarianceError("FACT_VARIANCE_TARGET_CHANGED")
    if not ack.get("base_commit_id"):
        raise FactVarianceError("FACT_VARIANCE_REVIEW_BASE_MISSING")
    if not db.execute("SELECT 1 FROM commit_batches WHERE commit_id=? AND status='COMMITTED'", (ack["base_commit_id"],)).fetchone():
        raise FactVarianceError("FACT_VARIANCE_REVIEW_BASE_MISSING")
    sources = ack.get("sources")
    if not isinstance(sources, list) or len(sources) != 2 or not all(isinstance(s, dict) for s in sources):
        raise FactVarianceError("FACT_VARIANCE_EXACT_SOURCE_PAIR_REQUIRED")
    for source in sources:
        actual = source_descriptor(db, sku=sku, fact_id=source.get("source_version_id"), field=source.get("source_field"))
        if typed_hash(actual) != typed_hash(source):
            raise FactVarianceError("FACT_VARIANCE_SOURCE_CHANGED")
    if (sources[0]["source_ref"], sources[0]["source_field"]) == (sources[1]["source_ref"], sources[1]["source_field"]):
        raise FactVarianceError("FACT_VARIANCE_DISTINCT_SOURCES_REQUIRED")
    if sources[0]["raw_hash"] == sources[1]["raw_hash"] and sources[0]["normalized_hash"] == sources[1]["normalized_hash"]:
        raise FactVarianceError("FACT_VARIANCE_NO_DIFFERENCE")
    if not any(source["source_field"] == field for source in sources):
        raise FactVarianceError("FACT_VARIANCE_TARGET_SOURCE_FIELD_MISSING")
    if _state(db, ack["ack_id"])[1]:
        raise FactVarianceError("FACT_VARIANCE_REVOKED")
    return ack


def match_ack(db, *, ack_id, sku, field, sources, current_value, now=None, authorized_service_actors=()):
    """Return an exact active ACK as context, never a QA/approval boolean."""
    ack, revoked = _state(db, ack_id)
    if not ack or revoked or ack.get("sku") != sku or ack.get("field") != field:
        return None
    if typed_hash(sources) != typed_hash(ack.get("sources")) or typed_hash(current_value) != ack.get("current_value_hash"):
        return None
    try:
        return validate_ack(db, ack, now=now, authorized_service_actors=authorized_service_actors)
    except (ValueError, TypeError):
        return None


def validate_commit(db, bundle, *, authorized_service_actors=()):
    """Called by the sole writer inside its existing BEGIN IMMEDIATE boundary."""
    operation = bundle.run_record.get("operation")
    if operation not in OPERATIONS:
        return
    if any((bundle.current_products, bundle.localization_updates, bundle.lifecycle_updates,
            bundle.observations, bundle.price_events, bundle.event_events, bundle.review_rows,
            bundle.source_fact_versions)) or bundle.requires_collection_integrity:
        raise FactVarianceError("FACT_VARIANCE_METADATA_ONLY")
    if bundle.base_commit_id is None:
        raise FactVarianceError("FACT_VARIANCE_EXECUTION_BASE_REQUIRED")
    if operation == "FACT_VARIANCE_ACK":
        ack = validate_ack(db, bundle.run_record.get("fact_variance_ack"), authorized_service_actors=authorized_service_actors)
        if _state(db, ack["ack_id"])[0]:
            raise FactVarianceError("FACT_VARIANCE_ALREADY_RECORDED")
    else:
        revocation = bundle.run_record.get("fact_variance_revocation") or {}
        if revocation.get("contract") != CONTRACT:
            raise FactVarianceError("FACT_VARIANCE_CONTRACT_INVALID")
        validate_patch_approval_actor(revocation.get("reviewed_by"), authorized_service_actors=authorized_service_actors)
        if not revocation.get("reason") or not revocation.get("evidence_refs"):
            raise FactVarianceError("FACT_VARIANCE_REVIEW_EVIDENCE_MISSING")
        if _time(revocation.get("revoked_at")) > datetime.now(timezone.utc):
            raise FactVarianceError("FACT_VARIANCE_REVIEW_EXPIRED_OR_FUTURE")
        existing, revoked = _state(db, revocation.get("ack_id"))
        if not existing or revoked:
            raise FactVarianceError("FACT_VARIANCE_REVOCATION_NOT_ACTIVE")
        if _time(revocation["revoked_at"]) < _time(existing["review"]["reviewed_at"]):
            raise FactVarianceError("FACT_VARIANCE_REVOCATION_PRECEDES_REVIEW")


def build_ack_bundle(database, payload, *, run_id, run_date, base_commit_id, revoke=False, authorized_service_actors=()):
    """Read-only preview; commit rechecks sources/target/review in the transaction."""
    from ...localization.history_audit import ro
    from ...database.production import CommitBundle
    operation = "FACT_VARIANCE_REVOKE" if revoke else "FACT_VARIANCE_ACK"
    key = "fact_variance_revocation" if revoke else "fact_variance_ack"
    # Freeze caller data so mutation after preview cannot quietly expand scope.
    frozen = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    bundle = CommitBundle(run_id=run_id, observation_date=run_date, qa_state="PASS",
        base_commit_id=base_commit_id, run_record={"operation": operation, key: frozen})
    with ro(database) as db:
        validate_commit(db, bundle, authorized_service_actors=authorized_service_actors)
    return bundle
