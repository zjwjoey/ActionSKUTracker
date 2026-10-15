"""Small read-only operational status; no notification or scheduler side effects."""
from datetime import datetime, timezone
from pathlib import Path

from .backup_sets import read_json, validate_seal
from .backup_operations import job_events
from .capture_lifecycle import _usage, manifest, _state, retired_intact
from .storage_foundation import StorageError


def validate_policy(value, raw):
    if value is None:
        return
    allowed = {'contract', 'approved', 'core_interval_hours', 'current_interval_hours',
        'archive_trigger', 'daily_versions', 'weekly_versions', 'monthly_versions',
        'restore_interval_days', 'max_repository_bytes', 'capacity_warning_percent',
        'password_custodian', 'recovery_owner', 'operations_owner', 'credentials_expires_at'}
    if (set(value) - allowed or value.get('contract') != 'BACKUP_OPERATIONS_POLICY_V1' or
            type(value.get('approved', False)) is not bool):
        raise StorageError('BACKUP_OPERATIONS_POLICY_INVALID')
    for key in ('core_interval_hours', 'current_interval_hours', 'daily_versions',
                'weekly_versions', 'monthly_versions', 'restore_interval_days', 'max_repository_bytes'):
        if key in value and (type(value[key]) is not int or value[key] <= 0):
            raise StorageError('BACKUP_OPERATIONS_POLICY_LIMIT_INVALID')
    if type(value.get('capacity_warning_percent', 80)) is not int or not 1 <= value.get('capacity_warning_percent', 80) <= 100:
        raise StorageError('BACKUP_OPERATIONS_POLICY_LIMIT_INVALID')
    if value.get('approved') and not all(value.get(k) for k in
            ('password_custodian', 'recovery_owner', 'operations_owner')):
        raise StorageError('BACKUP_OPERATIONS_RESPONSIBILITY_REQUIRED')


