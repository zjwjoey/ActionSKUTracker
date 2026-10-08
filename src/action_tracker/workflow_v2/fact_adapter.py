"""Read a verified daily fact commit. V2 never substitutes its partial fact writer."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..database.connection import connect
from ..database.integration import database_path, latest_commit_id
from ..database.production import CommitBundle, ProductionWriter
from ..operations.registry import committed_registry_records


def load_committed_daily_source(cfg: Mapping[str, Any], *, business_date: str) -> dict[str, Any]:
    path = database_path(cfg)
    head = latest_commit_id(path)
    with connect(path) as db:
        row = db.execute('''SELECT b.commit_id,b.run_id,e.evidence_json FROM commit_batches b
            JOIN runs r ON r.run_id=b.run_id JOIN run_evidence e ON e.run_id=b.run_id
            WHERE b.status='COMMITTED' AND r.run_date=? AND r.dry_run=0
              AND r.qa_state IN ('PASS','PASS_PRESENCE_ONLY')
            ORDER BY b.committed_at DESC''', (business_date,)).fetchall()
        selected = None
        for candidate in row:
            evidence = json.loads(candidate['evidence_json'])
            if evidence.get('collection_metrics_hash'):
                selected = candidate, evidence
                break
        if selected is None:
            raise ValueError('WORKFLOW_V2_REQUIRES_COMMITTED_DAILY_FACTS')
        candidate, evidence = selected
        state = str(evidence.get('collection_quality_state') or '')
        if state != 'COLLECTION_OK':
            # Degraded collections require the same one-shot override as daily.
            from ..data_quality.collection.gates import validate_collection_override
            if state != 'COLLECTION_DEGRADED' or not evidence.get('collection_quality_override') or not validate_collection_override(
                evidence.get('collection_quality_override_evidence') or {}, run_id=candidate['run_id'], metrics_hash=evidence['collection_metrics_hash']):
                raise ValueError('WORKFLOW_V2_DAILY_COLLECTION_NOT_READY')
        bundle = CommitBundle(run_id=candidate['run_id'], observation_date=business_date, qa_state='PASS',
                              run_record=evidence, collection_quality_state=state, requires_collection_integrity=True)
        ProductionWriter._validate_collection_quality_evidence(db, bundle)
        events = db.execute('SELECT official_sku,event_type FROM event_history WHERE run_id=?', (candidate['run_id'],)).fetchall()
    records = committed_registry_records(cfg, source_run_id=candidate['run_id'], fact_commit_id=candidate['commit_id'], business_date=business_date)
    if latest_commit_id(path) != head:
        raise ValueError('BASELINE_CHANGED_BEFORE_WORKFLOW')
    return {'records': records, 'business_date': business_date, 'fact_commit_id': candidate['commit_id'],
            'primary_head': head, 'collection_run_id': candidate['run_id'], 'extraction_run_id': candidate['run_id'],
            'authoritative_skus': [str(r['sku']) for r in records],
            'expected_new_skus': [str(r[0]) for r in events if r[1] in {'NEW', 'FIRST_SEEN'}],
            'expected_reappeared_skus': [str(r[0]) for r in events if r[1] == 'REAPPEARED']}
