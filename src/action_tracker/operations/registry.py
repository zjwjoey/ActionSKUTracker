"""Run-bound Registry recovery. Facts, approvals and translations are untouched."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..database.integration import database_path, latest_commit_id
from ..database.repository import ProductionRepository
from ..services.hashing import localization_source_hash
from ..services.runtime import RunLock
from ..services.normalization import normalize_official_text


def _status_path(cfg: Mapping[str, Any], source_run_id: str) -> Path:
    if not source_run_id or Path(source_run_id).name != source_run_id or any(c in source_run_id for c in '/\\:') or source_run_id in {'.', '..'}:
        raise ValueError('REGISTRY_RUN_ID_INVALID')
    return Path(cfg['project_root']) / 'runtime' / 'reports' / 'registry' / f'{source_run_id}.json'


def record_registry_status(cfg: Mapping[str, Any], *, source_run_id: str, fact_commit_id: str,
                           business_date: str, result: Mapping[str, Any], retry: bool = False) -> dict[str, Any]:
    path = _status_path(cfg, source_run_id)
    prior = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if prior and (prior.get('fact_commit_id') != fact_commit_id or prior.get('business_date') != business_date):
        raise ValueError('REGISTRY_RECOVERY_IDENTITY_MISMATCH')
    status = str(result.get('status') or 'PENDING')
    event = {'status': status, 'failure_reason': result.get('error'), 'retry': retry,
             'at': datetime.now(timezone.utc).isoformat()}
    payload = {**dict(result), 'source_run_id': source_run_id, 'fact_commit_id': fact_commit_id,
               'business_date': business_date, 'status': status, 'retryable': status in {'FAILED', 'PENDING'},
               'retry_attempts': int(prior.get('retry_attempts', 0)) + int(retry),
               'attempts': [*prior.get('attempts', []), event],
               'recovery_result': status if retry else 'NOT_ATTEMPTED'}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return payload


def committed_registry_records(cfg: Mapping[str, Any], *, source_run_id: str,
                               fact_commit_id: str, business_date: str) -> list[dict[str, Any]]:
    """Use frozen committed facts and refuse a retry against changed CURRENT."""
    path = database_path(cfg)
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT r.run_date,r.qa_state,r.dry_run,b.status FROM commit_batches b JOIN runs r ON r.run_id=b.run_id WHERE b.commit_id=? AND b.run_id=?',
                         (fact_commit_id, source_run_id)).fetchone()
        evidence_row = db.execute('SELECT evidence_json FROM run_evidence WHERE run_id=?', (source_run_id,)).fetchone()
        evidence = json.loads(evidence_row[0] or '{}') if evidence_row else {}
        partial = evidence.get('commit_scope') == 'POSITIVE_FACTS_ONLY'
        if not row or row[0] != business_date or (row[1] not in {'PASS', 'PASS_PRESENCE_ONLY'} and not partial) or row[2] or row[3] != 'COMMITTED':
            raise ValueError('REGISTRY_RECOVERY_COMMIT_NOT_VERIFIED')
        if partial:
            from ..qa.validator import validate_trusted_field_scope
            if not validate_trusted_field_scope(evidence.get('trusted_field_scope'), run_id=source_run_id, business_date=business_date):
                raise ValueError('REGISTRY_RECOVERY_FIELD_SCOPE_INVALID')
        field_evidence = evidence.get('fact_field_provenance') or {}
        historical = evidence.get('commit_scope') == 'HISTORICAL_FACTS_ONLY'
        if historical and evidence.get('operation') != 'HISTORICAL_FACT_IMPORT':
            raise ValueError('REGISTRY_RECOVERY_HISTORICAL_SCOPE_INVALID')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        fields = {'name': 'name_es', 'cat1': 'cat1_es', 'cat2': 'cat2_es', 'spec': 'spec_es', 'description': 'desc_es', 'details': 'details_es'}
        selected_skus = None
        if historical:
            selected_skus = sorted({item['sku'] for item in evidence.get('historical_committed_fields') or []})
        elif partial:
            # Keep every scoped identity, including rows without translation
            # fields; their existing missing-SKU guard must still run.
            selected_skus = sorted({item['sku'] for item in evidence['trusted_field_scope']['rows']})
        frozen: dict[str, dict[str, Any]] = {}
        if historical:
            pass  # Historical exact fields are read from the committed receipt below.
        elif 'product_fact_versions' in tables:
            bounded = ' AND official_sku IN (SELECT value FROM json_each(?))' if partial else ''
            args = (source_run_id, json.dumps(selected_skus)) if partial else (source_run_id,)
            for sku, payload in db.execute('SELECT official_sku,normalized_fact_json FROM product_fact_versions WHERE run_id=?' + bounded, args):
                value = json.loads(payload)
                record = {'sku': str(sku), **{key: value.get(field) for field, key in fields.items()}}
                if str(sku) in frozen and frozen[str(sku)] != record:
                    raise ValueError('REGISTRY_RECOVERY_SOURCE_AMBIGUOUS')
                frozen[str(sku)] = record
        else:
            bounded = ' AND official_sku IN (SELECT value FROM json_each(?))' if partial else ''
            args = (source_run_id, json.dumps(selected_skus)) if partial else (source_run_id,)
            for sku, field, value in db.execute('SELECT official_sku,field_name,normalized_value FROM source_fact_versions WHERE run_id=?' + bounded, args):
                if field in fields:
                    frozen.setdefault(str(sku), {'sku': str(sku)})[fields[field]] = value
    repository = ProductionRepository(path)
    current = (repository.load_localization_source_records(selected_skus, include_non_current=True)
               if partial or historical else repository.load_current_export_records())
    current_by_sku = {str(r['sku']): r for r in current}
    if historical:
        from ..data_quality.historical.variance import typed_hash
        allowed = {}
        for item in evidence.get('historical_committed_fields') or []:
            sku, field = item['sku'], item['field']
            if field not in fields or sku not in current_by_sku or typed_hash(current_by_sku[sku].get(fields[field])) != item['value_hash']:
                raise ValueError('REGISTRY_RECOVERY_HISTORICAL_SOURCE_CHANGED')
            allowed.setdefault(sku, []).append(fields[field])
        return [{**current_by_sku[sku], '_registry_allowed_fields': sorted(keys),
                 '_registry_observed_at': 'UNKNOWN'} for sku, keys in sorted(allowed.items())]
    if partial:
        from ..localization.hashes import value_hash
        result = []
        for item in evidence['trusted_field_scope']['rows']:
            sku = item['sku']
            if sku not in current_by_sku:
                raise ValueError('REGISTRY_RECOVERY_FIELD_SKU_MISSING')
            for key, permission in item['fields'].items():
                if key not in fields.values():
                    continue
                if (key not in (frozen.get(sku) or {})
                        or value_hash(current_by_sku[sku].get(key)) != permission['value_hash']
                        or value_hash(frozen[sku].get(key)) != permission['value_hash']):
                    raise ValueError(f'REGISTRY_RECOVERY_SOURCE_CHANGED:{sku}:{key}')
            allowed = sorted(set(item['fields']) & set(fields.values()))
            if allowed:
                result.append({**current_by_sku[sku], '_registry_allowed_fields': allowed})
        return result
    if not frozen or set(frozen) != set(current_by_sku):
        raise ValueError('REGISTRY_RECOVERY_CURRENT_SKU_SET_CHANGED')
    for sku, source in frozen.items():
        for key in fields.values():
            if ((field_evidence.get(sku) or {}).get(key, {}).get('state') == 'SOURCE_MISSING'
                    and current_by_sku[sku].get(key) is None and source.get(key) == ''):
                # The legacy scalar store serialized missing None as ''. The
                # committed availability marker prevents inventing official empty.
                source[key] = None
        for field in ('spec', 'desc', 'details'):
            if (field_evidence.get(sku) or {}).get(f'{field}_es', {}).get('state') != 'HISTORY_RETAINED':
                source[f'{field}_es'] = normalize_official_text(source.get(f'{field}_es'), field=field)
        if localization_source_hash(source) != localization_source_hash(current_by_sku[sku]):
            raise ValueError(f'REGISTRY_RECOVERY_SOURCE_CHANGED:{sku}')
    return list(current_by_sku.values())


def _retry_registry_unlocked(cfg: Mapping[str, Any], *, source_run_id: str, fact_commit_id: str,
                             business_date: str) -> dict[str, Any]:
    """Internal Operations adapter: caller already owns daily-run.lock."""
    path = _status_path(cfg, source_run_id)
    if not path.exists():
        raise ValueError('REGISTRY_FAILURE_EVIDENCE_MISSING')
    prior = json.loads(path.read_text(encoding='utf-8'))
    if prior.get('fact_commit_id') != fact_commit_id or prior.get('business_date') != business_date:
        raise ValueError('REGISTRY_RECOVERY_IDENTITY_MISMATCH')
    if prior.get('status') == 'SUCCESS':
        return {**prior, 'recovery_result': 'ALREADY_READY'}
    if prior.get('status') not in {'FAILED', 'PENDING'}:
        raise ValueError('REGISTRY_RETRY_ONLY_FAILED_RUN')
    try:
        expected_head = latest_commit_id(database_path(cfg))
        records = committed_registry_records(cfg, source_run_id=source_run_id, fact_commit_id=fact_commit_id, business_date=business_date)
        if latest_commit_id(database_path(cfg)) != expected_head:
            raise ValueError('BASELINE_CHANGED_BEFORE_REGISTRY_RETRY')
        from ..localization.registry.repository import LocalizationRegistry
        registry = LocalizationRegistry(database_path(cfg), role='PRIMARY')
        try:
            result = registry.ingest_records(records, source_run_id=source_run_id, observed_at=business_date)
        finally:
            registry.close()
        result = {**result, 'status': 'SUCCESS'}
    except Exception as exc:
        result = {'status': 'FAILED', 'error': f'{type(exc).__name__}:{exc}'}
    return record_registry_status(cfg, source_run_id=source_run_id, fact_commit_id=fact_commit_id,
                                  business_date=business_date, result=result, retry=True)


def retry_registry(cfg: Mapping[str, Any], *, source_run_id: str, fact_commit_id: str, business_date: str) -> dict[str, Any]:
    lock = RunLock(Path(cfg['paths']['state']))
    lock.acquire(source_run_id, command='registry-retry')
    try:
        return _retry_registry_unlocked(cfg, source_run_id=source_run_id, fact_commit_id=fact_commit_id, business_date=business_date)
    finally:
        lock.release()
