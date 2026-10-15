"""Operator-only, evidence-bound quarantine and purge. Never scans for deletable files.

Approval records are external operator assertions, not authentication credentials.
Their digest must be supplied independently to execution. No API grants approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .cleanup_evidence import REQUIRED_REFERENCE_DOMAINS, inspect_database, path_key, task_completion
from .storage_foundation import StorageError, resolve_asset, sha256_file, storage_lock
from .storage_lifecycle import cleanup_preview, create_candidate, materialize_plan, require_candidate

STATES = frozenset({'ACTIVE_REQUIRED', 'RECOVERY_REQUIRED', 'ARCHIVE_REQUIRED',
    'CLEANUP_REVIEW_REQUIRED', 'CLEANUP_ELIGIBLE', 'QUARANTINE_APPROVED', 'QUARANTINED',
    'PURGE_APPROVED', 'PURGED', 'UNKNOWN_BLOCKED'})
ACTIONS = frozenset({'QUARANTINE', 'ROLLBACK', 'PURGE'})


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise StorageError('EVIDENCE_UNAVAILABLE') from exc


def seal(value):
    if 'record_sha256' in value:
        raise StorageError('ALREADY_SEALED')
    return {**value, 'record_sha256': digest(value)}


def verify_seal(value):
    if not isinstance(value, dict) or value.get('record_sha256') != digest(
            {k: v for k, v in value.items() if k != 'record_sha256'}):
        raise StorageError('IMMUTABLE_RECORD_CHANGED')
    return value


def atomic_new(path, value):
    """Publish complete JSON with exclusive creation; never replaces unknown files."""
    path = Path(path)
    temporary = path.with_name('.publish-' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Fails atomically if destination already exists.
    finally:
        if temporary.exists():
            temporary.unlink()


def safe_path(raw, *, must_exist=True):
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise StorageError('ABSOLUTE_CANONICAL_PATH_REQUIRED')
    if os.name == 'nt' and (str(path).startswith('\\\\') or any(':' in p for p in path.parts[1:])):
        raise StorageError('UNC_OR_ALTERNATE_STREAM_NOT_SUPPORTED')
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise StorageError('REPARSE_POINT_OR_SYMLINK_FORBIDDEN')
    resolved = path.resolve(strict=must_exist)
    if os.path.normcase(str(resolved)) != os.path.normcase(os.path.abspath(path)):
        raise StorageError('PATH_ALIAS_FORBIDDEN')
    return resolved


def identity(path):
    path = safe_path(path)
    value = path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise StorageError('REGULAR_SINGLE_LINK_FILE_REQUIRED')
    return {'path': str(path), 'file_id': f'{value.st_dev}:{value.st_ino}',
            'volume_id': str(value.st_dev), 'size_bytes': value.st_size,
            'mtime_ns': value.st_mtime_ns}


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        raise StorageError('INVALID_EVIDENCE_TIMESTAMP') from None


def _protect(path, context):
    path = safe_path(path, must_exist=False)
    roots = context.get('protected_roots')
    if not isinstance(roots, list) or not roots:
        raise StorageError('PRODUCTION_PROTECTION_REQUIRED')
    fixed = [Path('F:/按日期整理'), Path('F:/Action_Master')] if os.name == 'nt' else []
    for raw in [*roots, *fixed]:
        protected = safe_path(raw, must_exist=False)
        if path == protected or path.is_relative_to(protected):
            raise StorageError('PROTECTED_PRODUCTION_OR_HISTORY_PATH')
        if path.exists() and protected.is_file() and path.samefile(protected):
            raise StorageError('PRODUCTION_FILE_ALIAS')
    return path


def initialize_root(root, protected_roots):
    root = safe_path(root, must_exist=False)
    marker = create_candidate(root, protected_roots=[Path(v) for v in protected_roots])
    # Keep approval/evidence/operation receipts outside user-writable shared directories.
    if os.name == 'nt':
        import subprocess
        user = subprocess.run(['whoami'], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(['icacls', str(root), '/inheritance:r', '/grant:r',
            user + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], capture_output=True)
        if result.returncode:
            raise StorageError('PRIVATE_ROOT_ACL_FAILED')
    else:
        root.chmod(0o700)
    for child in ('quarantine', 'receipts', 'locks'):
        (root / child).mkdir()
    return marker


def _root(context):
    if (not isinstance(context.get('active_task_markers'), list) or not context['active_task_markers'] or
            any(not Path(p).is_absolute() for p in context['active_task_markers'])):
        raise StorageError('EXPLICIT_ACTIVE_TASK_MARKERS_REQUIRED')
    age = context.get('reference_max_age_seconds', 3600)
    if type(age) is not int or age <= 0:
        raise StorageError('INVALID_REFERENCE_EVIDENCE_AGE')
    root = safe_path(context['state_root'])
    _protect(root, context)
    require_candidate(root)
    for child in ('quarantine', 'receipts', 'locks'):
        if not safe_path(root / child).is_dir():
            raise StorageError('EXECUTOR_ROOT_INVALID')
    return root


def inventory(root, *, max_files=10000, max_bytes=64 * 1024**2):
    """A bounded closed consumer directory. Unsupported/binary sources block closure.

    The responsible reviewer must establish that these directories cover all required
    consumer domains. This function proves their bytes did not change, not that an
    arbitrary selection of directories covers an entire machine.
    """
    root = safe_path(root)
    if not root.is_dir():
        raise StorageError('REFERENCE_DIRECTORY_REQUIRED')
    rows, total = [], 0
    for path in sorted(root.rglob('*')):
        safe_path(path)
        if path.is_dir():
            continue
        item = identity(path)
        total += item['size_bytes']
        if total > max_bytes or len(rows) >= max_files:
            raise StorageError('REFERENCE_INVENTORY_BUDGET_EXCEEDED')
        if path.suffix.lower() not in {'.json', '.jsonl', '.txt', '.csv', '.yaml', '.yml', '.md', '.py', '.toml'}:
            raise StorageError('UNSUPPORTED_REFERENCE_SOURCE')
        try:
            path.read_text(encoding='utf-8')
        except UnicodeError as exc:
            raise StorageError('UNREADABLE_REFERENCE_SOURCE') from exc
        rows.append({**item, 'sha256': sha256_file(path)})
    return rows


def capture_reference_scope(domains, *, reviewer, scope_reason):
    """Prepare observations; reviewer separately attests completeness in external evidence."""
    if set(domains) != REQUIRED_REFERENCE_DOMAINS or not reviewer or not scope_reason:
        raise StorageError('ALL_REFERENCE_DOMAINS_REQUIRED')
    roots = {key: str(safe_path(path)) for key, path in domains.items()}
    return {'contract': 'CLOSED_REFERENCE_SCOPE_V1', 'reviewer': reviewer,
        'scope_reason': scope_reason, 'captured_at': now().isoformat(),
        'complete': False, 'unresolved_dynamic_references': None,
        'excluded_required_sources': None, 'domains': roots,
        'inventories': {key: inventory(path) for key, path in roots.items()}}


def validate_references(proof, candidate_paths, *, max_age_seconds):
    if (proof.get('contract') != 'CLOSED_REFERENCE_SCOPE_V1' or proof.get('complete') is not True or
            proof.get('unresolved_dynamic_references') != 0 or proof.get('excluded_required_sources') != 0 or
            not proof.get('reviewer') or not proof.get('scope_reason') or
            set(proof.get('domains', {})) != REQUIRED_REFERENCE_DOMAINS):
        raise StorageError('REFERENCE_CLOSURE_UNPROVEN')
    age = (now() - _time(proof.get('captured_at'))).total_seconds()
    if not 0 <= age <= max_age_seconds:
        raise StorageError('REFERENCE_EVIDENCE_EXPIRED')
    targets = {path_key(str(v)) for v in candidate_paths}
    # Match every path spelling in text as well as normalized structured values.
    from .cleanup_evidence import extract_path_references
    for domain, root in proof['domains'].items():
        rows = inventory(root)
        if rows != proof.get('inventories', {}).get(domain):
            raise StorageError('REFERENCE_INVENTORY_CHANGED_REVIEW_REQUIRED')
        for row in rows:
            text = Path(row['path']).read_text(encoding='utf-8')
            if any(str(v).replace('\\', '/').casefold() in text.replace('\\\\', '/').replace('\\', '/').casefold()
                   for v in candidate_paths):
                raise StorageError('LIVE_REFERENCE_PRESENT')
            try:
                document = json.loads(text)
            except ValueError:
                document = text
            if extract_path_references(document, targets=targets):
                raise StorageError('LIVE_REFERENCE_PRESENT')


def _check_item(item, context, *, active_path=None, restoring=False):
    original = _protect(item['path'], context)
    path = _protect(active_path or original, context)
    actual = identity(path)
    for field in ('file_id', 'volume_id', 'size_bytes', 'mtime_ns'):
        if actual[field] != item[field]:
            raise StorageError('FILE_IDENTITY_CHANGED')
    if sha256_file(path) != item['sha256']:
        raise StorageError('FILE_CONTENT_CHANGED')
    for candidate in (original, path):
        if candidate.name.endswith(('-wal', '-shm', '-journal')) or any(
                Path(str(candidate) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
            raise StorageError('DATABASE_SIDECAR_OR_ACTIVITY_PRESENT')
    for marker in context.get('active_task_markers', []):
        if Path(marker).exists():
            raise StorageError('ACTIVE_TASK_PRESENT')
    proof = item['certificate']
    if proof.get('evidence_contract') != 'LEGACY_CLEANUP_EVIDENCE_V1':
        raise StorageError('P21_EVIDENCE_REQUIRED')
    if proof.get('task_evidence', {}).get('resume_allowed') is not False:
        raise StorageError('RESUME_RESPONSIBILITY_NOT_RELEASED')
    if not proof.get('owner_task') or not proof.get('run_id'):
        raise StorageError('TASK_OWNERSHIP_MISSING')
    if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}:
        current = inspect_database(path, full_integrity=True)
        if (current.get('current_integrity') != 'PASS' or current.get('current_foreign_keys') != 'PASS' or
                current.get('database_identity') != proof.get('database_identity')):
            raise StorageError('DATABASE_IDENTITY_OR_INTEGRITY_CHANGED')
    if restoring:
        # A newly discovered consumer is a reason to restore the exact original,
        # not a reason to hold it hostage in quarantine. Never overwrites a target.
        return actual
    _validate_closure(item)
    # Reuse P2.1's full gate; active path changes only after a bound quarantine receipt.
    current = {**proof, 'path': str(path), 'file_id': actual['file_id']}
    if not cleanup_preview([current])['whitelist']:
        raise StorageError('P21_CLEANUP_GATE_BLOCKED')
    alternative = proof['alternative_recovery']
    other = safe_path(alternative['path'])
    identity(other)  # No aliases/hardlinks for independent recovery.
    if str(other) in {str(original), str(path)} or other.samefile(path):
        raise StorageError('ALTERNATIVE_IS_CANDIDATE')
    evidence = read_json(alternative['evidence_path'])
    expected_proof = item['recovery_proof']
    if (identity(alternative['evidence_path']) != {k: v for k, v in expected_proof.items() if k != 'sha256'} or
            sha256_file(alternative['evidence_path']) != expected_proof['sha256']):
        raise StorageError('RECOVERY_PROOF_CHANGED')
    if (evidence.get('integrity') != 'PASS' or evidence.get('foreign_keys') != 'PASS' or
            evidence.get('backup_path') != str(other)):
        raise StorageError('BOUND_RESTORE_EVIDENCE_REQUIRED')
    validate_references(item['references'], (original, path),
                        max_age_seconds=context.get('reference_max_age_seconds', 3600))
    return actual


def _bound_document(binding):
    if not isinstance(binding, dict) or not binding.get('path') or not binding.get('sha256'):
        raise StorageError('BOUND_CLOSURE_DOCUMENT_REQUIRED')
    actual = identity(binding['path'])
    if (actual != {k: v for k, v in binding.items() if k != 'sha256'} or
            sha256_file(binding['path']) != binding['sha256']):
        raise StorageError('CLOSURE_DOCUMENT_CHANGED')
    return read_json(binding['path'])


def _validate_closure(item):
    certificate = item['certificate']
    sources = certificate.get('closure_documents') or {}
    task = task_completion(_bound_document(sources.get('task')), run_id=certificate['run_id'], database_path=item['path'])
    if (task.get('task_status') != 'COMPLETED' or task.get('terminal_verified') is not True or
            task.get('resume_allowed') is not False or task.get('scope') != 'EXACT_RUN_AND_DATABASE'):
        raise StorageError('BOUND_TASK_TERMINAL_REQUIRED')
    for kind, contract in (('retention', 'RETENTION_DISPOSITION_V1'), ('recovery', 'RECOVERY_RELEASE_V1')):
        decision = _bound_document(sources.get(kind))
        if (decision.get('contract') != contract or path_key(decision.get('path', '')) != path_key(item['path']) or
                any(decision.get(key) != item[key] for key in ('file_id', 'sha256')) or
                decision.get('owner_task') != certificate['owner_task'] or decision.get('run_id') != certificate['run_id'] or
                not decision.get('reviewer') or not decision.get('reason') or
                _time(decision.get('reviewed_at')) > now()):
            raise StorageError('BOUND_RESPONSIBILITY_DECISION_REQUIRED')
        if kind == 'retention':
            if (not decision.get('policy') or decision.get('retain_until') != certificate.get('retain_until') or
                    decision.get('review_apply_obligations_released') is not True or
                    _time(decision.get('retain_until')) > now()):
                raise StorageError('RETENTION_RESPONSIBILITY_NOT_RELEASED')
        else:
            alternative = certificate['alternative_recovery']
            if (decision.get('recovery_required') is not False or decision.get('independent_rollback_obligation') is not False or
                    decision.get('alternative_path') != alternative['path'] or decision.get('alternative_sha256') != alternative['sha256']):
                raise StorageError('RECOVERY_RESPONSIBILITY_NOT_RELEASED')


def review_candidates(candidates):
    result = cleanup_preview(candidates)
    for row in result['assets']:
        if row.get('production_dependency') is True:
            state = 'ACTIVE_REQUIRED'
        elif row.get('recovery_required') is True:
            state = 'RECOVERY_REQUIRED'
        elif row.get('archive_required') is True:
            state = 'ARCHIVE_REQUIRED'
        elif row.get('evidence_contract') != 'LEGACY_CLEANUP_EVIDENCE_V1':
            state = 'UNKNOWN_BLOCKED'
            row['estimated_releasable_bytes'] = 0
            row['blockers'].append('P21_EVIDENCE_REQUIRED')
        elif row['cleanup_status'] == 'CONDITIONS_MET_PREVIEW_ONLY':
            state = 'CLEANUP_ELIGIBLE'
        else:
            state = 'CLEANUP_REVIEW_REQUIRED' if row.get('identity_verified') else 'UNKNOWN_BLOCKED'
        row['state'] = state
    result['whitelist'] = [row for row in result['assets'] if row['state'] == 'CLEANUP_ELIGIBLE']
    result['estimated_releasable_bytes'] = sum(row['size_bytes'] for row in result['whitelist'])
    return result


def prepare_plan(candidates, references, context, *, observation_seconds=86400):
    root = _root(context)
    if type(observation_seconds) is not int or observation_seconds < 0:
        raise StorageError('INVALID_OBSERVATION_PERIOD')
    if not candidates:
        raise StorageError('EMPTY_CLEANUP_PLAN')
    plan_id, items, seen = uuid.uuid4().hex, [], set()
    for candidate in candidates:
        actual = identity(_protect(candidate['path'], context))
        if actual['file_id'] in seen:
            raise StorageError('DUPLICATE_FILE_ID')
        seen.add(actual['file_id'])
        if candidate.get('file_id') != actual['file_id']:
            raise StorageError('CERTIFICATE_FILE_ID_CHANGED')
        item = {**actual, 'sha256': candidate.get('sha256'), 'certificate': candidate,
                'references': references, 'asset_id': digest(actual),
                'quarantine_path': str(root / 'quarantine' / (plan_id + '-' + Path(actual['path']).name))}
        recovery_proof = candidate.get('alternative_recovery', {}).get('evidence_path')
        if not recovery_proof:
            raise StorageError('BOUND_RESTORE_EVIDENCE_REQUIRED')
        item['recovery_proof'] = {**identity(recovery_proof), 'sha256': sha256_file(recovery_proof)}
        _check_item(item, context)
        if str(root.stat().st_dev) != actual['volume_id']:
            raise StorageError('CROSS_VOLUME_QUARANTINE_UNSUPPORTED_USE_ARCHIVE')
        items.append(item)
    return seal({'contract': 'CLEANUP_ACTION_PLAN_V1', 'plan_id': plan_id,
        'created_at': now().isoformat(), 'action': 'QUARANTINE', 'context': context,
        'evidence_version': 'LEGACY_CLEANUP_EVIDENCE_V1+CLOSED_REFERENCE_SCOPE_V1',
        'observation_seconds': observation_seconds, 'items': items,
        'logical_bytes': sum(i['size_bytes'] for i in items), 'approval': 'EXTERNAL_REQUIRED'})


def _receipt_path(plan, item, suffix):
    key = digest([plan['record_sha256'], item['asset_id']])
    return _root(plan['context']) / 'receipts' / (key + '.' + suffix + '.json')


def transition_plan(previous, *, action, observation=None, references=None):
    verify_seal(previous)
    if previous['action'] != 'QUARANTINE' or action not in {'ROLLBACK', 'PURGE'}:
        raise StorageError('INVALID_STATE_TRANSITION')
    receipts = []
    for item in previous['items']:
        receipt = verify_seal(read_json(_receipt_path(previous, item, 'done')))
        if receipt.get('state') != 'QUARANTINED' or receipt.get('plan_sha256') != previous['record_sha256']:
            raise StorageError('QUARANTINE_RECEIPT_REQUIRED')
        receipts.append(receipt)
    if action == 'PURGE':
        if (not isinstance(observation, dict) or observation.get('status') != 'PASS' or
                observation.get('plan_sha256') != previous['record_sha256'] or
                observation.get('references_or_recovery_problems') is not False or
                not observation.get('reviewer') or not observation.get('evidence_paths')):
            raise StorageError('QUARANTINE_OBSERVATION_REQUIRED')
        for receipt in receipts:
            if (now() - _time(receipt['at'])).total_seconds() < previous['observation_seconds']:
                raise StorageError('QUARANTINE_OBSERVATION_NOT_ELAPSED')
        reviewed = _time(observation.get('reviewed_at'))
        if reviewed > now() or any((reviewed - _time(r['at'])).total_seconds() < previous['observation_seconds'] for r in receipts):
            raise StorageError('INVALID_OBSERVATION_TIMESTAMP')
        observation = {**observation, 'evidence_files': [
            {**identity(path), 'sha256': sha256_file(path)} for path in observation['evidence_paths']]}
    items = [{**i, 'references': references or i['references']} for i in previous['items']]
    return seal({**{k: v for k, v in previous.items() if k not in {'record_sha256', 'plan_id', 'created_at', 'action'}},
        'plan_id': uuid.uuid4().hex, 'created_at': now().isoformat(), 'action': action,
        'previous_plan_sha256': previous['record_sha256'], 'quarantine_receipts': receipts,
        'observation': observation, 'items': items})


def approval_request(plan):
    verify_seal(plan)
    return {'contract': 'CLEANUP_APPROVAL_V1', 'plan_sha256': plan['record_sha256'],
        'action': plan['action'], 'asset_ids': sorted(i['asset_id'] for i in plan['items']),
        'decision': 'PENDING', 'approver': None, 'approved_at': None, 'expires_at': None,
        'authorization_source': None}


def validate_approval(plan, approval, expected_digest):
    verify_seal(plan)
    if digest(approval) != expected_digest:
        raise StorageError('APPROVAL_DIGEST_MISMATCH')
    if (approval.get('contract') != 'CLEANUP_APPROVAL_V1' or approval.get('decision') != 'APPROVED' or
            approval.get('plan_sha256') != plan['record_sha256'] or approval.get('action') != plan['action'] or
            sorted(approval.get('asset_ids', [])) != sorted(i['asset_id'] for i in plan['items']) or
            not approval.get('approver') or not approval.get('authorization_source')):
        raise StorageError('EXPLICIT_EXACT_APPROVAL_REQUIRED')
    if not _time(plan['created_at']) <= _time(approval.get('approved_at')) <= now() < _time(approval.get('expires_at')):
        raise StorageError('APPROVAL_EXPIRED_OR_INVALID')


@contextmanager
def _held_file(path, *, writable=True):
    """Windows denies concurrent opens and mutates through the verified file handle."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        handle = kernel.CreateFileW(str(path), 0x80000000 | (0x10000 if writable else 0), 0, None, 3, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise StorageError('FILE_IN_USE_OR_ACCESS_DENIED')
        import msvcrt
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        try:
            with os.fdopen(fd, 'rb', closefd=True) as stream:
                yield stream, kernel, handle
        finally:
            pass  # CRT stream owns and closes the Windows handle.
    else:
        import fcntl
        with path.open('rb') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StorageError('FILE_IN_USE_OR_ACCESS_DENIED') from exc
            yield stream, None, None


