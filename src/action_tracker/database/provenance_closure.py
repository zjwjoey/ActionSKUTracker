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
