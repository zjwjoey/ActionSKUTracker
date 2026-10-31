"""Read-only historical evidence intake; never manufactures modern approval.

Adapted from stage6-provenance-v2 (9b729c2/e749099). Unlike that branch,
persisted artifact bytes and exact rows are checked, not just hash claims.
Verified historical evidence still requires the current approval workflow.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from ..services.hashing import field_source_hash

LEGACY_PROVENANCE_TYPE = 'LEGACY_ARTIFACT'
_SOURCES = {'name': 'name', 'cat1': 'cat1', 'cat2': 'cat2', 'spec': 'spec',
            'description': 'description', 'details': 'details'}


def _artifact_row(path, expected_hash, sku, field):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('ARTIFACT_FILE_UNAVAILABLE')
    if not re.fullmatch('[a-f0-9]{64}', str(expected_hash or '')):
        raise ValueError('ARTIFACT_HASH_INVALID')
    # Parse the exact bytes that were hashed, avoiding a second file read.
    payload = path.read_bytes()
    if len(payload) > 4 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError('ARTIFACT_HASH_CHANGED')
    text = payload.decode('utf-8-sig')
    if path.suffix.lower() == '.json':
        rows = json.loads(text)
        if isinstance(rows, dict):
            rows = rows.get('entries')
        if not isinstance(rows, list):
            raise ValueError('ARTIFACT_SCHEMA_INVALID')
    elif path.suffix.lower() == '.csv':
        import io
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('ARTIFACT_SCHEMA_INVALID')
        rows = list(reader)
    else:
        raise ValueError('ARTIFACT_FORMAT_UNSUPPORTED')
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError('ARTIFACT_SCHEMA_INVALID')
    selected = [row for row in rows if str(row.get('sku') or '') == sku and row.get('field') == field]
    if len(selected) != 1:
        raise ValueError('ARTIFACT_IDENTITY_AMBIGUOUS')
    return selected[0]


def legacy_conflicts(provenance, owner, candidate, source, target, *, field_target):
    """Audit existing evidence; even a valid historical approval stays pending."""
    conflicts = {'LEGACY_REAPPROVAL_REQUIRED'}
    if not isinstance(provenance, Mapping):
        return sorted(conflicts | {'MISSING_CRITICAL_EVIDENCE'})
    sku, field = str(owner.get('sku') or ''), owner.get('field')
    if (provenance.get('provenance_type') != LEGACY_PROVENANCE_TYPE
            or provenance.get('sku') != sku or provenance.get('field') != field
            or candidate.get('sku') != sku or candidate.get('source_field') != field):
        conflicts.add('FIELD_MISMATCH')
    try:
        historical = _artifact_row(provenance['artifact_path'], provenance['artifact_sha256'], sku, field)
        approval = _artifact_row(provenance['owner_approval_artifact'], provenance['owner_approval_sha256'], sku, field)
        expected = str((source or {}).get(_SOURCES.get(field, '')) or '').strip()
        if not expected:
            conflicts.add('NO_SOURCE')
        if historical.get('source_spanish_value') != expected or approval.get('source_spanish_value') != expected:
            conflicts.add('SOURCE_CHANGED')
        fields = {'name_es': (source or {}).get('name', ''), 'cat1_es': (source or {}).get('cat1', ''),
                  'cat2_es': (source or {}).get('cat2', ''), 'spec_es': (source or {}).get('spec', ''),
                  'desc_es': (source or {}).get('description', ''), 'details_es': (source or {}).get('details', '')}
        digest = field_source_hash(fields, field)
        if candidate.get('hash_scope') != 'field' or any(row.get('source_hash') != digest for row in (historical, approval, candidate)):
            conflicts.add('SOURCE_HASH_MISMATCH')
        value = candidate.get('parsed_candidate')
        if (not isinstance(value, str) or not value.strip() or value != historical.get('reviewed_value')
                or value != approval.get('reviewed_value') or value != owner.get('owner_final_candidate')):
            conflicts.add('CANDIDATE_VALUE_MISMATCH')
        if (not candidate.get('candidate_id') or any(row.get('candidate_id') != candidate.get('candidate_id')
                for row in (historical, approval))):
            conflicts.add('FIELD_MISMATCH')
        for row in (historical, approval):
            reviewed = datetime.fromisoformat(str(row.get('reviewed_at') or '').replace('Z', '+00:00'))
            if reviewed.utcoffset() is None or reviewed > datetime.now(timezone.utc) or not row.get('reviewed_by'):
                conflicts.add('MISSING_CRITICAL_EVIDENCE')
        if historical.get('review_decision') not in {'ACCEPT', 'ACCEPT_WITH_MINOR_EDIT'} or approval.get('owner_decision') != 'ACCEPT':
            conflicts.add('OWNER_NOT_APPROVED')
        if target is None or field_target not in target or approval.get('current_target_value') != target[field_target]:
            conflicts.add('TARGET_CHANGED')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        conflicts.add('MISSING_CRITICAL_EVIDENCE')
    return sorted(conflicts)