def _mutate(path, target, item, *, final_check=lambda: None):
    with _held_file(path) as (stream, kernel, handle):
        info = os.fstat(stream.fileno())
        actual_id = f'{info.st_dev}:{info.st_ino}'
        if actual_id != item['file_id'] or info.st_nlink != 1 or info.st_size != item['size_bytes']:
            raise StorageError('HELD_FILE_IDENTITY_CHANGED')
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if sha != item['sha256']:
            raise StorageError('HELD_FILE_CONTENT_CHANGED')
        final_check()
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            if target is None:
                value = wintypes.BOOLEAN(True)
                success = kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value))
            else:
                class Rename(ctypes.Structure):
                    _fields_ = [('ReplaceIfExists', wintypes.BOOLEAN), ('RootDirectory', wintypes.HANDLE),
                                ('FileNameLength', wintypes.DWORD), ('FileName', ctypes.c_wchar * (len(str(target)) + 1))]
                value = Rename(False, None, len(str(target).encode('utf-16-le')), str(target))
                success = kernel.SetFileInformationByHandle(handle, 3, ctypes.byref(value), ctypes.sizeof(value))
            if not success:
                raise StorageError('FILE_MUTATION_ACCESS_OR_IDENTITY_BLOCKED')
        else:
            # POSIX locks are cooperative; never silently claim enforced Windows semantics.
            if identity(path)['file_id'] != actual_id:
                raise StorageError('PATH_CHANGED_DURING_EXECUTION')
            if target is not None:
                os.link(path, target)  # exclusive destination; preserves inode
            path.unlink()


