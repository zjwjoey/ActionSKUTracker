"""Formal metadata-only closure for already approved registry revisions."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .connection import connect
from .provenance import sync_localization_field_provenance
from .schema import migrate_v2
from ..services.hashing import localization_field_source_hash, localization_source_hash


class ProvenanceClosureError(RuntimeError):
    pass


_FIELDS = {"name_es": "name", "cat1_es": "cat1", "cat2_es": "cat2", "spec_es": "spec", "desc_es": "description", "details_es": "details"}
_APPROVED_STATUSES = {"VERIFIED", "APPROVED", "HUMAN_APPROVED", "HUMAN_REVIEWED", "APPROVED_SOURCE_ABSENT"}
_SOURCE_FIELD_BY_CANONICAL = {value: key for key, value in _FIELDS.items()}


def rebind_current_localization_aggregate_hashes(
    path: Path,
    *,
    official_skus: Iterable[str],
    expected_base_commit_id: str,
    actor: str,
    run_id: str,
) -> dict[str, object]:
    """Refresh aggregate ZH source bindings when every field binding is current.

    This is metadata-only: it never changes a Chinese value or field-level
    approval.  Each SKU must already have six current, approved field records
    whose field-scoped source hashes match the current official Spanish facts.
    """
    skus = tuple(dict.fromkeys(str(value).strip() for value in official_skus if str(value).strip()))
    if not skus or not str(actor).startswith("human:"):
        raise ProvenanceClosureError("AGGREGATE_METADATA_CLOSURE_IDENTITY_REQUIRED")
    path = Path(path); migrate_v2(path, role="PRIMARY"); now = datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            head = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            if not head or str(head[0]) != expected_base_commit_id:
                raise ProvenanceClosureError("STALE_AGGREGATE_METADATA_CLOSURE_BUNDLE")
            plans = []
            already_current = []
            for sku in skus:
                es = db.execute(
                    "SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'",
                    (sku,),
                ).fetchone()
                zh = db.execute(
                    "SELECT name,cat1,cat2,spec,description,details,source_hash FROM product_localizations WHERE official_sku=? AND language='zh'",
                    (sku,),
                ).fetchone()
                if es is None or zh is None:
                    raise ProvenanceClosureError("AGGREGATE_METADATA_CLOSURE_LOCALIZATION_MISSING:" + sku)
                record = {"name_es": es[0], "cat1_es": es[1], "cat2_es": es[2], "spec_es": es[3], "desc_es": es[4], "details_es": es[5]}
                aggregate_hash = localization_source_hash(record)
                if str(zh[6] or "") == aggregate_hash:
                    already_current.append(sku)
                    continue
                for index, field in enumerate(_FIELDS.values()):
                    field_row = db.execute(
                        "SELECT value,review_status,source_hash,freshness_status FROM localization_fields "
                        "WHERE official_sku=? AND language='zh' AND field_name=?",
                        (sku, field),
                    ).fetchone()
                    if field_row is None:
                        raise ProvenanceClosureError("AGGREGATE_METADATA_CLOSURE_FIELD_MISSING:" + sku + ":" + field)
                    status = str(field_row[1] or "").upper()
                    current_value = "" if zh[index] is None else str(zh[index])
                    source_value = "" if record[{"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]] is None else str(record[{"name": "name_es", "cat1": "cat1_es", "cat2": "cat2_es", "spec": "spec_es", "description": "desc_es", "details": "details_es"}[field]])
                    if (
                        str(field_row[0] or "") != current_value
                        or status not in _APPROVED_STATUSES
                        or str(field_row[3] or "").upper() != "CURRENT"
                        or str(field_row[2] or "") != localization_field_source_hash(record, field)
                        or (not source_value.strip() and status != "APPROVED_SOURCE_ABSENT")
                        or (source_value.strip() and not current_value.strip())
                    ):
                        raise ProvenanceClosureError("AGGREGATE_METADATA_CLOSURE_FIELD_NOT_READY:" + sku + ":" + field)
                plans.append((sku, aggregate_hash))
            if not plans:
                db.rollback()
                return {"status": "NOOP", "base_commit_id": expected_base_commit_id, "applied_skus": 0, "already_current_skus": already_current}
            bundle = json.dumps({"base": expected_base_commit_id, "skus": [sku for sku, _ in plans]}, sort_keys=True)
            commit_id = f"{datetime.now(timezone.utc).date().isoformat()}_{run_id}_{uuid.uuid5(uuid.NAMESPACE_OID, bundle).hex[:12]}"
            db.execute(
                "INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, str(datetime.now(timezone.utc).date()), "COMMITTED", "PASS", 0, now, now, "2.0.0"),
            )
            db.execute(
                "INSERT INTO run_evidence(run_id,snapshot_path,snapshot_hash,evidence_json) VALUES(?,?,?,?)",
                (run_id, None, None, json.dumps({"operation": "AGGREGATE_LOCALIZATION_METADATA_REBIND", "base_commit_id": expected_base_commit_id, "skus": [sku for sku, _ in plans]}, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO commit_batches(commit_id,run_id,base_commit_id,bundle_hash,schema_version,started_at,committed_at,product_count,observation_count,price_event_count,event_count,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (commit_id, run_id, expected_base_commit_id, uuid.uuid5(uuid.NAMESPACE_OID, bundle).hex, "2.0.0", now, now, len(plans), 0, 0, len(plans), "COMMITTED"),
            )
            for sku, aggregate_hash in plans:
                db.execute(
                    "UPDATE product_localizations SET source_hash=?,updated_at=?,last_commit_id=?,applied_commit_id=? WHERE official_sku=? AND language='zh'",
                    (aggregate_hash, now, commit_id, commit_id, sku),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"status": "SUCCESS", "commit_id": commit_id, "base_commit_id": expected_base_commit_id, "applied_skus": len(plans), "already_current_skus": already_current}


def apply_source_absence_closures(
    path: Path,
    *,
    sku_fields: Iterable[tuple[str, str]],
    expected_base_commit_id: str,
    actor: str,
    run_id: str,
) -> dict[str, object]:
    """Approve only verified empty official source fields without altering Chinese.

    An empty Chinese field is valid only when its own Spanish source field is
    also empty.  The closure writes an explicit field-level absence approval,
    then refreshes the aggregate binding when all six field bindings are ready.
    """
    items = tuple(dict.fromkeys(
        (str(sku).strip(), str(field).strip())
        for sku, field in sku_fields
        if str(sku).strip() and str(field).strip()
    ))
    if not items or not str(actor).startswith("human:"):
        raise ProvenanceClosureError("SOURCE_ABSENCE_CLOSURE_IDENTITY_REQUIRED")
    if any(field not in _SOURCE_FIELD_BY_CANONICAL for _, field in items):
        raise ProvenanceClosureError("SOURCE_ABSENCE_CLOSURE_FIELD_INVALID")
    path = Path(path); migrate_v2(path, role="PRIMARY"); now = datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            head = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            if not head or str(head[0]) != expected_base_commit_id:
                raise ProvenanceClosureError("STALE_SOURCE_ABSENCE_CLOSURE_BUNDLE")
            plans = []
            source_records = {}
            for sku, field in items:
                es = db.execute(
                    "SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'",
                    (sku,),
                ).fetchone()
                zh = db.execute(
                    "SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='zh'",
                    (sku,),
                ).fetchone()
                if es is None or zh is None:
                    raise ProvenanceClosureError("SOURCE_ABSENCE_CLOSURE_LOCALIZATION_MISSING:" + sku)
                record = {"name_es": es[0], "cat1_es": es[1], "cat2_es": es[2], "spec_es": es[3], "desc_es": es[4], "details_es": es[5]}
                index = tuple(_FIELDS.values()).index(field)
                if str(record[_SOURCE_FIELD_BY_CANONICAL[field]] or "").strip() or str(zh[index] or "").strip():
                    raise ProvenanceClosureError("SOURCE_ABSENCE_CLOSURE_NOT_EMPTY:" + sku + ":" + field)
                source_records[sku] = record
                plans.append((sku, field))
            bundle = json.dumps({"base": expected_base_commit_id, "fields": plans}, sort_keys=True)
            commit_id = f"{datetime.now(timezone.utc).date().isoformat()}_{run_id}_{uuid.uuid5(uuid.NAMESPACE_OID, bundle).hex[:12]}"
            db.execute(
                "INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, str(datetime.now(timezone.utc).date()), "COMMITTED", "PASS", 0, now, now, "2.0.0"),
            )
            db.execute(
                "INSERT INTO run_evidence(run_id,snapshot_path,snapshot_hash,evidence_json) VALUES(?,?,?,?)",
                (run_id, None, None, json.dumps({"operation": "SOURCE_ABSENCE_METADATA_CLOSURE", "base_commit_id": expected_base_commit_id, "fields": plans}, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO commit_batches(commit_id,run_id,base_commit_id,bundle_hash,schema_version,started_at,committed_at,product_count,observation_count,price_event_count,event_count,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (commit_id, run_id, expected_base_commit_id, uuid.uuid5(uuid.NAMESPACE_OID, bundle).hex, "2.0.0", now, now, len({sku for sku, _ in plans}), 0, 0, len(plans), "COMMITTED"),
            )
            for sku, field in plans:
                values = {
                    "official_sku": sku, "language": "zh", field: "",
                    f"{field}_source": "OFFICIAL_SOURCE_ABSENT",
                    f"{field}_review_status": "APPROVED_SOURCE_ABSENT",
                    f"{field}_freshness_status": "CURRENT",
                    f"{field}_source_hash": localization_field_source_hash(source_records[sku], field),
                    f"{field}_approved_by": actor, f"{field}_approved_at": now,
                    f"{field}_applied_commit_id": commit_id,
                }
                sync_localization_field_provenance(db, values, commit_id=commit_id, now=now)
            updated_skus = []
            for sku in sorted({sku for sku, _ in plans}):
                record = source_records[sku]
                ready = True
                for field in _FIELDS.values():
                    field_row = db.execute(
                        "SELECT review_status,source_hash,freshness_status FROM localization_fields WHERE official_sku=? AND language='zh' AND field_name=?",
                        (sku, field),
                    ).fetchone()
                    if field_row is None or str(field_row[0] or "").upper() not in _APPROVED_STATUSES or str(field_row[2] or "").upper() != "CURRENT" or str(field_row[1] or "") != localization_field_source_hash(record, field):
                        ready = False
                        break
                if ready:
                    db.execute(
                        "UPDATE product_localizations SET source_hash=?,updated_at=?,last_commit_id=?,applied_commit_id=? WHERE official_sku=? AND language='zh'",
                        (localization_source_hash(record), now, commit_id, commit_id, sku),
                    )
                    updated_skus.append(sku)
            db.commit()
        except Exception:
            db.rollback(); raise
    return {"status": "SUCCESS", "commit_id": commit_id, "base_commit_id": expected_base_commit_id, "applied_fields": len(plans), "aggregate_rebound_skus": updated_skus}


def apply_metadata_only_closures(path: Path, *, revision_ids: Iterable[str], expected_base_commit_id: str, actor: str, run_id: str) -> dict[str, object]:
    """Bind selected, already-approved revisions to PRIMARY without a value patch.

    This is intentionally narrower than a localization patch: target text must
    already equal PRIMARY, while source binding and provenance are refreshed.
    """
    ids = tuple(dict.fromkeys(str(value).strip() for value in revision_ids if str(value).strip()))
    if not ids or not str(actor).startswith("human:"):
        raise ProvenanceClosureError("METADATA_CLOSURE_IDENTITY_REQUIRED")
    path = Path(path); migrate_v2(path, role="PRIMARY"); now = datetime.now(timezone.utc).isoformat()
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            head = db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            if not head or str(head[0]) != expected_base_commit_id:
                raise ProvenanceClosureError("STALE_METADATA_CLOSURE_BUNDLE")
            plans = []; already_ready = []
            for revision_id in ids:
                row = db.execute("""SELECT r.revision_id,r.target_text,r.source_hash,r.approved_by,r.approved_at,u.field_name,u.freshness_status,s.official_sku
                    FROM translation_revisions r JOIN translation_units u ON u.unit_id=r.unit_id JOIN translation_source_versions s ON s.source_version_id=u.source_version_id
                    WHERE r.revision_id=? AND u.current_revision_id=r.revision_id AND u.freshness_status='FRESH' AND r.qa_status='PASS'
                      AND COALESCE(r.canonical_qa_status,'NOT_RUN') IN ('PASS','NOT_REQUIRED') AND r.review_status IN ('APPROVED','HUMAN_REVIEWED','LOCKED')
                      AND NOT EXISTS(SELECT 1 FROM translation_qa_findings f WHERE f.revision_id=r.revision_id AND f.status='OPEN' AND f.severity IN ('BLOCKER','ERROR','HIGH'))""", (revision_id,)).fetchone()
                if not row or str(row[5]) not in _FIELDS:
                    raise ProvenanceClosureError("METADATA_CLOSURE_REVISION_INVALID:" + revision_id)
                sku, field = str(row[7]), _FIELDS[str(row[5])]
                es = db.execute("SELECT name,cat1,cat2,spec,description,details FROM product_localizations WHERE official_sku=? AND language='es'", (sku,)).fetchone()
                current = db.execute(f"SELECT {field} FROM product_localizations WHERE official_sku=? AND language='zh'", (sku,)).fetchone()
                record = {"name_es": es[0], "cat1_es": es[1], "cat2_es": es[2], "spec_es": es[3], "desc_es": es[4], "details_es": es[5]} if es else None
                if not record or not current or str(current[0] or "") != str(row[1] or "") or localization_source_hash(record) != str(row[2]):
                    raise ProvenanceClosureError("METADATA_CLOSURE_SOURCE_OR_TARGET_CHANGED:" + sku + ":" + field)
                provenance = db.execute("SELECT value,source_hash,review_status,freshness_status FROM localization_field_provenance WHERE official_sku=? AND language='zh' AND field_name=?", (sku, field)).fetchone()
                field_hash = localization_field_source_hash(record, field)
                if provenance and str(provenance[0] or "") == str(row[1] or "") and str(provenance[1] or "") == field_hash and str(provenance[2] or "") == "APPROVED" and str(provenance[3] or "") == "CURRENT":
                    already_ready.append(revision_id); continue
                plans.append((row, sku, field, field_hash))
            if not plans:
                db.rollback()
                return {"status":"NOOP","base_commit_id":expected_base_commit_id,"applied_fields":0,"already_ready_revision_ids":already_ready}
            bundle = json.dumps({"base": expected_base_commit_id, "revisions": ids}, sort_keys=True)
            commit_id = f"{datetime.now(timezone.utc).date().isoformat()}_{run_id}_{uuid.uuid5(uuid.NAMESPACE_OID,bundle).hex[:12]}"
            db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES(?,?,?,?,?,?,?,?)", (run_id, str(datetime.now(timezone.utc).date()), "COMMITTED", "PASS", 0, now, now, "2.0.0"))
            db.execute("INSERT INTO commit_batches(commit_id,run_id,base_commit_id,bundle_hash,schema_version,started_at,committed_at,product_count,observation_count,price_event_count,event_count,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (commit_id,run_id,expected_base_commit_id,uuid.uuid5(uuid.NAMESPACE_OID,bundle).hex,"2.0.0",now,now,len({p[1] for p in plans}),0,0,len(plans),"COMMITTED"))
            for row, sku, field, field_hash in plans:
                values={"official_sku":sku,"language":"zh",field:row[1],f"{field}_source":"REGISTRY_APPROVED",f"{field}_review_status":"APPROVED",f"{field}_freshness_status":"CURRENT",f"{field}_source_hash":field_hash,f"{field}_approved_by":str(row[3] or actor),f"{field}_approved_at":str(row[4] or now),f"{field}_applied_commit_id":commit_id}
                sync_localization_field_provenance(db, values, commit_id=commit_id, now=now)
                db.execute("INSERT INTO translation_revision_events(event_id,revision_id,event_type,actor,evidence_json,occurred_at) VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()),row[0],"METADATA_REBOUND",actor,json.dumps({"commit_id":commit_id,"field":field,"field_source_hash":field_hash},sort_keys=True),now))
            db.commit()
        except Exception:
            db.rollback(); raise
    return {"status":"SUCCESS","commit_id":commit_id,"base_commit_id":expected_base_commit_id,"applied_fields":len(plans),"already_ready_revision_ids":already_ready,"revision_ids":list(ids)}
