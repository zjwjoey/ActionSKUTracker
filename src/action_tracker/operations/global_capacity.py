"""Whole-project admission, durable peak reservations and bounded-volume gate.

Extends Storage Foundation; never deletes/adopts historical assets. The OS
volume limit is mandatory for write admission, not inferred from a config flag.
Read-only inspection works before consolidation and reports legacy overage.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from .backup_sets import seal, validate_seal
from .pool_files import allocation, os_lock
from .storage_foundation import StorageError

HARD_LIMIT = 15_000_000_000  # Decimal GB; includes temporary peaks.
CONTROL_RESERVE = 262144  # Ledger, lock, atomic replacement, allocation rounding.
_active = ContextVar('global_capacity_lease', default=None)
_command_active = ContextVar('global_capacity_command', default=False)


class CapacityBlocked(StorageError):
    def __init__(self, report):
        self.report = report
        super().__init__('GLOBAL_CAPACITY_BLOCKED:' + report['reason'])


def canonical(path):
    """Reject reparse components instead of silently losing their target bytes."""
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        try:
            value = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
            raise StorageError('GLOBAL_REPARSE_PATH:' + str(part))
    return path


def compact_roots(paths):
    result = []
    for path in sorted({canonical(p) for p in paths}, key=lambda p: len(p.parts)):
        if not any(path.is_relative_to(parent) for parent in result):
            result.append(path)
    return result


def measure(paths):
    """One charge per inode, including unknown files, WAL and control artifacts."""
    seen = set()
    logical = allocated = files = 0
    for root in compact_roots(paths):
        stack = [root]
        while stack:
            path = stack.pop()
            try:
                value = path.lstat()
            except FileNotFoundError:
                # Missing roots may be planned outputs; a disappearing child
                # makes a concurrent inventory untrustworthy.
                if path == root:
                    continue
                raise StorageError('GLOBAL_INVENTORY_CHANGED:' + str(path))
            if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
                raise StorageError('GLOBAL_REPARSE_PATH:' + str(path))
            if stat.S_ISDIR(value.st_mode):
                with os.scandir(path) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(value.st_mode):
                inode = (value.st_dev, value.st_ino)
                if not value.st_ino:
                    raise StorageError('GLOBAL_FILE_ID_UNAVAILABLE')
                if inode in seen:
                    continue
                seen.add(inode)
                logical += value.st_size
                allocated += allocation(path)
                files += 1
            else:
                raise StorageError('GLOBAL_UNKNOWN_FILE_TYPE:' + str(path))
    return {'logical_bytes': logical, 'allocated_bytes': allocated,
            'charged_bytes': max(logical, allocated), 'files': files}


def volume_info(path):
    """Read the actual backing filesystem capacity. A large F: is not a quota."""
    path = canonical(path)
    while not path.exists():
        path = path.parent
    if os.name == 'nt':
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_path = kernel.GetVolumePathNameW
        get_path.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        get_path.restype = w.BOOL
        name = ctypes.create_unicode_buffer(32768)
        if not get_path(str(path), name, len(name)):
            raise ctypes.WinError(ctypes.get_last_error())
        usage = shutil.disk_usage(name.value)
        return {'id': name.value.casefold(), 'total_bytes': usage.total, 'used_bytes': usage.used}
    usage = shutil.disk_usage(path)
    return {'id': path.stat().st_dev, 'total_bytes': usage.total, 'used_bytes': usage.used}


def owner_alive(pid):
    """An openable Windows process object may already have exited.

