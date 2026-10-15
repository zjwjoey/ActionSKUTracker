"""CI_SAFE: owned synthetic files only; no historical assets or cloud services."""
import copy
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from action_tracker.operations import cleanup_executor as e
from action_tracker.operations.cleanup_cli import main
from action_tracker.operations.storage_foundation import StorageError, sha256_file
from action_tracker.operations.storage_lifecycle import create_candidate


def fixture(tmp_path):
    protected = tmp_path / 'production'; protected.mkdir()
    state = tmp_path / 'executor'
    e.initialize_root(state, [protected])
    source = tmp_path / 'historical-fixture.txt'; source.write_text('synthetic bytes only', encoding='utf-8')
    backup = tmp_path / 'recovery.txt'; backup.write_bytes(source.read_bytes())
    sha = sha256_file(source)
    proof = tmp_path / 'restore-proof.json'
    proof.write_text(json.dumps({'status': 'PASS', 'backup_sha256': sha,
        'backup_path': str(backup), 'integrity': 'PASS', 'foreign_keys': 'PASS'}))
    domains = {}
    for domain in e.REQUIRED_REFERENCE_DOMAINS:
        root = tmp_path / domain; root.mkdir()
        (root / 'consumer.json').write_text('{"live": []}', encoding='utf-8')
        domains[domain] = root
    refs = e.capture_reference_scope(domains, reviewer='synthetic fixture owner', scope_reason='all consumers are owned fixtures')
    # Explicit test-only external attestation; production capture never marks scope complete.
    refs.update(complete=True, unresolved_dynamic_references=0, excluded_required_sources=0)
    candidate = {**e.identity(source), 'evidence_contract': 'LEGACY_CLEANUP_EVIDENCE_V1',
        'sha256': sha, 'owner_task': 'synthetic-cleanup-test', 'run_id': 'test-run',
        'identity_verified': True, 'unique_business_data': False, 'current_identity_check': 'PASS',
        'task_status': 'COMPLETED', 'task_terminal_verified': True,
        'task_evidence': {'resume_allowed': False}, 'current_integrity': 'PASS', 'current_foreign_keys': 'PASS',
        'recovery_required': False, 'recovery_release_verified': True, 'independent_rollback_obligation': False,
        'reference_count': 0, 'references_verified': True, 'production_dependency': False,
        'production_dependency_verified': True, 'retain_until': '2020-01-01T00:00:00+00:00',
        'retention_policy_verified': True,
        'alternative_recovery': {'path': str(backup), 'status': 'PASS', 'sha256': sha, 'evidence_path': str(proof)}}
    decisions = {
        'task': {'run_id': candidate['run_id'], 'database_path': str(source), 'state': 'SUCCESS',
                 'finished_at': e.now().isoformat(), 'resume_allowed': False},
        'retention': {'contract': 'RETENTION_DISPOSITION_V1', 'policy': 'owned synthetic fixture expires after test',
                      'retain_until': candidate['retain_until'], 'review_apply_obligations_released': True},
        'recovery': {'contract': 'RECOVERY_RELEASE_V1', 'recovery_required': False,
                     'independent_rollback_obligation': False, 'alternative_path': str(backup), 'alternative_sha256': sha}}
    bindings = {}
    for kind, document in decisions.items():
        document.update({key: candidate[key] for key in ('path', 'file_id', 'sha256', 'owner_task', 'run_id')})
        document.update(reviewer='synthetic fixture owner', reason='new owned synthetic fixture only', reviewed_at=e.now().isoformat())
        document_path = tmp_path / (kind + '-decision.json')
        document_path.write_text(json.dumps(document), encoding='utf-8')
        bindings[kind] = {**e.identity(document_path), 'sha256': sha256_file(document_path)}
    candidate['closure_documents'] = bindings
    context = {'state_root': str(state), 'protected_roots': [str(protected)], 'active_task_markers': [str(tmp_path / 'daily-run.lock')]}
    return candidate, refs, context


def approve(plan):
    request = e.approval_request(plan)
    assert request['decision'] == 'PENDING'
    return {**request, 'decision': 'APPROVED', 'approver': 'test fixture only',
            'authorization_source': 'USER_AUTHORIZED_SYNTHETIC_TEST_NOT_HISTORICAL',
            'approved_at': e.now().isoformat(), 'expires_at': (e.now() + timedelta(hours=1)).isoformat()}


def run(plan):
    approval = approve(plan)
    return e.execute(plan, approval, approval_sha256=e.digest(approval), enabled=True)


