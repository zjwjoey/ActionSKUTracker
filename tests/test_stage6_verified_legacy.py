"""CI_SAFE: generated historical artifacts, read-only evidence validation."""
import csv
import hashlib
import json
from copy import deepcopy

import pytest

from action_tracker.stage5.source_candidate_v2 import candidate_source_provenance
from action_tracker.stage6.preview import preview_one, preview_hash


def fixture(tmp_path, suffix='.json'):
    source = {'name': 'Producto', 'description': 'Para casa', 'details': 'Material: Plástico'}
    candidate = {'candidate_id': 'c1', 'sku': '1001', 'source_field': 'description',
                 'parsed_candidate': '用于家庭', 'provenance_type': 'LEGACY_ARTIFACT',
                 **candidate_source_provenance(source, 'description')}
    row = dict(sku='1001', field='description', candidate_id='c1',
        source_spanish_value='Para casa', source_hash=candidate['source_hash'], reviewed_value='用于家庭',
        reviewed_at='2026-01-01T00:00:00+00:00', reviewed_by='human:fixture',
        review_decision='ACCEPT', owner_decision='ACCEPT', current_target_value='旧中文')
    def write(name):
        p = tmp_path/(name+suffix)
        if suffix == '.json':
            p.write_text(json.dumps({'entries': [row]}, ensure_ascii=False), encoding='utf8')
        else:
            with p.open('w', encoding='utf8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader(); writer.writerow(row)
        return str(p), hashlib.sha256(p.read_bytes()).hexdigest()
    artifact, ah = write('review'); approval, oh = write('owner')
    candidate['legacy_provenance'] = dict(provenance_type='LEGACY_ARTIFACT', sku='1001', field='description',
        artifact_path=artifact, artifact_sha256=ah, owner_approval_artifact=approval, owner_approval_sha256=oh)
    owner = dict(sku='1001', field='description', candidate_id='c1', owner_final_candidate='用于家庭',
        owner_disposition='ACCEPT_AS_IS', owner_reviewer='human:fixture', owner_reviewed_at='2026-01-01')
    return owner, candidate, source, {'desc_zh': '旧中文'}


def preview(args):
    return preview_one(*args, policy_hash='p1', source_snapshot_path='historical-reference')


@pytest.mark.parametrize('suffix', ['.json', '.csv'])
def test_valid_historical_evidence_is_reusable_but_never_grants_current_approval(tmp_path, suffix):
    args = fixture(tmp_path, suffix)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    a = preview(args); b = preview(args)
    assert a['conflict_reason'] == ['LEGACY_REAPPROVAL_REQUIRED']
    assert a['apply_action'] == 'BLOCKED_CONFLICT' and a['conflict_status'] == 'DO_NOT_APPLY'
    assert preview_hash([a]) == preview_hash([b])
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('part,key,value,reason', [
    (1, 'parsed_candidate', '错误候选', 'CANDIDATE_VALUE_MISMATCH'),
    (1, 'candidate_id', 'c2', 'FIELD_MISMATCH'),
    (1, 'source_hash', '0'*64, 'SOURCE_HASH_MISMATCH'),
    (1, 'provenance_type', 'UNRECOGNIZED', 'UNSUPPORTED_PROVENANCE_TYPE'),
    (2, 'description', 'Otro texto', 'SOURCE_CHANGED'),
    (3, 'desc_zh', '新中文', 'TARGET_CHANGED'),
])
def test_identity_source_value_and_target_changes_are_not_trusted(tmp_path, part, key, value, reason):
    args = fixture(tmp_path); args[part][key] = value
    result = preview(args)
    assert result['apply_action'] == 'BLOCKED_CONFLICT'
    assert reason in result['conflict_reason']


@pytest.mark.parametrize('change', ['missing', 'wrong_hash', 'invalid_json', 'duplicate', 'other_sku',
                                    'other_field', 'future_time', 'no_reviewer', 'rejected', 'different_value'])
def test_actual_artifact_bytes_and_exact_review_rows_are_required(tmp_path, change):
    args = fixture(tmp_path); provenance = args[1]['legacy_provenance']
    path = tmp_path/'review.json'
    row = json.loads(path.read_text(encoding='utf8'))['entries'][0]
    if change == 'missing':
        provenance['artifact_path'] = str(tmp_path/'missing.json')
    elif change == 'wrong_hash':
        provenance['artifact_sha256'] = '0'*64
    elif change == 'invalid_json':
        path.write_text('not json', encoding='utf8')
    else:
        changes = {'other_sku': ('sku', '2002'), 'other_field': ('field', 'name'),
            'future_time': ('reviewed_at', '2999-01-01T00:00:00+00:00'),
            'no_reviewer': ('reviewed_by', ''), 'rejected': ('review_decision', 'REJECT'),
            'different_value': ('reviewed_value', '另一候选')}
        if change in changes:
            key, value = changes[change]; row[key] = value
        path.write_text(json.dumps({'entries': [row, deepcopy(row)] if change == 'duplicate' else [row]}), encoding='utf8')
    if change not in {'wrong_hash', 'missing'}:
        provenance['artifact_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = preview(args)
    assert result['apply_action'] == 'BLOCKED_CONFLICT'
    assert set(result['conflict_reason']) - {'LEGACY_REAPPROVAL_REQUIRED'}


def test_legacy_claim_without_persisted_evidence_stays_blocked(tmp_path):
    args = fixture(tmp_path); args[1]['legacy_provenance'] = None
    assert 'MISSING_CRITICAL_EVIDENCE' in preview(args)['conflict_reason']


def test_owner_artifact_cannot_be_replaced_with_only_a_hash_claim(tmp_path):
    args = fixture(tmp_path); args[1]['legacy_provenance']['owner_approval_artifact'] = 'not-an-absolute-file'
    assert 'MISSING_CRITICAL_EVIDENCE' in preview(args)['conflict_reason']


def test_missing_official_source_is_still_terminal_no_source(tmp_path):
    args = fixture(tmp_path); args[2]['description'] = ''
    assert preview(args)['apply_action'] == 'NO_SOURCE'