Treat permission/unknown failures as alive; never steal a reservation just
because an owner cannot be inspected. Do not signal a Windows process.
"""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() not in (87, 1168)
    try:
        code = w.DWORD()
        return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
    finally:
        kernel.CloseHandle(handle)


@dataclass(frozen=True)
class CapacityPolicy:
    max_bytes: int = HARD_LIMIT
    reserve_bytes: int = CONTROL_RESERVE

    def __post_init__(self):
        if (type(self.max_bytes) is not int or not 0 < self.max_bytes <= HARD_LIMIT
                or type(self.reserve_bytes) is not int
                or not 4096 <= self.reserve_bytes < self.max_bytes):
            raise StorageError('GLOBAL_CAPACITY_POLICY_INVALID')


class GlobalCapacity:
    def __init__(self, root, roots, *, write_roots, discovery=(), policy=None):
        self.root = canonical(root)
        self.policy = policy or CapacityPolicy()
        if not roots or not write_roots:
            raise StorageError('GLOBAL_SCOPE_REQUIRED')
        self.roots = compact_roots(roots)
        self.write_roots = compact_roots(write_roots)
        self.discovery = tuple(discovery)
        if any(not any(p.is_relative_to(r) for r in self.roots) for p in [self.root, *self.write_roots]):
            raise StorageError('GLOBAL_WRITER_OUTSIDE_SCOPE')
        self.ledger = self.root / 'reservations.json'
        self.binding = hashlib.sha256(json.dumps({
            'roots': [str(p) for p in self.roots], 'writers': [str(p) for p in self.write_roots],
            'discovery': self.discovery, 'limit': self.policy.max_bytes,
            'reserve': self.policy.reserve_bytes}, sort_keys=True).encode()).hexdigest()

    def scope(self):
        roots = list(self.roots)
        for spec in self.discovery:
            if set(spec) != {'path', 'prefixes'} or not spec['prefixes']:
                raise StorageError('GLOBAL_DISCOVERY_INVALID')
            anchor = canonical(spec['path'])
            if not anchor.is_dir():
                raise StorageError('GLOBAL_DISCOVERY_ANCHOR_MISSING:' + str(anchor))
            for child in anchor.iterdir():
                if child.name.casefold().startswith(tuple(v.casefold() for v in spec['prefixes'])):
                    roots.append(canonical(child))
        return compact_roots(roots)

    def _load(self):
        canonical(self.ledger)
        if not self.ledger.exists():
            return {'contract': 'ACTION_GLOBAL_CAPACITY_V1', 'binding': self.binding, 'leases': {}}
        value = validate_seal(json.loads(self.ledger.read_text('utf-8')), 'ACTION_GLOBAL_CAPACITY_V1')
        if value['binding'] != self.binding:
            raise StorageError('GLOBAL_SCOPE_OR_POLICY_CHANGED')
        return {k: v for k, v in value.items() if k != 'document_sha256'}

    def _save(self, state):
        payload = json.dumps(seal(state), sort_keys=True).encode()
        if len(payload) * 2 + 4096 > self.policy.reserve_bytes:
            raise StorageError('GLOBAL_LEDGER_BUDGET_EXCEEDED')
        canonical(self.ledger)
        temporary = self.root / ('.ledger-' + uuid.uuid4().hex + '.writing')
        created = False
        try:
            with temporary.open('xb') as stream:
                created = True
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.ledger)
        finally:
            # Only this new unpublished control file, never payload/recovery.
            if created:
                temporary.unlink(missing_ok=True)

    def _remaining(self, lease, *, volume=None, single=False):
        if single and volume is not None and lease.get('baseline_volume_used') is not None:
            growth = max(0, volume['used_bytes'] - lease['baseline_volume_used'])
        else:
            growth = max(0, measure(lease['targets'])['charged_bytes'] - lease['baseline_bytes'])
        if growth > lease['peak_bytes']:
            raise StorageError('GLOBAL_OPERATION_RESERVATION_EXCEEDED')
        return lease['peak_bytes'] - growth

    def inspect(self, state=None, *, extra=0, inventory=True):
        if type(extra) is not int or extra < 0:
            raise StorageError('GLOBAL_PEAK_INVALID')
        state = self._load() if state is None else state
        scope = self.scope()
        volumes = [volume_info(p) for p in scope]
        bounded = (len({str(v['id']) for v in volumes}) == 1 and all(
            v['total_bytes'] <= self.policy.max_bytes for v in volumes))
        volume = volumes[0] if bounded and 'used_bytes' in volumes[0] else None
        if not inventory and volume is not None:
            # This counts *all* allocations on the bounded filesystem,
            # including metadata/unknown files, without per-field tree scans.
            usage = {'charged_bytes': volume['used_bytes'], 'allocated_bytes': volume['used_bytes'],
                     'logical_bytes': None, 'files': None, 'measurement': 'bounded_volume_used'}
        else:
            usage = measure(scope)
            if volume is not None:
                usage['charged_bytes'] = max(usage['charged_bytes'], volume['used_bytes'])
        outstanding = sum(self._remaining(v, volume=volume, single=len(state['leases']) == 1)
                          for v in state['leases'].values())
        projected = usage['charged_bytes'] + outstanding + extra + self.policy.reserve_bytes
        report = {'contract': 'ACTION_GLOBAL_CAPACITY_STATUS_V1', **usage,
                  'max_bytes': self.policy.max_bytes, 'outstanding_bytes': outstanding,
                  'requested_peak_bytes': extra, 'projected_bytes': projected,
                  'scope': [str(p) for p in scope], 'automatic_cleanup': False}
        if projected > self.policy.max_bytes:
            return {**report, 'status': 'BLOCKED', 'reason': 'TOTAL_OR_PEAK_EXCEEDS_LIMIT'}
        if not bounded:
            return {**report, 'status': 'BLOCKED', 'reason': 'BOUNDED_VOLUME_REQUIRED'}
        if volume is not None and projected > volume['total_bytes']:
            return {**report, 'status': 'BLOCKED', 'reason': 'VOLUME_PEAK_SPACE_INSUFFICIENT'}
        return {**report, 'status': 'PASS', 'reason': 'WITHIN_BOUNDED_VOLUME'}

    def require(self, state=None, *, extra=0, inventory=True):
        report = self.inspect(state, extra=extra, inventory=inventory)
        if report['status'] != 'PASS':
            raise CapacityBlocked(report)
        return report

    @contextmanager
    def lease(self, task_id, peak_bytes, *, targets=None, resume=False):
        if not isinstance(task_id, str) or not task_id or len(task_id) > 160:
            raise StorageError('GLOBAL_TASK_ID_INVALID')
        if type(peak_bytes) is not int or peak_bytes <= 0:
            raise StorageError('GLOBAL_PEAK_REQUIRED')
        targets = compact_roots(targets or self.write_roots)
        if any(not any(p.is_relative_to(r) for r in self.write_roots) for p in targets):
            raise StorageError('GLOBAL_TARGET_OUTSIDE_WRITERS')
        # Capacity and backing-volume checks precede even directory creation.
        self.require(extra=0 if resume and self.ledger.exists() else peak_bytes)
        self.root.mkdir(parents=True, exist_ok=True)
        with os_lock(self.root / 'capacity.lock', wait=30):
            state = self._load()
            previous = state['leases'].get(task_id)
            if previous:
                alive = owner_alive(previous['pid'])
                own_failed = previous['pid'] == os.getpid() and previous['state'] == 'FAILED_RETAINED'
                if not resume or (alive and not own_failed):
                    raise StorageError('GLOBAL_TASK_ACTIVE_OR_RESUME_REQUIRED')
                if previous['peak_bytes'] != peak_bytes or previous['targets'] != [str(p) for p in targets]:
                    raise StorageError('GLOBAL_RESUME_BINDING_CHANGED')
                self.require(state)
                value = previous
            else:
                self.require(state, extra=peak_bytes)
                for item in state['leases'].values():
                    if any(a.is_relative_to(Path(b)) or Path(b).is_relative_to(a)
                           for a in targets for b in item['targets']):
                        raise StorageError('GLOBAL_WRITER_ALREADY_RESERVED')
                value = {'task_id': task_id, 'peak_bytes': peak_bytes,
                         'targets': [str(p) for p in targets],
                         'baseline_bytes': measure(targets)['charged_bytes'],
                         'baseline_volume_used': volume_info(targets[0]).get('used_bytes')}
            value.update(token=uuid.uuid4().hex, pid=os.getpid(), state='RUNNING')
            state['leases'][task_id] = value
            self._save(state)
        lease = CapacityLease(self, task_id, value['token'])
        token = _active.set(lease)
        try:
            yield lease
            lease.checkpoint()
        except BaseException as exc:
            try:
                with os_lock(self.root / 'capacity.lock', wait=30):
                    state = self._load()
                    state['leases'][task_id]['state'] = 'FAILED_RETAINED'
                    self._save(state)
            except Exception as evidence_error:
                exc.add_note('Global reservation retained; failure receipt: ' + str(evidence_error))
            raise
        else:
            with os_lock(self.root / 'capacity.lock', wait=30):
                state = self._load()
                del state['leases'][task_id]
                self._save(state)
        finally:
            _active.reset(token)


class CapacityLease:
    def __init__(self, gate, task_id, token):
        self.gate, self.task_id, self.token = gate, task_id, token

    def checkpoint(self, extra=0):
        with os_lock(self.gate.root / 'capacity.lock', wait=30):
            state = self.gate._load()
            lease = state['leases'][self.task_id]
            if lease['token'] != self.token:
                raise StorageError('GLOBAL_LEASE_OWNER_CHANGED')
            report = self.gate.require(state, inventory=False)
            volume = volume_info(lease['targets'][0])
            remaining = self.gate._remaining(lease,
                volume=volume if 'used_bytes' in volume else None, single=len(state['leases']) == 1)
            if type(extra) is not int or not 0 <= extra <= remaining:
                raise StorageError('GLOBAL_OPERATION_RESERVATION_EXCEEDED')
            return report

    def check_path(self, path):
        path = canonical(path)
        state = self.gate._load()
        if not any(path.is_relative_to(Path(p)) for p in state['leases'][self.task_id]['targets']):
            raise StorageError('GLOBAL_WRITE_OUTSIDE_RESERVATION')
        return path

    def write_bytes(self, path, data):
        """For bounded adapters; arbitrary libraries are fenced by the volume."""
        path = self.check_path(path)
        self.checkpoint(len(data))
        with path.open('xb') as stream:
            stream.write(data)
        self.checkpoint()


def active_checkpoint(extra=0, *, path=None):
    lease = _active.get()
    if lease:
        if path is not None:
            lease.check_path(path)
        lease.checkpoint(extra)


def active_path(path):
    """Cheap pre-open fencing for an already-created pool/workspace instance."""
    lease = _active.get()
    if lease:
        lease.check_path(path)


def configured_capacity(cfg):
    raw = cfg.get('global_storage') or {}
    if not isinstance(raw, dict):
        raise StorageError('GLOBAL_CONFIG_OBJECT_REQUIRED')
    if type(raw.get('enabled', False)) is not bool:
        raise StorageError('GLOBAL_ENABLED_BOOLEAN_REQUIRED')
    if not raw.get('enabled'):
        return None
    allowed = {'enabled', 'root', 'roots', 'write_roots', 'discovery', 'max_bytes', 'peak_bytes'}
    if set(raw) - allowed:
        raise StorageError('GLOBAL_CONFIG_UNKNOWN_FIELDS')
    for name in ('roots', 'write_roots'):
        if (not isinstance(raw.get(name), list) or not raw[name]
                or not all(isinstance(p, (str, Path)) and str(p) for p in raw[name])):
            raise StorageError('GLOBAL_SCOPE_REQUIRED')
    base = Path(cfg['project_root'])
    def resolve(p):
        path = Path(p)
        return path if path.is_absolute() else base / path
    gate = GlobalCapacity(resolve(raw['root']), [resolve(p) for p in raw['roots']],
                          write_roots=[resolve(p) for p in raw['write_roots']],
                          discovery=raw.get('discovery', ()),
                          policy=CapacityPolicy(max_bytes=raw.get('max_bytes', HARD_LIMIT)))
    # Fence opaque native writers (browser profiles, image libraries, SQLite)
    # from redirecting to an unbudgeted directory through another config key.
    required = [p for p in cfg.get('paths', {}).values() if isinstance(p, (str, Path)) and str(p)]
    for section, names in (
            ('browser', ('profile_dir', 'cookies_path')),
            ('images', ('asset_root', 'staging_root', 'derivative_root', 'manifest_path')),
            ('storage', ('db_path',))):
        required.extend(cfg.get(section, {}).get(n) for n in names if cfg.get(section, {}).get(n))
    if (cfg.get('backup_pool') or {}).get('enabled'):
        required.append(cfg['backup_pool'].get('root') or 'runtime/backup_pool')
    tracker = (cfg.get('localization') or {}).get('source_conflict_tracking') or {}
    if tracker.get('enabled') and tracker.get('path'):
        required.append(tracker['path'])
    for path in required:
        if not any(canonical(resolve(path)).is_relative_to(p) for p in gate.write_roots):
            raise StorageError('GLOBAL_CONFIGURED_WRITER_OUTSIDE_SCOPE:' + str(path))
    return gate


@contextmanager
def operation_scope(cfg, *, task_id, resume=False):
    gate = configured_capacity(cfg)
    if gate is None:
        yield None
        return
    parent = _active.get()
    if parent:
        if parent.gate.binding != gate.binding:
            raise StorageError('GLOBAL_NESTED_SCOPE_CHANGED')
        parent.checkpoint()
        yield parent
        return
    peak = cfg['global_storage'].get('peak_bytes')
    with gate.lease(task_id, peak, resume=resume) as lease:
        yield lease


@contextmanager
def command_scope(cfg, args):
    """Guard before ensure_runtime_dirs/logging and route child/library temp I/O.