def health(raw):
    policy = raw.get('operations_policy') or {}
    validate_policy(raw.get('operations_policy'), raw)
    operations = Path(raw['operations_root'])
    failures, successes, restored, captures, blockers = [], [], [], [], []
    for path in (operations / 'invocations').glob('*.json'):
        invocation = validate_seal(read_json(path), 'BACKUP_INVOCATION_V1')
        if invocation['exit_code']:
            failures.append({k: invocation.get(k) for k in ('run_id', 'created_at', 'reason', 'action')})
    for job in (operations / 'jobs').glob('*'):
        for event in job_events(job):
            if event['status'] in {'FAILED', 'CANCELLED'}:
                failures.append({'run_id': job.name, 'created_at': event['created_at'],
                                 'reason': event.get('reason', event['status'])})
        if (job / 'receipt.json').exists():
            receipt = validate_seal(read_json(job / 'receipt.json'), 'BACKUP_SET_V1')
            successes.append({'run_id': job.name, 'created_at': receipt['created_at'],
                              'snapshot_id': receipt['snapshot_id']})
    for directory in (operations / 'capture-lifecycle').glob('*'):
        value = manifest(operations, directory.name)
        state = _state(directory)
        if state == 'RETIRED' and not retired_intact(value):
            state = 'BLOCKED_RETIRED_WORKSPACE_CHANGED'
        size = _usage(Path(value['workspace']))
        quarantine_bytes = _usage(directory / 'quarantine')
        payload = sum(_usage(Path(value['workspace']) / name) for name in ('capture', 'restore')) + quarantine_bytes
        captures.append({'run_id': directory.name, 'state': state, 'workspace_bytes': size,
                         'quarantine_bytes': quarantine_bytes, 'pending_payload_bytes': payload})
        if state != 'RETIRED':
            cancelled = (operations / 'jobs' / directory.name / 'cancel.json').exists()
            blockers.append({'run_id': directory.name, 'reason': 'BACKUP_CANCELLED' if cancelled else job_events(directory)[-1].get('reason', state)})
        if (directory / 'restore.json').exists():
            report = validate_seal(read_json(directory / 'restore.json'), 'BACKUP_RESTORE_V1')
            if report.get('status') == 'ROUNDTRIP_PASS':
                restored.append(report.get('checked_at', value['created_at']))
    enabled = raw.get('policy', {}).get('enabled', False)
    scratch = _usage(operations)
    maximum = raw.get('policy', {}).get('max_scratch_bytes', 16 * 1024**3)
    warnings = []
    legacy = operations / 'captures'
    legacy_entries = list(legacy.iterdir()) if legacy.is_dir() else []
    if raw.get('capture_lifecycle', {}).get('enabled') and legacy_entries:
        blockers.append({'run_id': None, 'reason': 'LEGACY_CAPTURE_REQUIRES_SEPARATE_DISPOSITION'})
    if scratch >= maximum:
        blockers.append({'run_id': None, 'reason': 'SCRATCH_CAPACITY_EXHAUSTED'})
    if scratch * 100 >= maximum * policy.get('capacity_warning_percent', 80):
        warnings.append('SCRATCH_CAPACITY_WARNING')
    backend = raw.get('backend', {})
    repository_bytes = None
    if backend.get('provider', 'filesystem') in {'filesystem', 'local'} and backend.get('repository_path'):
        if not Path(backend['repository_path']).is_dir():
            blockers.append({'run_id': None, 'reason': 'KOPIA_REPOSITORY_UNAVAILABLE'})
        repository_bytes = _usage(Path(backend['repository_path']))
        if repository_bytes >= policy.get('max_repository_bytes', 30 * 1024**3):
            blockers.append({'run_id': None, 'reason': 'REPOSITORY_CAPACITY_EXHAUSTED'})
        if repository_bytes * 100 >= policy.get('max_repository_bytes', 30 * 1024**3) * policy.get('capacity_warning_percent', 80):
            warnings.append('REPOSITORY_CAPACITY_WARNING')
    latest_success = max(successes, key=lambda x: x['created_at']) if successes else None
    latest_failure = max(failures, key=lambda x: x['created_at']) if failures else None
    if enabled and (not latest_success or
            (datetime.now(timezone.utc) - datetime.fromisoformat(latest_success['created_at'])).total_seconds() > policy.get('core_interval_hours', 24) * 3600):
        warnings.append('BACKUP_MISSING_OR_OVERDUE')
    if enabled and (not restored or
            (datetime.now(timezone.utc) - datetime.fromisoformat(max(restored))).total_seconds() > policy.get('restore_interval_days', 30) * 86400):
        warnings.append('RESTORE_PROOF_MISSING_OR_OVERDUE')
    if latest_failure and (not latest_success or latest_failure['created_at'] > latest_success['created_at']):
        warnings.append('LATEST_BACKUP_FAILED')
    expiry = policy.get('credentials_expires_at')
    if expiry:
        try:
            expires = datetime.fromisoformat(expiry)
            if expires.tzinfo is None:
                raise ValueError()
            credential = 'EXPIRED' if expires <= datetime.now(timezone.utc) else 'VALID_UNTIL_REPORTED_EXPIRY'
        except (TypeError, ValueError):
            raise StorageError('CREDENTIAL_EXPIRY_METADATA_INVALID') from None
        if credential == 'EXPIRED':
            warnings.append('CREDENTIALS_EXPIRED')
    else:
        credential = 'NOT_APPLICABLE' if backend.get('provider', 'filesystem') in {'filesystem', 'local'} else 'UNKNOWN'
    if raw.get('capture_lifecycle', {}).get('enabled') and backend.get('provider', 'filesystem') not in {'filesystem', 'local'}:
        blockers.append({'run_id': None, 'reason': 'V31_CLOUD_GATE_BLOCKED'})
    status = 'DISABLED' if not enabled else 'BLOCKED' if blockers else 'WARNING' if warnings else 'HEALTHY'
    return {'contract': 'BACKUP_HEALTH_V1', 'status': status,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'latest_successful_backup': latest_success, 'latest_failed_job': latest_failure,
        'latest_restore_verified_at': max(restored) if restored else None,
        'captures': captures, 'unfinished_capture_count': sum(c['state'] != 'RETIRED' for c in captures),
        'legacy_unmanaged_entry_count': len(legacy_entries),
        'pending_disposition_bytes': sum(c['pending_payload_bytes'] for c in captures if c['state'] != 'RETIRED'),
        'scratch_bytes': scratch, 'scratch_budget_bytes': maximum,
        'repository_bytes': repository_bytes, 'repository_live_status': 'NOT_PROBED',
        'credential_expiry_status': credential, 'warnings': warnings, 'next_run_blockers': blockers,
        'notification_sent': False, 'policy_approved': policy.get('approved', False),
        'suggested_exit_code': 0 if status in {'HEALTHY', 'DISABLED'} else 2 if status == 'BLOCKED' else 1}