def _execute(plan, approval, *, approval_sha256, enabled=False):
    if enabled is not True:
        raise StorageError('CLEANUP_EXECUTION_DISABLED')
    validate_approval(plan, approval, approval_sha256)
    if plan.get('contract') != 'CLEANUP_ACTION_PLAN_V1' or plan.get('action') not in ACTIONS:
        raise StorageError('INVALID_CLEANUP_PLAN')
    context, action = plan['context'], plan['action']
    root = _root(context)
    results = []
    with storage_lock(root / 'locks', run_id=plan['plan_id']):
        for item in plan['items']:
            original, quarantine = Path(item['path']), Path(item['quarantine_path'])
            if quarantine.parent != root / 'quarantine':
                raise StorageError('QUARANTINE_BOUNDARY_VIOLATION')
            path = original if action == 'QUARANTINE' else quarantine
            target = quarantine if action == 'QUARANTINE' else original if action == 'ROLLBACK' else None
            done = _receipt_path(plan, item, 'done')
            if done.exists():
                record = verify_seal(read_json(done))
                if record.get('plan_sha256') != plan['record_sha256'] or record.get('asset_id') != item['asset_id']:
                    raise StorageError('RECEIPT_BINDING_INVALID')
                if path.exists() or target is not None and (not target.exists() or identity(target)['file_id'] != item['file_id']):
                    raise StorageError('COMPLETED_OPERATION_STATE_CHANGED')
                results.append({**record, 'reused': True})
                continue
            intent_path = _receipt_path(plan, item, 'intent')
            if intent_path.exists():
                # An interrupted mutation needs explicit investigation, never an automatic second delete.
                raise StorageError('INTERRUPTED_OPERATION_RECONCILIATION_REQUIRED')
            if target is not None and target.exists():
                raise StorageError('TARGET_ALREADY_EXISTS')
            _check_item(item, context, active_path=path, restoring=action == 'ROLLBACK')
            if action in {'PURGE', 'ROLLBACK'}:
                receipts = plan.get('quarantine_receipts', [])
                selected = [verify_seal(r) for r in receipts if r.get('asset_id') == item['asset_id'] and r.get('state') == 'QUARANTINED' and
                            r.get('plan_sha256') == plan.get('previous_plan_sha256')]
                if len(selected) != 1:
                    raise StorageError('QUARANTINE_RECEIPT_REQUIRED')
                stored = root / 'receipts' / (digest([plan['previous_plan_sha256'], item['asset_id']]) + '.done.json')
                if verify_seal(read_json(stored)) != selected[0]:
                    raise StorageError('QUARANTINE_RECEIPT_CHANGED')
            if action == 'PURGE':
                observation = plan.get('observation') or {}
                if (observation.get('status') != 'PASS' or observation.get('references_or_recovery_problems') is not False or
                        observation.get('plan_sha256') != plan.get('previous_plan_sha256') or
                        not observation.get('reviewer') or not observation.get('evidence_files') or
                        (_time(observation.get('reviewed_at')) - _time(selected[0]['at'])).total_seconds() < plan['observation_seconds'] or
                        _time(observation.get('reviewed_at')) > now()):
                    raise StorageError('QUARANTINE_OBSERVATION_REQUIRED')
                if (now() - _time(selected[0]['at'])).total_seconds() < plan['observation_seconds']:
                    raise StorageError('QUARANTINE_OBSERVATION_NOT_ELAPSED')
                for evidence in observation.get('evidence_files', []):
                    if identity(evidence['path']) != {k: v for k, v in evidence.items() if k != 'sha256'} or sha256_file(evidence['path']) != evidence['sha256']:
                        raise StorageError('OBSERVATION_EVIDENCE_UNAVAILABLE')
            free_before = shutil.disk_usage(root).free
            intent = seal({'contract': 'CLEANUP_INTENT_V1', 'at': now().isoformat(),
                'plan_sha256': plan['record_sha256'], 'approval_sha256': approval_sha256,
                'approval': approval, 'asset_id': item['asset_id'], 'action': action,
                'source': str(path), 'target': str(target) if target else None,
                'identity': item, 'free_bytes_before': free_before})
            atomic_new(intent_path, intent)
            def final_check(held_recovery):
                validate_approval(plan, approval, approval_sha256)
                for checked in (path, target):
                    if checked is not None:
                        safe_path(checked.parent)
                        if any(Path(str(checked) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                            raise StorageError('DATABASE_SIDECAR_OR_ACTIVITY_PRESENT')
                if any(Path(marker).exists() for marker in context.get('active_task_markers', [])):
                    raise StorageError('ACTIVE_TASK_PRESENT')
                if action != 'ROLLBACK':
                    # Hold the independent recovery bytes and proof until mutation completes.
                    # Candidate hashing can take minutes; its earlier recovery check is not enough.
                    alternative = item['certificate']['alternative_recovery']
                    for recovery_path, expected_sha, expected_id in (
                            (alternative['path'], alternative['sha256'], None),
                            (item['recovery_proof']['path'], item['recovery_proof']['sha256'], item['recovery_proof']['file_id']),
                            *((b['path'], b['sha256'], b['file_id']) for b in item['certificate']['closure_documents'].values())):
                        safe_path(recovery_path)
                        recovery_stream, _, _ = held_recovery.enter_context(_held_file(Path(recovery_path), writable=False))
                        recovery_stat = os.fstat(recovery_stream.fileno())
                        if (recovery_stat.st_nlink != 1 or
                                expected_id is not None and f'{recovery_stat.st_dev}:{recovery_stat.st_ino}' != expected_id or
                                hashlib.file_digest(recovery_stream, 'sha256').hexdigest() != expected_sha):
                            raise StorageError('RECOVERY_CHANGED_BEFORE_MUTATION')
                    # Recheck cheap activity/approval gates after the potentially long recovery hash.
                    validate_references(item['references'], (original, path),
                        max_age_seconds=context.get('reference_max_age_seconds', 3600))
                    validate_approval(plan, approval, approval_sha256)
                    if any(Path(marker).exists() for marker in context['active_task_markers']):
                        raise StorageError('ACTIVE_TASK_PRESENT')
                    if any(Path(str(checked) + suffix).exists() for checked in (original, path)
                           for suffix in ('-wal', '-shm', '-journal')):
                        raise StorageError('DATABASE_SIDECAR_OR_ACTIVITY_PRESENT')
            with ExitStack() as held_recovery:
                _mutate(path, target, item, final_check=lambda: final_check(held_recovery))
            if path.exists() or target is not None and (identity(target)['file_id'] != item['file_id'] or sha256_file(target) != item['sha256']):
                raise StorageError('POST_OPERATION_VERIFICATION_FAILED')
            record = seal({'contract': 'CLEANUP_RECEIPT_V1', 'at': now().isoformat(),
                'plan_sha256': plan['record_sha256'], 'approval_sha256': approval_sha256,
                'intent_sha256': intent['record_sha256'], 'asset_id': item['asset_id'],
                'state': {'QUARANTINE': 'QUARANTINED', 'ROLLBACK': 'CLEANUP_ELIGIBLE', 'PURGE': 'PURGED'}[action],
                'source': str(path), 'target': str(target) if target else None,
                'logical_deleted_bytes': item['size_bytes'] if action == 'PURGE' else 0,
                'observed_volume_free_delta_bytes': shutil.disk_usage(root).free - free_before,
                'space_delta_attribution': 'OBSERVED_VOLUME_DELTA_INCLUDES_OTHER_PROCESSES',
                'locking': 'WINDOWS_EXCLUSIVE_HANDLE' if os.name == 'nt' else 'POSIX_COOPERATIVE_FLOCK'})
            atomic_new(done, record)
            results.append(record)
    return {'contract': 'CLEANUP_BATCH_RESULT_V1', 'action': action, 'results': results,
            'logical_deleted_bytes': sum(r['logical_deleted_bytes'] for r in results if not r.get('reused')),
            'historical_scope_expanded': False}


class CleanupExecutionError(StorageError):
    def __init__(self, reason, operation_state):
        super().__init__(reason)
        self.operation_state = operation_state


def execute(plan, approval, *, approval_sha256, enabled=False):
    """Failures expose durable per-file progress; partial work is never called no-op."""
    try:
        return _execute(plan, approval, approval_sha256=approval_sha256, enabled=enabled)
    except (StorageError, OSError) as exc:
        completed, pending = [], []
        try:
            verify_seal(plan)
            for item in plan['items']:
                done = _receipt_path(plan, item, 'done')
                intent = _receipt_path(plan, item, 'intent')
                if done.exists():
                    completed.append({'receipt_path': str(done), 'receipt': verify_seal(read_json(done))})
                elif intent.exists():
                    pending.append(str(intent))
        except (StorageError, OSError, KeyError, TypeError):
            pass  # Malformed plans may have no safe receipt location to inspect.
        reason = str(exc) if isinstance(exc, StorageError) else type(exc).__name__
        raise CleanupExecutionError(reason, {'status': 'PARTIAL' if completed else 'BLOCKED',
            'reason': reason, 'completed_items': completed, 'pending_intents': pending,
            'logical_deleted_bytes': sum(r['receipt']['logical_deleted_bytes'] for r in completed),
            'automatic_rollback_performed': False}) from exc


def archive_assets(items, roots, *, max_bytes, consumer_check):
    """Copy only; exit-from-service and destruction require separate cleanup approvals."""
    if not callable(consumer_check):
        raise StorageError('ARCHIVE_CONSUMER_VALIDATION_REQUIRED')
    assets = materialize_plan(items, roots, max_bytes=max_bytes)
    results = []
    for asset in assets:
        resolved = resolve_asset(asset, roots)
        if resolved.get('status') != 'AVAILABLE':
            raise StorageError('ARCHIVE_RESOLVER_FAILED')
        check = consumer_check(asset, resolved)
        if not isinstance(check, dict) or check.get('status') != 'PASS' or not check.get('consumer'):
            raise StorageError('ARCHIVE_CONSUMER_FAILED')
        receipt = seal({'contract': 'ARCHIVE_ASSET_V1', 'asset': asset, 'resolver': resolved,
            'consumer': check, 'source_exit_status': 'SEPARATE_REVIEW_AND_APPROVAL_REQUIRED',
            'original_modified': False, 'created_at': now().isoformat()})
        destination = Path(asset['target_path'] + '.archive.json')
        if destination.exists():
            prior = verify_seal(read_json(destination))
            if prior['asset']['binding_id'] != asset['binding_id']:
                raise StorageError('ARCHIVE_MANIFEST_CONFLICT')
            receipt = prior
        else:
            atomic_new(destination, receipt)
        results.append(receipt)
    return results


def disposition_request(candidate):
    """A present-day ownership decision template, never a synthetic historical SUCCESS."""
    return {'contract': 'HISTORICAL_ASSET_DISPOSITION_V1', 'record_kind': 'NEW_OWNER_DECISION',
        'path': candidate['path'], 'file_id': candidate.get('file_id'), 'sha256': candidate.get('sha256'),
        'historical_task_status': candidate.get('task_status', 'UNKNOWN'),
        'decision': 'PENDING', 'owner': None, 'decided_at': None, 'reason': None,
        'resume_allowed': None, 'recovery_responsibility': None, 'retention_policy': None,
        'technical_reference_closure': 'SEPARATE_EVIDENCE_REQUIRED'}


def record_disposition(decision):
    if (decision.get('contract') != 'HISTORICAL_ASSET_DISPOSITION_V1' or
            decision.get('record_kind') != 'NEW_OWNER_DECISION' or
            decision.get('decision') not in {'RETAIN', 'ARCHIVE_REQUIRED', 'RELEASE_TASK_OWNERSHIP'} or
            not decision.get('owner') or not decision.get('reason') or
            type(decision.get('resume_allowed')) is not bool or
            not decision.get('recovery_responsibility') or not decision.get('retention_policy') or
            _time(decision.get('decided_at')) > now()):
        raise StorageError('EXPLICIT_OWNER_DISPOSITION_REQUIRED')
    current = identity(decision['path'])
    if current['file_id'] != decision.get('file_id') or sha256_file(decision['path']) != decision.get('sha256'):
        raise StorageError('DISPOSITION_FILE_IDENTITY_CHANGED')
    return seal({**decision, 'recorded_at': now().isoformat(),
                 'cleanup_eligibility_granted': False, 'historical_success_asserted': False})