The CLI is synchronous. A process lock prevents concurrent environment changes;
library task scopes only reserve space and do not mutate process environment.
"""
    gate = configured_capacity(cfg)
    if gate is None:
        yield cfg
        return
    if _command_active.get() and (cfg.get('_transient_workspace')
            or cfg.get('paths', {}).get('temp') is None
            or args.command in {'data-update', 'production-run', 'daily-run'}):
        with operation_scope(cfg, task_id='nested-command'):
            yield cfg
        return
    import tempfile
    command = str(args.command)
    task = 'cli:' + command + ':' + str(getattr(args, 'run_id', None)
        or getattr(args, 'date', None) or getattr(args, 'backup_task_id', None) or 'interactive')
    with _environment_lock, operation_scope(cfg, task_id=task,
                                           resume=bool(getattr(args, 'resume', False))) as lease:
        for name in ('root', 'output', 'output_dir', 'output_directory', 'scratch_root',
                     'destination', 'restore_root', 'workspace'):
            value = getattr(args, name, None)
            if value is not None:
                lease.check_path(value)
        task_key = hashlib.sha256(lease.task_id.encode()).hexdigest()[:24]
        workspace = None
        operations = {'data-update', 'production-run', 'daily-run'}
        if command not in operations and cfg.get('paths', {}).get('temp') is not None:
            from .storage_lifecycle import prepare_run_workspace
            cfg, workspace = prepare_run_workspace(cfg, run_id=task_key,
                report_dir=gate.root / 'commands' / task_key, owner_task='cli:' + command)
            temporary = cfg['paths']['temp']
        else:
            # Operations owns its business run_id/P2 pointer. This directory
            # only contains system-library temp until that envelope is opened.
            temporary = gate.root / 'command-temp' / task_key
        lease.check_path(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        old = {k: os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR')}
        old_tempdir = tempfile.tempdir
        command_token = _command_active.set(True)
        try:
            for key in old:
                os.environ[key] = str(temporary)
            tempfile.tempdir = str(temporary)
            yield cfg
        finally:
            _command_active.reset(command_token)
            tempfile.tempdir = old_tempdir
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if workspace is not None:
                import sys
                from .storage_lifecycle import record_run_workspace
                failure = sys.exc_info()[1]
                try:
                    record_run_workspace(workspace)
                except Exception as evidence_error:
                    if failure is None:
                        raise
                    failure.add_note('Task inventory retained; receipt failure: ' + str(evidence_error))


from threading import RLock
_environment_lock = RLock()