def observe(plan, tmp_path):
    evidence = tmp_path / ('observation-' + plan['plan_id'] + '.txt')
    evidence.write_text('synthetic quarantine checked; recovery bytes verified', encoding='utf-8')
    return {'status': 'PASS', 'plan_sha256': plan['record_sha256'], 'references_or_recovery_problems': False,
        'reviewer': 'synthetic fixture owner', 'evidence_paths': [str(evidence)], 'reviewed_at': e.now().isoformat()}


def test_full_real_file_quarantine_rollback_and_purge(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    first = e.prepare_plan([candidate], refs, context, observation_seconds=0)
    result = run(first)
    assert result['results'][0]['state'] == 'QUARANTINED' and result['logical_deleted_bytes'] == 0
    assert not Path(candidate['path']).exists()
    assert run(first)['results'][0]['reused']
    back = e.transition_plan(first, action='ROLLBACK')
    run(back)
    assert sha256_file(candidate['path']) == candidate['sha256']
    second = e.prepare_plan([candidate], refs, context, observation_seconds=0)
    run(second)
    purge = e.transition_plan(second, action='PURGE', observation=observe(second, tmp_path))
    outcome = run(purge)
    assert outcome['logical_deleted_bytes'] == candidate['size_bytes']
    assert not Path(second['items'][0]['quarantine_path']).exists()
    assert Path(candidate['alternative_recovery']['path']).is_file()
    assert run(purge)['logical_deleted_bytes'] == 0
    assert 'observed_volume_free_delta_bytes' in outcome['results'][0]


def test_execution_is_disabled_by_default(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    approval = approve(plan)
    with pytest.raises(StorageError, match='EXECUTION_DISABLED'):
        e.execute(plan, approval, approval_sha256=e.digest(approval))
    assert Path(candidate['path']).exists()


@pytest.mark.parametrize('change', ['hash', 'identity', 'reference', 'new-file', 'active', 'sidecar', 'recovery'])
def test_execution_rechecks_changed_evidence(tmp_path, change):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    source = Path(candidate['path'])
    if change == 'hash': source.write_text('different')
    elif change == 'identity':
        saved = source.with_suffix('.save'); source.rename(saved); source.write_bytes(saved.read_bytes())
    elif change == 'reference':
        Path(refs['domains']['configuration'], 'consumer.json').write_text(json.dumps({'database': str(source)}))
    elif change == 'new-file': Path(refs['domains']['active_tasks'], 'new.json').write_text('{}')
    elif change == 'active': Path(context['active_task_markers'][0]).write_text('RUNNING')
    elif change == 'sidecar': Path(str(source) + '-wal').write_text('live')
    else: Path(candidate['alternative_recovery']['path']).write_text('lost recovery')
    with pytest.raises(StorageError): run(plan)
    assert source.exists()


@pytest.mark.parametrize('field,value', [('decision', 'PENDING'), ('approver', None), ('action', 'PURGE'),
    ('plan_sha256', 'a'*64), ('asset_ids', []), ('expires_at', '2020-01-01T00:00:00+00:00')])
def test_unapproved_scope_or_expiry_blocks(tmp_path, field, value):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    approval = {**approve(plan), field: value}
    with pytest.raises(StorageError): e.execute(plan, approval, approval_sha256=e.digest(approval), enabled=True)
    assert Path(candidate['path']).exists()


def test_plan_approval_tamper_and_unknown_destination_block(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    approval = approve(plan)
    with pytest.raises(StorageError, match='DIGEST'):
        e.execute(plan, approval, approval_sha256='a'*64, enabled=True)
    changed = copy.deepcopy(plan); changed['items'][0]['path'] = 'different'
    with pytest.raises(StorageError, match='RECORD_CHANGED'): run(changed)
    Path(plan['items'][0]['quarantine_path']).write_text('unknown')
    with pytest.raises(StorageError, match='TARGET_ALREADY_EXISTS'): run(plan)


def test_hardlink_and_production_boundary_and_incomplete_refs(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    alias = tmp_path / 'alias'; alias.hardlink_to(candidate['path'])
    with pytest.raises(StorageError, match='SINGLE_LINK'): e.prepare_plan([candidate], refs, context)
    alias.unlink()
    with pytest.raises(StorageError, match='PROTECTED'):
        e.prepare_plan([candidate], refs, {**context, 'protected_roots': [str(tmp_path)]})
    with pytest.raises(StorageError, match='CLOSURE_UNPROVEN'):
        e.prepare_plan([candidate], {**refs, 'complete': False}, context)


def test_symlink_directory_alias_is_blocked(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    link = tmp_path / 'alias-dir'
    try: link.symlink_to(Path(candidate['path']).parent, target_is_directory=True)
    except OSError: pytest.skip('host does not permit test symlinks')
    with pytest.raises(StorageError, match='SYMLINK'):
        e.identity(link / Path(candidate['path']).name)


def test_observation_and_second_approval_are_mandatory(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    run(plan)
    with pytest.raises(StorageError, match='OBSERVATION_REQUIRED'): e.transition_plan(plan, action='PURGE')
    with pytest.raises(StorageError, match='NOT_ELAPSED'):
        e.transition_plan(plan, action='PURGE', observation=observe(plan, tmp_path))


def test_rollback_restores_when_new_consumer_appears(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    run(plan)
    Path(refs['domains']['configuration'], 'consumer.json').write_text(json.dumps({'file': candidate['path']}))
    run(e.transition_plan(plan, action='ROLLBACK'))
    assert Path(candidate['path']).is_file()


def test_archive_resolver_and_consumer_verification_never_move_source(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    archive = tmp_path / 'archive'
    create_candidate(archive, protected_roots=[Path(context['protected_roots'][0])])
    item = {'original_path': candidate['path'], 'sha256': candidate['sha256'], 'size_bytes': candidate['size_bytes'],
        'root_id': 'archive', 'relative_path': 'sample.txt', 'run_id': 'synthetic', 'asset_type': 'TEST_FIXTURE'}
    result = e.archive_assets([item], {'archive': str(archive)}, max_bytes=1000,
        consumer_check=lambda asset, resolved: {'status': 'PASS' if Path(resolved['path']).read_bytes() == Path(candidate['path']).read_bytes() else 'FAIL',
                                              'consumer': 'synthetic byte consumer'})
    assert result[0]['original_modified'] is False and Path(candidate['path']).is_file()
    assert Path(archive / 'sample.txt.archive.json').is_file()


def test_atomic_records_never_overwrite_and_cli_never_defaults_execute(tmp_path, capsys):
    target = tmp_path / 'immutable.json'
    e.atomic_new(target, {'one': 1})
    with pytest.raises(FileExistsError): e.atomic_new(target, {'two': 2})
    assert e.read_json(target) == {'one': 1}
    candidate, refs, context = fixture(tmp_path)
    candidates = tmp_path / 'candidates.json'; candidates.write_text(json.dumps([candidate]))
    assert main(['review', '--candidates', str(candidates)]) == 0
    assert 'CLEANUP_ELIGIBLE' in capsys.readouterr().out


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive handle behavior')
def test_windows_open_handle_blocks_mutation(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    with Path(candidate['path']).open('rb'):
        with pytest.raises(StorageError, match='IN_USE'): run(plan)
    assert Path(candidate['path']).is_file()


def test_interrupted_intent_does_not_repeat_mutation(tmp_path, monkeypatch):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    original = e._mutate
    monkeypatch.setattr(e, '_mutate', lambda *args, **kwargs: (_ for _ in ()).throw(StorageError('INTERRUPTED')))
    with pytest.raises(StorageError, match='INTERRUPTED'): run(plan)
    monkeypatch.setattr(e, '_mutate', original)
    with pytest.raises(StorageError, match='RECONCILIATION'): run(plan)
    assert Path(candidate['path']).is_file()


def test_owner_decision_is_present_day_and_never_fakes_historical_success(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    candidate['task_status'] = 'UNKNOWN'
    request = e.disposition_request(candidate)
    with pytest.raises(StorageError, match='EXPLICIT_OWNER'): e.record_disposition(request)
    decision = e.record_disposition({**request, 'decision': 'ARCHIVE_REQUIRED',
        'owner': 'synthetic fixture owner', 'decided_at': e.now().isoformat(),
        'reason': 'retain recovery fixture', 'resume_allowed': False,
        'recovery_responsibility': 'retain independent original', 'retention_policy': 'keep indefinitely'})
    assert decision['historical_task_status'] == 'UNKNOWN'
    assert decision['historical_success_asserted'] is False
    assert decision['cleanup_eligibility_granted'] is False


def test_live_reference_without_inventory_change_is_not_zero_reference(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    root = Path(refs['domains']['recovery_lists'])
    (root / 'consumer.json').write_text(json.dumps({'recover': candidate['path']}))
    refs['inventories']['recovery_lists'] = e.inventory(root)
    with pytest.raises(StorageError, match='LIVE_REFERENCE_PRESENT'):
        e.prepare_plan([candidate], refs, context)


def test_observation_evidence_is_hash_bound_until_purge(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context, observation_seconds=0)
    run(plan)
    observed = observe(plan, tmp_path)
    purge = e.transition_plan(plan, action='PURGE', observation=observed)
    Path(observed['evidence_paths'][0]).write_text('changed after review')
    with pytest.raises(StorageError, match='OBSERVATION_EVIDENCE'): run(purge)
    assert Path(plan['items'][0]['quarantine_path']).is_file()


def test_candidate_review_never_promotes_untagged_p2_record(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    candidate.pop('evidence_contract')
    result = e.review_candidates([candidate])
    assert result['assets'][0]['state'] == 'UNKNOWN_BLOCKED'
    assert not result['whitelist'] and result['estimated_releasable_bytes'] == 0


@pytest.mark.parametrize('name', ['records.sqlite3', 'records.json'])
def test_reference_domain_cannot_be_a_file_disguised_as_empty_directory(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b'not a consumer directory')
    with pytest.raises(StorageError, match='REFERENCE_DIRECTORY_REQUIRED'): e.inventory(path)


def test_early_observation_does_not_become_valid_when_wall_clock_advances(tmp_path, monkeypatch):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context, observation_seconds=60)
    run(plan)
    observation = observe(plan, tmp_path)
    later = e.now() + timedelta(seconds=65)
    monkeypatch.setattr(e, 'now', lambda: later)
    with pytest.raises(StorageError, match='INVALID_OBSERVATION_TIMESTAMP'):
        e.transition_plan(plan, action='PURGE', observation=observation)


def test_alternative_cannot_change_during_last_candidate_hash(tmp_path, monkeypatch):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    original = e._mutate
    def delayed_mutation(*args, **kwargs):
        Path(candidate['alternative_recovery']['path']).write_text('recovery changed after initial validation')
        return original(*args, **kwargs)
    monkeypatch.setattr(e, '_mutate', delayed_mutation)
    with pytest.raises(StorageError, match='RECOVERY_CHANGED_BEFORE_MUTATION'): run(plan)
    assert Path(candidate['path']).is_file()


def test_certificate_boolean_fields_do_not_replace_bound_owner_documents(tmp_path):
    candidate, refs, context = fixture(tmp_path)
    candidate.pop('closure_documents')
    with pytest.raises(StorageError, match='BOUND_CLOSURE_DOCUMENT'): e.prepare_plan([candidate], refs, context)


def test_partial_batch_failure_retains_first_completed_receipt(tmp_path):
    a = tmp_path / 'a'; a.mkdir()
    b = tmp_path / 'b'; b.mkdir()
    first, refs, context = fixture(a)
    second, other_refs, other_context = fixture(b)
    plan = e.prepare_plan([first, second], refs, context)
    Path(second['path']).write_text('changed only after batch planning')
    with pytest.raises(e.CleanupExecutionError) as error: run(plan)
    assert error.value.operation_state['status'] == 'PARTIAL'
    assert len(error.value.operation_state['completed_items']) == 1
    assert not Path(first['path']).exists() and Path(second['path']).exists()
    first_id = plan['items'][0]['asset_id']
    with pytest.raises(StorageError, match='TRANSITION_SCOPE_INVALID'):
        e.transition_plan(plan, action='ROLLBACK', asset_ids=['unknown'])
    rollback = e.transition_plan(plan, action='ROLLBACK', asset_ids=[first_id])
    assert len(rollback['items']) == 1 and rollback['logical_bytes'] == first['size_bytes']
    assert rollback['previous_plan_sha256'] == plan['record_sha256']
    run(rollback)
    assert sha256_file(first['path']) == first['sha256']
    assert Path(second['path']).read_text() == 'changed only after batch planning'


def test_cli_existing_output_blocks_before_any_mutation(tmp_path, capsys):
    candidate, refs, context = fixture(tmp_path)
    plan = e.prepare_plan([candidate], refs, context)
    approval = approve(plan)
    plan_file = tmp_path / 'plan.json'; e.atomic_new(plan_file, plan)
    approval_file = tmp_path / 'approval.json'; e.atomic_new(approval_file, approval)
    output = tmp_path / 'existing.json'; output.write_text('unknown')
    assert main(['execute', '--plan', str(plan_file), '--approval', str(approval_file),
        '--approval-sha256', e.digest(approval), '--out', str(output), '--enable-execution']) == 2
    assert 'OUTPUT_ALREADY_EXISTS' in capsys.readouterr().out
    assert Path(candidate['path']).exists() and output.read_text() == 'unknown'
