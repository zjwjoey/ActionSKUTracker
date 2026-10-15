"""Lifecycle for NEW P2-owned CORE workspaces; never adopts legacy assets.

Backup/restore use the existing V3 operations. Disposition reuses Cleanup V1's
exclusive-handle mutation primitive, with a distinct preapproved new-workspace
gate. Old P2.1 qualification and approval semantics are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import re
import os
import shutil
import uuid
import time

from .backup_sets import (IDENTIFIER, digest, file_identity, now, read_json, safe_path,
                          seal, validate_seal, verify_restored, write_immutable)
from . import backup_operations as op
from . import cleanup_executor as cleanup
from .backup_capture import capture_core
from .storage_foundation import StorageError, sha256_file, storage_lock
from .storage_lifecycle import ScratchTask, require_candidate


@dataclass(frozen=True)
class CaptureDispositionPolicy:
    enabled: bool = False
    minimum_retention_seconds: int = 86400
    quarantine_seconds: int = 86400
    keep_latest_capture: bool = True
    independent_rollback_required: bool = True

    def __post_init__(self):
        for key in ('enabled', 'keep_latest_capture', 'independent_rollback_required'):
            if type(getattr(self, key)) is not bool:
                raise StorageError('CAPTURE_POLICY_BOOLEAN_REQUIRED')
        for key in ('minimum_retention_seconds', 'quarantine_seconds'):
            if type(getattr(self, key)) is not int or not 0 <= getattr(self, key) <= 10 * 365 * 86400:
                raise StorageError('CAPTURE_POLICY_RETENTION_INVALID')


def _directory(operations, run_id):
    if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
        raise StorageError('BACKUP_ID_INVALID')
    return safe_path(Path(operations) / 'capture-lifecycle' / run_id, file=False)


def _scope(raw, backend):
    return digest({'capture': raw['capture'], 'protected_roots': raw['protected_roots'],
                   'writer_lock_directory': raw['writer_lock_directory'],
                   'repository': op.public_backend_identity(backend),
                   'backend_configuration': raw.get('backend', {}),
                   'operation_policy': asdict(op.OperationPolicy(**raw.get('policy', {}))),
                   'operations_policy': raw.get('operations_policy'),
                   'local_password_dpapi_path': raw.get('local_password_dpapi_path'),
                   'operations_root': str(Path(raw['operations_root']).resolve())})


def _shared_lock(raw):
    if os.name == 'nt' and Path(raw['capture']['source']).resolve() == Path('F:/ActionSKUTracker/runtime/db/action_tracker.db').resolve():
        if Path(raw['writer_lock_directory']).resolve() != Path('F:/ActionSKUTracker/runtime/state').resolve():
            raise StorageError('PRODUCTION_CAPTURE_SHARED_WRITER_LOCK_MISMATCH')


def _protect_root(raw, root):
    root = safe_path(root, file=False)
    fixed = ['F:/ActionSKUTracker', 'F:/ActionSKUTracker_production'] if os.name == 'nt' else []
    context = {'protected_roots': [*raw['protected_roots'], *fixed]}
    cleanup._protect(root, context)
    for protected in raw['protected_roots']:
        if safe_path(Path(protected), file=False).is_relative_to(root):
            raise StorageError('CAPTURE_PROTECTED_ROOT_OVERLAP')
    return root


def manifest(operations, run_id):
    directory = _directory(operations, run_id)
    value = validate_seal(read_json(directory / 'manifest.json'), 'CAPTURE_LIFECYCLE_V1')
    if value['run_id'] != run_id or value['operations_root'] != str(Path(operations).resolve()):
        raise StorageError('CAPTURE_MANIFEST_SCOPE_MISMATCH')
    root = safe_path(Path(operations) / 'capture-workspaces', file=False)
    require_candidate(root)
    marker = read_json(root / '.storage_candidate.json')
    if value['root_token'] != marker['owner_token']:
        raise StorageError('CAPTURE_ROOT_OWNERSHIP_CHANGED')
    workspace = safe_path(Path(value['workspace']), file=False)
    if workspace.parent != root or workspace.name != 'task-' + value['scratch_token']:
        raise StorageError('CAPTURE_WORKSPACE_BOUNDARY_INVALID')
    task = ScratchTask.resume(root, workspace, owner_task=value['owner_task'], run_id=run_id)
    if task.token != value['scratch_token']:
        raise StorageError('CAPTURE_SCRATCH_OWNERSHIP_CHANGED')
    return value


def owns_capture(operations, run_id, source):
    path = _directory(operations, run_id) / 'manifest.json'
    if not path.exists():
        return False
    value = manifest(operations, run_id)
    return safe_path(Path(source), file=False) == Path(value['workspace']) / 'capture'


def events(operations, run_id):
    return op.job_events(_directory(operations, run_id))


def _event(directory, state, **facts):
    return op._event(directory, state, **facts)


def _state(directory):
    rows = op.job_events(directory)
    return rows[-1]['status'] if rows else 'CREATED'


def _usage(root):
    total = 0
    if root.is_dir():
        for path in root.rglob('*'):
            checked = safe_path(path, file=path.is_file())
            if checked.is_file():
                total += checked.stat().st_size
    return total


def retired_intact(value):
    workspace = Path(value['workspace'])
    quarantine = _directory(value['operations_root'], value['run_id']) / 'quarantine'
    return ({p for p in workspace.rglob('*') if p.is_file()} ==
            {workspace / 'owner.json', workspace / 'lifecycle.jsonl'} and
            quarantine.is_dir() and not any(quarantine.iterdir()))


def _admission(raw, run_id):
    operations = Path(raw['operations_root'])
    legacy = safe_path(operations / 'captures', file=False)
    if legacy.exists() and any(legacy.iterdir()):
        raise StorageError('LEGACY_CAPTURE_REQUIRES_SEPARATE_DISPOSITION')
    registry = safe_path(operations / 'capture-lifecycle', file=False)
    if registry.exists():
        for child in registry.iterdir():
            if child.name == run_id:
                continue
            manifest(operations, child.name)
            if _state(child) != 'RETIRED':
                raise StorageError('PRIOR_MANAGED_CAPTURE_NOT_RETIRED')
            if not retired_intact(manifest(operations, child.name)):
                raise StorageError('RETIRED_CAPTURE_WORKSPACE_CHANGED')


def _drain_previous(raw, backend, run_id, grant):
    """Re-evaluate a finished prior task under an existing scoped policy grant.

    This is not a waiver: incomplete restores, retention, references or expiry
    still block admission. No new capture starts until prior retirement passes.
    """
    operations = Path(raw['operations_root'])
    legacy = safe_path(operations / 'captures', file=False)
    if legacy.exists() and any(legacy.iterdir()):
        raise StorageError('LEGACY_CAPTURE_REQUIRES_SEPARATE_DISPOSITION')
    for directory in (operations / 'capture-lifecycle').glob('*'):
        if directory.name == run_id or _state(directory) == 'RETIRED':
            continue
        value = manifest(operations, directory.name)
        if value['source_configuration_sha256'] != _scope(raw, backend):
            raise StorageError('PRIOR_CAPTURE_CONFIGURATION_REVIEW_REQUIRED')
        if not (directory / 'restore.json').exists() or not (directory / 'plan.json').exists():
            raise StorageError('PRIOR_MANAGED_CAPTURE_RESTORE_INCOMPLETE')
        with storage_lock(directory, run_id=directory.name):
            try:
                _retire(raw, backend, value, grant)
            except Exception as exc:
                _event(directory, 'BLOCKED', reason=str(exc) if isinstance(exc, StorageError) else 'CAPTURE_DISPOSITION_FAILED')
                raise


def _create(raw, backend, run_id, budget):
    operations = _protect_root(raw, Path(raw['operations_root']))
    directory = _directory(operations, run_id)
    if directory.exists():
        value = manifest(operations, run_id)
        if value['source_configuration_sha256'] != _scope(raw, backend):
            raise StorageError('CAPTURE_CONFIGURATION_CHANGED')
        return value
    workspace_root = _protect_root(raw, operations / 'capture-workspaces')
    if not workspace_root.exists():
        cleanup.initialize_root(workspace_root, raw['protected_roots'])
    require_candidate(workspace_root)
    for child in workspace_root.iterdir():
        if child.name in {'.storage_candidate.json', 'quarantine', 'receipts', 'locks'}:
            continue
        if not child.is_dir() or not child.name.startswith('task-'):
            raise StorageError('UNREGISTERED_CAPTURE_WORKSPACE_REVIEW_REQUIRED')
    # Never adopt an old capture. Only a crash before the first registration
    # may leave an owner-only workspace with the exact new P2 run identity.
    orphans = []
    registered = {read_json(p)['workspace'] for p in (operations / 'capture-lifecycle').glob('*/manifest.json')}
    for path in workspace_root.glob('task-*'):
        if str(path) in registered:
            continue
        owner = read_json(safe_path(path / 'owner.json'))
        if (owner.get('run_id') != run_id or owner.get('owner_task') != 'capture-lifecycle-v1' or
                {p.name for p in path.iterdir()} != {'owner.json'}):
            raise StorageError('UNREGISTERED_CAPTURE_WORKSPACE_REVIEW_REQUIRED')
        orphans.append(path)
    if len(orphans) > 1:
        raise StorageError('AMBIGUOUS_CAPTURE_WORKSPACE')
    if orphans:
        task = ScratchTask.resume(workspace_root, orphans[0], owner_task='capture-lifecycle-v1', run_id=run_id)
    else:
        task = ScratchTask(workspace_root, owner_task='capture-lifecycle-v1', run_id=run_id, budget_bytes=budget)
    from .kopia_connectivity import _private_workspace
    directory.parent.mkdir(parents=True, exist_ok=True)
    _private_workspace(directory)
    value = seal({'contract': 'CAPTURE_LIFECYCLE_V1', 'capture_id': task.token,
        'backup_set_id': run_id, 'owner_task': task.owner_task, 'run_id': run_id,
        'operations_root': str(operations), 'workspace': str(task.directory),
        'root_token': read_json(workspace_root / '.storage_candidate.json')['owner_token'],
        'scratch_token': task.token, 'created_at': now(), 'source_primary': raw['capture']['source'],
        'source_configuration_sha256': _scope(raw, backend),
        'repository': op.public_backend_identity(backend), 'protected_roots': raw['protected_roots'],
        'disposition_policy': asdict(CaptureDispositionPolicy(**raw.get('capture_lifecycle', {})))})
    write_immutable(directory / 'manifest.json', value)
    _event(directory, 'CREATED', manifest_sha256=value['document_sha256'])
    return value


def _register_workspace(value):
    workspace = Path(value['workspace'])
    task = ScratchTask.resume(workspace.parent, workspace, owner_task=value['owner_task'], run_id=value['run_id'])
    plan = read_json(_directory(value['operations_root'], value['run_id']) / 'plan.json')
    allowed = {workspace / 'capture' / a['relative_path'] for a in plan['assets']}
    allowed.add(workspace / 'capture' / 'primary.sqlite3.asset.json')
    allowed.update(workspace / 'restore' / a['snapshot_path'] for a in plan['assets'])
    allowed.add(workspace / 'restore' / 'backup-set.json')
    for parent in (workspace / 'capture', workspace / 'restore'):
        if not parent.exists():
            continue
        for path in parent.rglob('*'):
            checked = safe_path(path, file=path.is_file())
            if not checked.is_file():
                continue
            if checked not in allowed:
                raise StorageError('CAPTURE_UNEXPECTED_WORKSPACE_FILE')
            relative = checked.relative_to(workspace).as_posix()
            if checked not in task.assets:
                task.register(relative, source='OWNED_CAPTURE_LIFECYCLE', expected_bytes=checked.stat().st_size)
            elif task.assets[checked].get('sha256') is not None:
                if (sha256_file(checked) != task.assets[checked]['sha256'] or
                        task.assets[checked].get('capture_identity') != cleanup.identity(checked)):
                    raise StorageError('CAPTURE_REGISTERED_FILE_CHANGED')
                continue
            task.complete(relative)
            task.assets[checked]['capture_identity'] = cleanup.identity(checked)
            task._event(task.assets[checked], 'CAPTURE_IDENTITY_BOUND')


def _restore(raw, backend, value, receipt, operation_policy):
    directory = _directory(raw['operations_root'], value['run_id'])
    report_path = directory / 'restore.json'
    target = Path(value['workspace']) / 'restore'
    if report_path.exists():
        report = validate_seal(read_json(report_path), 'BACKUP_RESTORE_V1')
        if report['receipt_sha256'] != receipt['document_sha256']:
            raise StorageError('CAPTURE_RESTORE_RECEIPT_MISMATCH')
        verify_restored(receipt['plan'], target)
        return report
    if target.exists():
        # A complete download may have lost only its report. Verify all bytes
        # and business consistency; incomplete downloads are retained/blocked.
        op._repository_matches(backend, receipt['plan'])
        if backend.verify_snapshot(receipt['snapshot_id']).get('status') not in {'PASS', 'VERIFIED', 'SNAPSHOT_VERIFIED'}:
            raise StorageError('SNAPSHOT_VERIFICATION_FAILED')
        checked = verify_restored(receipt['plan'], target)
        report = seal({'contract': 'BACKUP_RESTORE_V1', **checked,
            'receipt_sha256': receipt['document_sha256'], 'repository': receipt['repository'],
            'snapshot_id': receipt['snapshot_id'], 'target': str(target),
            'metrics': {'download': {'status': 'UNKNOWN'}, 'resumed_existing_download': True}})
        write_immutable(report_path, report)
        return report
    if _usage(Path(raw['operations_root'])) + receipt['logical_bytes'] + 64 * 1024**2 > operation_policy.max_scratch_bytes:
        raise StorageError('CAPTURE_TOTAL_SCRATCH_BUDGET_EXCEEDED')
    return op.restore_backup(receipt, backend=backend, target=target, policy=operation_policy,
        protected_roots=[Path(p) for p in raw['protected_roots']] + [Path(raw['capture']['source']).parent],
        report_path=report_path)


def _runtime(raw, backend=None):
    deadline = raw.get('_capture_invocation_deadline')
    if deadline is not None and time.monotonic() >= deadline:
        raise StorageError('CAPTURE_TASK_TIME_BUDGET_EXCEEDED')
    if backend is not None and deadline is not None and hasattr(backend, 'set_deadline'):
        backend.set_deadline(deadline)


def cycle(raw, backend, run_id, *, grant=None, retire=False):
    runtime = dict(raw, _capture_invocation_deadline=time.monotonic() +
        op.OperationPolicy(**raw.get('policy', {})).max_runtime_seconds)
    previous = getattr(backend, '_deadline', None)
    try:
        return _cycle(runtime, backend, run_id, grant=grant, retire=retire)
    finally:
        if hasattr(backend, 'set_deadline'):
            backend.set_deadline(previous)


def _cycle(raw, backend, run_id, *, grant=None, retire=False):
    """The existing CORE path, with persistent ownership and a restore phase."""
    policy = CaptureDispositionPolicy(**raw.get('capture_lifecycle', {}))
    operation_policy = op.OperationPolicy(**raw.get('policy', {}))
    if not policy.enabled or not operation_policy.enabled:
        return {'status': 'DISABLED', 'side_effects': False}
    if backend.config.provider not in {'local', 'filesystem'}:
        raise StorageError('V31_CLOUD_GATE_BLOCKED')
    _shared_lock(raw)
    _directory(raw['operations_root'], run_id)
    _runtime(raw, backend)
    if Path(raw['operations_root'], 'jobs', run_id, 'cancel.json').exists():
        raise StorageError('BACKUP_CANCELLED')
    operations = _protect_root(raw, Path(raw['operations_root']))
    with storage_lock(operations / 'capture-admission', run_id=run_id):
        prior = _directory(operations, run_id)
        if (prior / 'manifest.json').exists() and _state(prior) == 'RETIRED':
            value = manifest(operations, run_id)
            if value['source_configuration_sha256'] != _scope(raw, backend):
                raise StorageError('CAPTURE_CONFIGURATION_CHANGED')
            if not retired_intact(value):
                raise StorageError('RETIRED_CAPTURE_WORKSPACE_CHANGED')
            return {'status': 'RETIRED', 'reused': True, 'run_id': run_id}
        if retire and grant is not None:
            _drain_previous(raw, backend, run_id, grant)
        _admission(raw, run_id)
        if not (prior / 'plan.json').exists():
            needed = (safe_path(Path(raw['capture']['source'])).stat().st_size +
                      sum(safe_path(Path(p)).stat().st_size for p in raw['capture']['projections'].values()))
            wal = Path(raw['capture']['source'] + '-wal')
            if wal.exists():
                needed += safe_path(wal).stat().st_size
            if _usage(operations) + needed * 3 + 64 * 1024**2 > operation_policy.max_scratch_bytes:
                raise StorageError('CAPTURE_TOTAL_SCRATCH_BUDGET_EXCEEDED')
            repository_budget = (raw.get('operations_policy') or {}).get('max_repository_bytes', 30 * 1024**3)
            if _usage(Path(backend.config.repository_path)) + needed * 2 + 16 * 1024**2 > repository_budget:
                raise StorageError('CAPTURE_REPOSITORY_BUDGET_EXCEEDED')
        value = _create(raw, backend, run_id, operation_policy.max_scratch_bytes)
        directory = _directory(operations, run_id)
        if _state(directory) == 'RETIRED':
            return {'status': 'RETIRED', 'reused': True, 'run_id': run_id}
        with storage_lock(directory, run_id=run_id):
            try:
                if (directory / 'disposition-plan.json').exists():
                    if not retire:
                        return {'status': 'DISPOSITION_PENDING', 'run_id': run_id}
                    return _retire(raw, backend, value, grant)
                plan_path = directory / 'plan.json'
                if not plan_path.exists():
                    capture = raw['capture']
                    plan, roots = capture_core(source=Path(capture['source']),
                        projections={k: Path(p) for k, p in capture['projections'].items()},
                        destination=Path(value['workspace']) / 'capture',
                        writer_lock_directory=Path(raw['writer_lock_directory']), run_id=run_id,
                        code_version=capture['code_version'], repository=op.public_backend_identity(backend),
                        config_version=raw.get('config_version', 'CAPTURE_V1'),
                        recovery_owner=capture['recovery_owner'], retention=capture['retention'],
                        max_bytes=operation_policy.max_scratch_bytes)
                    write_immutable(plan_path, plan)
                _runtime(raw, backend)
                plan = read_json(plan_path)
                _event(directory, 'P1_VERIFIED', plan_sha256=plan['document_sha256'],
                    database_identities=plan['database_identities'], logical_bytes=plan['logical_bytes'])
                _register_workspace(value)
                receipt = op.run_backup(plan, {'core': str(Path(value['workspace']) / 'capture')},
                    backend=backend, operations_root=operations,
                    writer_lock_directory=Path(raw['writer_lock_directory']), policy=operation_policy)
                _runtime(raw, backend)
                _event(directory, 'SNAPSHOT_COMMITTED', snapshot_id=receipt['snapshot_id'], receipt_sha256=receipt['document_sha256'])
                _event(directory, 'SNAPSHOT_VERIFIED', snapshot_id=receipt['snapshot_id'])
                restored = _restore(raw, backend, value, receipt, operation_policy)
                _runtime(raw, backend)
                _register_workspace(value)
                _event(directory, 'RESTORE_VERIFIED', restore_sha256=restored['document_sha256'])
                _event(directory, 'DISPOSITION_PENDING', task_success=True, resume_requires_workspace=False)
                if retire:
                    return _retire(raw, backend, value, grant)
                return {'status': 'DISPOSITION_PENDING', 'run_id': run_id, 'snapshot_id': receipt['snapshot_id']}
            except Exception as exc:
                reason = str(exc) if isinstance(exc, StorageError) else 'CAPTURE_OPERATION_FAILED'
                _event(directory, 'BLOCKED', reason=reason if re.fullmatch('[A-Z0-9_]{1,100}', reason) else 'CAPTURE_OPERATION_FAILED')
                raise


def _grant(raw, backend, value, grant):
    policy = CaptureDispositionPolicy(**raw.get('capture_lifecycle', {}))
    if not policy.enabled or not isinstance(grant, dict):
        raise StorageError('CAPTURE_DISPOSITION_APPROVAL_REQUIRED')
    allowed = {'contract', 'scope', 'approved_by', 'approval_id', 'instruction_reference',
        'expires_at', 'root_token', 'operations_root', 'source_configuration_sha256',
        'policy_sha256', 'reference_scope_complete', 'unresolved_dynamic_references',
        'excluded_required_sources',
        'reference_domains', 'activation_restore_report_path', 'activation_restore_report_sha256',
        'replaces_grant_sha256', 'allowed_actions', 'release_creation_policy_sha256'}
    if set(grant) - allowed or grant.get('allowed_actions') != ['QUARANTINE', 'PURGE']:
        raise StorageError('CAPTURE_DISPOSITION_APPROVAL_SCOPE_MISMATCH')
    try:
        expiry = datetime.fromisoformat(grant['expires_at'])
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise StorageError('CAPTURE_DISPOSITION_APPROVAL_EXPIRED') from None
    if (grant.get('contract') != 'CAPTURE_DISPOSITION_POLICY_V1' or
            grant.get('scope') != 'NEW_CAPTURE_WORKSPACES_ONLY' or
            grant.get('root_token') != value['root_token'] or
            grant.get('operations_root') != value['operations_root'] or
            grant.get('source_configuration_sha256') != _scope(raw, backend) or
            grant.get('policy_sha256') != digest(asdict(policy)) or
            not grant.get('approved_by') or not grant.get('approval_id') or
            not grant.get('instruction_reference') or grant.get('reference_scope_complete') is not True or
            grant.get('unresolved_dynamic_references') != 0 or
            type(grant.get('unresolved_dynamic_references')) is not int or
            grant.get('excluded_required_sources') != 0 or
            type(grant.get('excluded_required_sources')) is not int or
            set(grant.get('reference_domains', {})) != cleanup.REQUIRED_REFERENCE_DOMAINS):
        raise StorageError('CAPTURE_DISPOSITION_APPROVAL_SCOPE_MISMATCH')
    if (asdict(policy) != value['disposition_policy'] and
            grant.get('release_creation_policy_sha256') != digest(value['disposition_policy'])):
        raise StorageError('CAPTURE_CREATION_POLICY_CHANGED')
    activation = validate_seal(read_json(Path(grant.get('activation_restore_report_path', ''))), 'BACKUP_RESTORE_V1')
    activation_path = safe_path(Path(grant['activation_restore_report_path']))
    activation_manifest = manifest(raw['operations_root'], activation_path.parent.name)
    if (activation['document_sha256'] != grant.get('activation_restore_report_sha256') or
            activation.get('status') != 'ROUNDTRIP_PASS' or activation.get('repository') != value['repository'] or
            activation_path != _directory(raw['operations_root'], activation_path.parent.name) / 'restore.json' or
            activation_manifest['source_configuration_sha256'] != value['source_configuration_sha256']):
        raise StorageError('CAPTURE_ACTIVATION_RESTORE_PROOF_REQUIRED')
    return policy


def _references(grant, workspace, additional=()):
    refs = cleanup.capture_reference_scope(grant['reference_domains'], reviewer=grant['approved_by'],
        scope_reason='Approved closed consumer scope for newly owned capture workspaces only')
    # Coverage is an explicit external attestation bound by the policy grant;
    # the scanner itself never declares global coverage or invents zero hits.
    refs.update(complete=grant['reference_scope_complete'],
        unresolved_dynamic_references=grant['unresolved_dynamic_references'],
        excluded_required_sources=grant['excluded_required_sources'])
    cleanup.validate_references(refs, (workspace, *additional), max_age_seconds=3600)
    return refs


def _validate_disposition(value, plan):
    if plan.get('manifest_sha256') != value['document_sha256'] or not plan.get('items'):
        raise StorageError('CAPTURE_DISPOSITION_PLAN_BINDING_CHANGED')
    workspace = Path(value['workspace'])
    task = ScratchTask.resume(workspace.parent, workspace, owner_task=value['owner_task'], run_id=value['run_id'])
    seen = set()
    for item in plan['items']:
        path = safe_path(Path(item['path']), file=False)
        registered = task.assets.get(path)
        if (not any(path.is_relative_to(workspace / name) for name in ('capture', 'restore')) or
                path in seen or not registered or registered.get('sha256') != item.get('sha256') or
                registered.get('size_bytes') != item.get('size_bytes') or
                registered.get('capture_identity') != {k: v for k, v in item.items() if k != 'sha256'}):
            raise StorageError('CAPTURE_DISPOSITION_ITEM_OUTSIDE_REGISTRY')
        seen.add(path)


def _retire(raw, backend, value, grant):
    """Preapproved new-workspace quarantine/purge, with per-file durable receipts."""
    directory = _directory(raw['operations_root'], value['run_id'])
    _runtime(raw, backend)
    if (directory / 'rollback.json').exists():
        return {'status': 'RETAIN_REQUIRED', 'reason': 'CAPTURE_ROLLED_BACK_OWNER_REVIEW_REQUIRED'}
    workspace = _protect_root(raw, Path(value['workspace']))
    policy = _grant(raw, backend, value, grant)
    write_immutable(directory / ('disposition-authorization-' + digest(grant) + '.json'), grant)
    # A retained reason is a legitimate blocking outcome, not a failed test.
    if policy.independent_rollback_required or policy.keep_latest_capture:
        _event(directory, 'RETAIN_REQUIRED', reason='CAPTURE_RETENTION_RESPONSIBILITY')
        return {'status': 'RETAIN_REQUIRED', 'reason': 'CAPTURE_RETENTION_RESPONSIBILITY'}
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['created_at'])).total_seconds()
    if age < policy.minimum_retention_seconds:
        _event(directory, 'RETAIN_REQUIRED', reason='CAPTURE_MINIMUM_RETENTION_NOT_ELAPSED')
        return {'status': 'RETAIN_REQUIRED', 'reason': 'CAPTURE_MINIMUM_RETENTION_NOT_ELAPSED'}
    plan = read_json(directory / 'plan.json')
    receipt = validate_seal(read_json(Path(raw['operations_root']) / 'jobs' / value['run_id'] / 'receipt.json'), 'BACKUP_SET_V1')
    restored = validate_seal(read_json(directory / 'restore.json'), 'BACKUP_RESTORE_V1')
    if (receipt['plan_sha256'] != plan['document_sha256'] or
            receipt['repository'] != value['repository'] or
            restored['receipt_sha256'] != receipt['document_sha256'] or
            restored['status'] != 'ROUNDTRIP_PASS' or restored['snapshot_id'] != receipt['snapshot_id']):
        raise StorageError('CAPTURE_RESTORE_PROOF_MISMATCH')
    op._repository_matches(backend, plan)
    if not any(s['snapshot_id'] == receipt['snapshot_id'] and not s.get('incomplete')
               for s in backend.list_snapshots(backup_set_id=value['run_id'])):
        raise StorageError('CAPTURE_RETAINED_SNAPSHOT_UNAVAILABLE')
    if backend.verify_snapshot(receipt['snapshot_id']).get('status') not in {'PASS', 'VERIFIED', 'SNAPSHOT_VERIFIED'}:
        raise StorageError('SNAPSHOT_VERIFICATION_FAILED')
    refs = _references(grant, workspace, (directory / 'quarantine',))
    _event(directory, 'REFERENCES_REVIEWED', reference_scope_sha256=refs['record_sha256'] if 'record_sha256' in refs else digest(refs), grant_id=grant['approval_id'])
    disposition_path = directory / 'disposition-plan.json'
    if not disposition_path.exists():
        verify_restored(plan, workspace / 'restore')
        # Existing stage release has its own independent restore proof gate.
        op.release_owned_stage(Path(raw['operations_root']) / 'jobs' / value['run_id'], backend=backend, restore_report=restored)
        task = ScratchTask.resume(workspace.parent, workspace, owner_task=value['owner_task'], run_id=value['run_id'])
        items = []
        for path in workspace.rglob('*'):
            path = safe_path(path, file=path.is_file())
            if not path.is_file() or path.name in {'owner.json', 'lifecycle.jsonl'} and path.parent == workspace:
                continue
            if path not in task.assets or sha256_file(path) != task.assets[path].get('sha256'):
                raise StorageError('CAPTURE_UNREGISTERED_OR_CHANGED_FILE')
            items.append({**cleanup.identity(path), 'sha256': sha256_file(path)})
        write_immutable(disposition_path, seal({'contract': 'CAPTURE_DISPOSITION_PLAN_V1',
            'manifest_sha256': value['document_sha256'], 'grant_sha256': digest(grant),
            'created_at': now(), 'items': items}))
        _event(directory, 'DISPOSITION_ELIGIBLE', files=len(items), logical_bytes=sum(i['size_bytes'] for i in items))
    disposition = validate_seal(read_json(disposition_path), 'CAPTURE_DISPOSITION_PLAN_V1')
    _validate_disposition(value, disposition)
    if (disposition['manifest_sha256'] != value['document_sha256'] or
            disposition['grant_sha256'] != digest(grant) and grant.get('replaces_grant_sha256') != disposition['grant_sha256']):
        raise StorageError('CAPTURE_DISPOSITION_PLAN_BINDING_CHANGED')
    quarantine = directory / 'quarantine'
    quarantine.mkdir(exist_ok=True)
    for index, item in enumerate(disposition['items']):
        source = Path(item['path']); target = quarantine / f'{index:06d}'
        if str(quarantine.stat().st_dev) != item['volume_id']:
            raise StorageError('CROSS_VOLUME_QUARANTINE_UNSUPPORTED_USE_ARCHIVE')
        _owned_mutation(raw, backend, value, grant, disposition, index, source, target, 'QUARANTINE')
    observe = directory / 'quarantined.json'
    if not observe.exists():
        write_immutable(observe, seal({'contract': 'CAPTURE_QUARANTINE_OBSERVATION_V1',
            'plan_sha256': disposition['document_sha256'], 'created_at': now()}))
    observed = validate_seal(read_json(observe), 'CAPTURE_QUARANTINE_OBSERVATION_V1')
    if (datetime.now(timezone.utc) - datetime.fromisoformat(observed['created_at'])).total_seconds() < policy.quarantine_seconds:
        _event(directory, 'DISPOSITION_PENDING', reason='QUARANTINE_OBSERVATION_NOT_ELAPSED')
        return {'status': 'DISPOSITION_PENDING', 'reason': 'QUARANTINE_OBSERVATION_NOT_ELAPSED'}
    _references(grant, workspace, (directory / 'quarantine',))
    for index, item in enumerate(disposition['items']):
        _owned_mutation(raw, backend, value, grant, disposition, index, quarantine / f'{index:06d}', None, 'PURGE')
    remaining = {p for p in workspace.rglob('*') if p.is_file()}
    if remaining != {workspace / 'owner.json', workspace / 'lifecycle.jsonl'} or any(quarantine.iterdir()):
        raise StorageError('CAPTURE_UNEXPECTED_REMAINING_FILES')
    _event(directory, 'RETIRED', disposition_sha256=disposition['document_sha256'],
        restore_report_sha256=restored['document_sha256'], snapshot_id=receipt['snapshot_id'],
        retained_recovery='VERIFIED_PINNED_KOPIA_SNAPSHOT', logical_bytes=sum(i['size_bytes'] for i in disposition['items']))
    return {'status': 'RETIRED', 'run_id': value['run_id'], 'snapshot_id': receipt['snapshot_id'],
            'workspace_bytes_remaining': _usage(workspace), 'files_disposed': len(disposition['items'])}


def _owned_mutation(raw, backend, value, grant, plan, index, source, target, action):
    directory = _directory(raw['operations_root'], value['run_id'])
    item = plan['items'][index]
    key = f'{action.lower()}-{index:06d}'
    done = directory / (key + '.done.json'); intent = directory / (key + '.intent.json')
    if done.exists():
        record = validate_seal(read_json(done), 'CAPTURE_MUTATION_RECEIPT_V1')
        if record['plan_sha256'] != plan['document_sha256'] or record.get('index') != index or record.get('action') != action:
            raise StorageError('CAPTURE_MUTATION_RECEIPT_CHANGED')
        # After PURGE, replaying the earlier quarantine step is an audit no-op.
        purged = directory / (f'purge-{index:06d}.done.json')
        purge_intent = directory / (f'purge-{index:06d}.intent.json')
        if action == 'QUARANTINE' and purged.exists():
            validate_seal(read_json(purged), 'CAPTURE_MUTATION_RECEIPT_V1')
            if source.exists() or target.exists():
                raise StorageError('CAPTURE_RETIRED_FILE_REAPPEARED')
        elif action == 'QUARANTINE' and purge_intent.exists() and not source.exists() and not target.exists():
            pending = validate_seal(read_json(purge_intent), 'CAPTURE_MUTATION_INTENT_V1')
            if pending['plan_sha256'] != plan['document_sha256'] or pending['item'] != item or pending['index'] != index:
                raise StorageError('CAPTURE_INTERRUPTED_OUTCOME_UNRESOLVED')
        elif source.exists() or target is not None and cleanup.identity(target)['file_id'] != item['file_id']:
            raise StorageError('CAPTURE_COMPLETED_MUTATION_STATE_CHANGED')
        return
    def check():
        _runtime(raw)
        manifest(raw['operations_root'], value['run_id'])
        _grant(raw, backend, value, grant)
        _references(grant, Path(value['workspace']), (directory / 'quarantine',))
        if Path(raw['writer_lock_directory'], 'daily-run.lock').exists():
            raise StorageError('ACTIVE_TASK_PRESENT')
        if Path(raw['operations_root'], 'jobs', value['run_id'], 'cancel.json').exists():
            raise StorageError('BACKUP_CANCELLED')
        original = Path(item['path'])
        if action == 'PURGE' and original.exists():
            raise StorageError('CAPTURE_ORIGINAL_PATH_REAPPEARED')
        for p in (source, target, original):
            if p is not None:
                _protect_root(raw, p)
                if any(Path(str(p) + s).exists() for s in ('-wal', '-shm', '-journal')):
                    raise StorageError('DATABASE_SIDECAR_OR_ACTIVITY_PRESENT')
    if intent.exists():
        prior = validate_seal(read_json(intent), 'CAPTURE_MUTATION_INTENT_V1')
        if prior['plan_sha256'] != plan['document_sha256'] or prior['item'] != item or prior.get('index') != index or prior.get('action') != action:
            raise StorageError('CAPTURE_MUTATION_INTENT_CHANGED')
        if not source.exists():
            if target is not None and cleanup.identity(target)['file_id'] != item['file_id']:
                raise StorageError('CAPTURE_INTERRUPTED_OUTCOME_UNRESOLVED')
            check()
            write_immutable(done, seal({'contract': 'CAPTURE_MUTATION_RECEIPT_V1',
                'plan_sha256': plan['document_sha256'], 'action': action, 'created_at': now(),
                'index': index,
                'reconciled_after_interruption': True, 'observed_new_deleted_bytes': 0}))
            return
    else:
        write_immutable(intent, seal({'contract': 'CAPTURE_MUTATION_INTENT_V1',
            'plan_sha256': plan['document_sha256'], 'item': item, 'action': action, 'index': index, 'created_at': now()}))
    check()
    # Existing Cleanup V1 Windows HANDLE/fstat/hash/no-overwrite primitive.
    # The lifecycle gate authorizes only this exact newly registered inventory.
    cleanup._mutate(source, target, item, final_check=check)
    write_immutable(done, seal({'contract': 'CAPTURE_MUTATION_RECEIPT_V1',
        'plan_sha256': plan['document_sha256'], 'action': action, 'created_at': now(),
        'index': index,
        'reconciled_after_interruption': False, 'observed_new_deleted_bytes': item['size_bytes'] if target is None else 0}))


def reconcile_lock(raw, run_id, *, kind, expected_lock_sha256, requested_by):
    """Explicit exact-owner dead-lock reconciliation. Never touches writer lock."""
    operations = _protect_root(raw, Path(raw['operations_root']))
    directory = _directory(operations, run_id)
    has_manifest = (directory / 'manifest.json').exists()
    if has_manifest:
        manifest(operations, run_id)
    elif kind != 'admission':
        raise StorageError('CAPTURE_MANIFEST_REQUIRED_FOR_LOCK_RECONCILIATION')
    parents = {'admission': operations / 'capture-admission', 'lifecycle': directory,
               'backup-job': operations / 'jobs' / run_id}
    if kind not in parents or not requested_by:
        raise StorageError('CAPTURE_LOCK_RECONCILIATION_SCOPE_INVALID')
    lock = safe_path(parents[kind] / 'daily-run.lock')
    identity = file_identity(lock); record = read_json(lock)
    if (sha256_file(lock) != expected_lock_sha256 or record.get('run_id') != run_id or
            record.get('command') != 'storage-baseline' or
            not re.fullmatch('[0-9a-f]{32}', record.get('storage_token', ''))):
        raise StorageError('CAPTURE_LOCK_OWNER_MISMATCH')
    if op._process_alive(record.get('pid')):
        raise StorageError('CAPTURE_LOCK_OWNER_STILL_ACTIVE')
    archive = directory if has_manifest else operations / 'capture-reconciliations'
    write_immutable(archive / ('reconciled-' + kind + '-' + expected_lock_sha256 + '.json'),
        seal({'contract': 'CAPTURE_DEAD_LOCK_RECEIPT_V1', 'lock': record,
              'expected_sha256': expected_lock_sha256, 'requested_by': requested_by, 'created_at': now()}))
    if file_identity(lock) != identity or sha256_file(lock) != expected_lock_sha256:
        raise StorageError('CAPTURE_LOCK_CHANGED_DURING_RECONCILIATION')
    lock.unlink()
    return {'status': 'CAPTURE_LOCK_RECONCILED', 'writer_lock_changed': False}


def rollback(raw, run_id, *, requested_by):
    """Explicit restoration of new owned quarantined files, never after purge.

    A rollback deliberately retains this workspace and blocks subsequent runs
    pending owner review. It is not an implicit new deletion authorization.
    """
    if not raw.get('capture_lifecycle', {}).get('enabled', False) or not raw.get('policy', {}).get('enabled', False):
        return {'status': 'DISABLED', 'side_effects': False}
    if not requested_by:
        raise StorageError('CAPTURE_ROLLBACK_REQUESTOR_REQUIRED')
    operations = _protect_root(raw, Path(raw['operations_root']))
    directory = _directory(operations, run_id)
    with storage_lock(operations / 'capture-admission', run_id=run_id), storage_lock(directory, run_id=run_id):
        value = manifest(operations, run_id)
        if (directory / 'rollback.json').exists():
            return {**validate_seal(read_json(directory / 'rollback.json'), 'CAPTURE_ROLLBACK_V1'), 'reused': True}
        if list(directory.glob('purge-*.intent.json')) or list(directory.glob('purge-*.done.json')):
            raise StorageError('CAPTURE_PURGE_STARTED_CANNOT_ROLLBACK_USE_SNAPSHOT_RESTORE')
        plan = validate_seal(read_json(directory / 'disposition-plan.json'), 'CAPTURE_DISPOSITION_PLAN_V1')
        _validate_disposition(value, plan)
        for index, item in enumerate(plan['items']):
            original = Path(item['path']); source = directory / 'quarantine' / f'{index:06d}'
            _protect_root(raw, original)
            if not source.exists():
                if cleanup.identity(original)['file_id'] != item['file_id'] or sha256_file(original) != item['sha256']:
                    raise StorageError('CAPTURE_ROLLBACK_STATE_UNRESOLVED')
                continue
            if original.exists():
                raise StorageError('TARGET_ALREADY_EXISTS')
            intent = directory / f'rollback-{index:06d}.intent.json'
            if not intent.exists():
                write_immutable(intent, seal({'contract': 'CAPTURE_ROLLBACK_INTENT_V1',
                    'plan_sha256': plan['document_sha256'], 'index': index, 'requested_by': requested_by, 'created_at': now()}))
            def check():
                manifest(operations, run_id)
                _protect_root(raw, original.parent)
                if original.exists() or any(Path(str(original) + s).exists() for s in ('-wal', '-shm', '-journal')):
                    raise StorageError('CAPTURE_ROLLBACK_TARGET_BLOCKED')
            cleanup._mutate(source, original, item, final_check=check)
        record = seal({'contract': 'CAPTURE_ROLLBACK_V1', 'status': 'RETAIN_REQUIRED',
            'plan_sha256': plan['document_sha256'], 'created_at': now(), 'requested_by': requested_by,
            'reason': 'CAPTURE_ROLLED_BACK_OWNER_REVIEW_REQUIRED', 'files_restored': len(plan['items'])})
        write_immutable(directory / 'rollback.json', record)
        _event(directory, 'RETAIN_REQUIRED', reason=record['reason'], rollback_sha256=record['document_sha256'])
        return record
