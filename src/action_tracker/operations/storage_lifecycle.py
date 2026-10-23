"""Operator-only P2 asset copies and owned scratch; no legacy deletion API."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from .storage_foundation import (StorageError, database_identity, read_database,
                                rehearse_restore, resolve_asset, sha256_file,
                                verified_backup)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path: Path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def relative_target(root: Path, relative: str) -> Path:
    raw = str(relative)
    pure = PurePosixPath(raw.replace('\\', '/'))
    if (not raw or pure.is_absolute() or PureWindowsPath(raw).drive or
            '..' in pure.parts or any(':' in p for p in pure.parts)):
        raise StorageError('ASSET_PATH_ESCAPE')
    root = Path(root).resolve()
    target = (root / Path(*pure.parts)).resolve()
    if target == root or not target.is_relative_to(root):
        raise StorageError('ASSET_PATH_ESCAPE')
    return target


def create_candidate(root: Path, *, protected_roots: list[Path]) -> dict:
    root = Path(root).resolve()
    for protected in protected_roots:
        protected = Path(protected).resolve()
        if root.is_relative_to(protected) or protected.is_relative_to(root):
            raise StorageError('CANDIDATE_PRODUCTION_OVERLAP')
    root.mkdir(parents=True, exist_ok=False)
    marker = {'contract': 'ISOLATED_STORAGE_CANDIDATE_V1', 'root': str(root),
              'owner_token': uuid.uuid4().hex, 'created_at': now(), 'production_enabled': False}
    write_new(root / '.storage_candidate.json', marker)
    return marker


def require_candidate(root: Path):
    root = Path(root).resolve()
    marker = json.loads((root / '.storage_candidate.json').read_text(encoding='utf-8'))
    if (marker.get('contract') != 'ISOLATED_STORAGE_CANDIDATE_V1' or
            marker.get('root') != str(root) or marker.get('production_enabled') is not False):
        raise StorageError('CANDIDATE_OWNERSHIP_INVALID')
    return root


def check_budget(root: Path, expected_bytes: int, *, reserve_bytes=64 * 1024**2):
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise StorageError('INVALID_STORAGE_BUDGET')
    if shutil.disk_usage(root).free < expected_bytes + reserve_bytes:
        raise StorageError('STORAGE_DISK_BUDGET_EXCEEDED')


def materialize_asset(item: dict, roots: dict[str, str]) -> dict:
    """Copy a verified file into an explicitly owned isolated root, never move it."""
    root = require_candidate(Path(roots[item['root_id']]))
    source = Path(item['original_path']).resolve()
    target = relative_target(root, item['relative_path'])
    expected = item.get('sha256')
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise StorageError('ASSET_FULL_HASH_REQUIRED')
    if not source.is_file():
        raise StorageError('ASSET_UNAVAILABLE')
    if source.name.endswith(('-wal', '-shm', '-journal')):
        raise StorageError('LIVE_DATABASE_SIDECAR_COPY_FORBIDDEN')
    signature = (source.stat().st_size, source.stat().st_mtime_ns)
    if signature[0] != item.get('size_bytes') or sha256_file(source) != expected:
        raise StorageError('ASSET_SOURCE_CHANGED')
    if source.suffix.lower() in {'.db', '.sqlite', '.sqlite3'}:
        evidence = item.get('verified_database') or {}
        if (evidence.get('contract') != 'BACKUP_ASSET_V1' or evidence.get('sha256') != expected or
                evidence.get('backup_path') != str(source) or evidence.get('integrity') != 'PASS' or
                evidence.get('foreign_keys') != 'PASS'):
            raise StorageError('DATABASE_VERIFIED_BACKUP_REQUIRED')
        with read_database(source, immutable=True) as db:
            identity = database_identity(db)
        if (identity['commit_id'] != evidence.get('commit_id') or
                any(identity['metadata'].get(k) != evidence.get(k)
                    for k in ('schema_family', 'schema_version', 'database_role'))):
            raise StorageError('DATABASE_IDENTITY_MISMATCH')
    check_budget(root, signature[0] if not target.exists() else 0)
    reused = target.exists()
    if reused:
        if not target.is_file() or sha256_file(target) != expected:
            raise StorageError('CANDIDATE_TARGET_UNKNOWN')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents overwriting an unknown interrupted copy.
        with source.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        if sha256_file(target) != expected:
            raise StorageError('CANDIDATE_COPY_HASH_MISMATCH')
    if (source.stat().st_size, source.stat().st_mtime_ns) != signature:
        raise StorageError('ASSET_SOURCE_CHANGED')
    binding = {k: item.get(k) for k in ('original_path', 'relative_path', 'root_id', 'sku',
                                       'run_id', 'source_version', 'primary_commit_id', 'asset_type')}
    binding_id = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    return {**item, 'asset_id': 'sha256:' + expected, 'binding_id': binding_id,
            'target_path': str(target), 'status': 'COPIED_VERIFIED', 'reused': reused,
            'original_modified': False, 'source_quality_promoted': False}


def materialize_plan(plan: list[dict], roots: dict[str, str], *, max_bytes: int) -> list[dict]:
    # Complete validation/budget before the first large copy; no root is production.
    if sum(item['size_bytes'] for item in plan) > max_bytes:
        raise StorageError('PLAN_COPY_BUDGET_EXCEEDED')
    for name in {item['root_id'] for item in plan}:
        root = require_candidate(Path(roots[name]))
        check_budget(root, sum(i['size_bytes'] for i in plan if i['root_id'] == name))
    for item in plan:
        relative_target(Path(roots[item['root_id']]), item['relative_path'])
    return [materialize_asset(item, roots) for item in plan]


def resolve_legacy_binding(original_path: str, assets: list[dict], roots: dict[str, str]) -> dict:
    """Exact original path adapter; never guess by basename, SKU or content similarity."""
    key = str(PureWindowsPath(original_path)).casefold()
    matches = [a for a in assets if str(PureWindowsPath(a['original_path'])).casefold() == key]
    if not matches:
        return {'status': 'ASSET_UNAVAILABLE', 'reason': 'LEGACY_BINDING_MISSING'}
    identities = {(a['root_id'], a['relative_path'], a.get('sha256')) for a in matches}
    if len(identities) != 1:
        raise StorageError('LEGACY_BINDING_AMBIGUOUS')
    return resolve_asset(matches[0], roots)


def materialize_zip_member(item: dict, roots: dict[str, str], *, max_member_bytes=16*1024**2):
    """Extract one explicitly identified/hash-bound member; container is not promoted."""
    root = require_candidate(Path(roots[item['root_id']]))
    target = relative_target(root, item['relative_path'])
    container = Path(item['container_path']).resolve()
    if not container.is_file():
        return {**item,'status':'ASSET_UNAVAILABLE','reason':'ZIP_CONTAINER_UNAVAILABLE',
                'source_quality_promoted':False}
    expected = item.get('sha256')
    if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
        raise StorageError('ASSET_FULL_HASH_REQUIRED')
    signature = (container.stat().st_size, container.stat().st_mtime_ns)
    with zipfile.ZipFile(container) as package:
        candidates = [i for i in package.infolist() if i.filename == item['member']]
        if len(candidates)!=1: raise StorageError('ZIP_MEMBER_IDENTITY_AMBIGUOUS_OR_MISSING')
        member = candidates[0]
        if member.is_dir() or member.file_size!=item['size_bytes'] or member.file_size>max_member_bytes:
            raise StorageError('ZIP_MEMBER_BUDGET_OR_SIZE_MISMATCH')
        check_budget(root,member.file_size)
        target.parent.mkdir(parents=True,exist_ok=True)
        reused=target.exists()
        if not reused:
            with package.open(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source,output,length=1024*1024)
        if sha256_file(target)!=expected: raise StorageError('ZIP_MEMBER_HASH_MISMATCH')
    if (container.stat().st_size,container.stat().st_mtime_ns)!=signature:
        raise StorageError('ZIP_CONTAINER_CHANGED')
    binding = {k:item.get(k) for k in ('container_path','member','sku','source_url','source_version')}
    return {**item,'original_path':str(container)+'!/'+item['member'],'asset_id':'sha256:'+expected,
        'binding_id':hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest(),
        'target_path':str(target),'status':'COPIED_VERIFIED','reused':reused,
        'container_integrity':'UNVERIFIED_WHOLE_CONTAINER','hash_evidence':'SELECTED_MEMBER_FULL_SHA256_AND_CRC',
        'original_modified':False,'source_quality_promoted':False}


def classify_legacy_database(row: dict, *, evidence_files: list[str], full_hash=None) -> dict:
    purpose = row.get('purpose_class')
    if purpose in {'ACTIVE_PRIMARY', 'ACTIVE_PRIMARY_SIDECAR'}: category = 'ACTIVE_REQUIRED'
    elif purpose in {'RESTORE_VALIDATION_COPY', 'DEVELOPMENT_VALIDATION_COPY_CANDIDATE'}:
        category = 'DEV_TEST_ARTIFACT'
    elif purpose in {'RUN_ID_PRE_RUN_BACKUP', 'OTHER_RECOVERY_BACKUP_CANDIDATE',
                     'LOCALIZATION_PRE_APPLY_BACKUP', 'REPOSITORY_PRESERVATION_BACKUP'}:
        category = 'RECOVERY_REQUIRED'
    else: category = 'UNKNOWN'
    return {**row, 'path': row['path'], 'classification': category,
            'classification_evidence': 'P05_ROLE_CANDIDATE_NOT_CLEANUP_PROOF',
            'referencing_evidence_files': evidence_files, 'reference_count': None,
            'references_verified': False, 'task_status': 'UNKNOWN',
            'recovery_required': None, 'original_backup_exists': None,
            'trusted_restore_validation': None, 'unique_business_data': None,
            'regeneratable': None, 'independent_rollback_obligation': None,
            'sha256': full_hash, 'identity_verified': bool(full_hash),
            'duplicate_content_verified': bool(full_hash), 'current_identity_check': 'UNKNOWN',
            'delete_allowed': False}


def cleanup_preview(items: list[dict], *, at: datetime | None = None) -> dict:
    """Explicit evidence gates, including legacy objects; never grants deletion."""
    at = at or datetime.now(timezone.utc)
    results, whitelist = [], []
    for item in items:
        blockers = []
        if item.get('evidence_contract') == 'LEGACY_CLEANUP_EVIDENCE_V1':
            if not item.get('file_id'): blockers.append('FILE_ID_UNKNOWN')
            if item.get('current_integrity') != 'PASS' or item.get('current_foreign_keys') != 'PASS':
                blockers.append('DATABASE_INTEGRITY_NOT_VERIFIED')
            if item.get('task_terminal_verified') is not True: blockers.append('TASK_OWNERSHIP_TERMINAL_UNKNOWN')
            if item.get('production_dependency_verified') is not True or item.get('production_dependency') is not False:
                blockers.append('PRODUCTION_DEPENDENCY_UNKNOWN_OR_PRESENT')
            if item.get('retention_policy_verified') is not True: blockers.append('RETENTION_POLICY_NOT_VERIFIED')
            if item.get('recovery_release_verified') is not True or item.get('independent_rollback_obligation') is not False:
                blockers.append('INDEPENDENT_RECOVERY_OBLIGATION_UNKNOWN_OR_PRESENT')
        if item.get('identity_verified') is not True: blockers.append('IDENTITY_UNKNOWN')
        if item.get('unique_business_data') is not False: blockers.append('UNIQUE_DATA_UNKNOWN_OR_PRESENT')
        if item.get('recovery_required') is not False: blockers.append('RECOVERY_REQUIRED_OR_UNKNOWN')
        if item.get('references_verified') is not True: blockers.append('REFERENCES_UNKNOWN')
        if type(item.get('reference_count')) is not int or item['reference_count'] != 0:
            blockers.append('REFERENCED_OR_COUNT_UNKNOWN')
        if item.get('task_status') != 'COMPLETED': blockers.append('TASK_NOT_COMPLETED_OR_UNKNOWN')
        alternative = item.get('alternative_recovery') or {}
        if not (alternative.get('status') == 'PASS' and alternative.get('sha256') and
                alternative.get('evidence_path')):
            blockers.append('ALTERNATIVE_RECOVERY_UNVERIFIED')
        try:
            expiry = datetime.fromisoformat(item['retain_until'])
            if expiry.tzinfo is None or expiry > at: blockers.append('RETENTION_NOT_EXPIRED')
        except (KeyError, ValueError, TypeError): blockers.append('RETENTION_UNKNOWN')
        sha = item.get('sha256')
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            blockers.append('FULL_HASH_UNKNOWN')
        if item.get('current_identity_check') != 'PASS': blockers.append('CURRENT_FILE_UNVERIFIED')
        if not blockers:
            path = Path(item.get('path', ''))
            try:
                proof = json.loads(Path(alternative['evidence_path']).read_text(encoding='utf-8'))
                other = Path(alternative['path'])
                if (not path.is_file() or path.stat().st_size != item.get('size_bytes') or
                        sha256_file(path) != sha):
                    blockers.append('CURRENT_FILE_IDENTITY_CHANGED')
                if (proof.get('status') != 'PASS' or proof.get('backup_sha256') != alternative['sha256'] or
                        not other.is_file() or sha256_file(other) != alternative['sha256']):
                    blockers.append('ALTERNATIVE_RECOVERY_IDENTITY_CHANGED')
                if other.is_file() and path.is_file() and other.samefile(path):
                    blockers.append('ALTERNATIVE_RECOVERY_IS_CANDIDATE')
                if Path(str(path) + '-wal').exists() or Path(str(path) + '-shm').exists():
                    blockers.append('DATABASE_SIDECAR_REVIEW_REQUIRED')
            except (OSError, KeyError, ValueError, TypeError):
                blockers.append('RECOVERY_EVIDENCE_UNAVAILABLE')
        result = {**item, 'cleanup_status': 'CONDITIONS_MET_PREVIEW_ONLY' if not blockers else 'RETAIN',
                  'blockers': blockers, 'delete_allowed': False,
                  'estimated_releasable_bytes': item.get('size_bytes', 0) if not blockers else 0}
        results.append(result)
        if not blockers: whitelist.append(result)
    return {'contract': 'SAFE_CLEANUP_PREVIEW_V1', 'assets': results, 'whitelist': whitelist,
            'legacy_deletion_enabled': False, 'released_bytes': 0,
            'estimated_releasable_bytes': sum(i['estimated_releasable_bytes'] for i in whitelist)}


class ScratchTask:
    """A freshly created task directory. Only its own registered bytes can expire."""
    def __init__(self, root: Path, *, owner_task: str, run_id: str, budget_bytes: int):
        self.root = require_candidate(root)
        self.owner_task, self.run_id = owner_task, run_id
        self.token = uuid.uuid4().hex
        self.directory = self.root / ('task-' + self.token)
        check_budget(self.root, budget_bytes)
        self.directory.mkdir(exist_ok=False)
        self.budget_bytes = budget_bytes
        self.assets = {}
        write_new(self.directory / 'owner.json', {'owner_task': owner_task, 'run_id': run_id,
                  'token': self.token, 'created_at': now(), 'budget_bytes': budget_bytes})

    @classmethod
    def resume(cls, root: Path, directory: Path, *, owner_task: str, run_id: str):
        obj = cls.__new__(cls)
        obj.root = require_candidate(root)
        obj.directory = Path(directory).resolve()
        if obj.directory.parent != obj.root:
            raise StorageError('SCRATCH_BOUNDARY_VIOLATION')
        owner = json.loads((obj.directory / 'owner.json').read_text(encoding='utf-8'))
        if (owner.get('owner_task') != owner_task or owner.get('run_id') != run_id or
                obj.directory.name != 'task-' + owner.get('token', '')):
            raise StorageError('SCRATCH_OWNERSHIP_INVALID')
        obj.owner_task, obj.run_id, obj.token = owner_task, run_id, owner['token']
        obj.budget_bytes, obj.assets = owner['budget_bytes'], {}
        journal = obj.directory / 'lifecycle.jsonl'
        # A process may stop immediately after publishing owner.json, before
        # registering its first file. Ownership still has to match exactly.
        if journal.exists():
            with journal.open(encoding='utf-8') as stream:
                for line in stream:
                    record = json.loads(line)
                    path = Path(record['path']).resolve()
                    if (not path.is_relative_to(obj.directory) or record.get('owner_token') != obj.token or
                            record.get('owner_task') != owner_task or record.get('run_id') != run_id):
                        raise StorageError('SCRATCH_OWNERSHIP_INVALID')
                    obj.assets[path] = record
        obj.resumed = True
        return obj

    def register(self, relative: str, *, source: str, expected_bytes: int,
                 recovery_required=True) -> dict:
        path = relative_target(self.directory, relative)
        if path in self.assets: raise StorageError('SCRATCH_ALREADY_REGISTERED')
        if sum(i['expected_bytes'] for i in self.assets.values()) + expected_bytes > self.budget_bytes:
            raise StorageError('SCRATCH_TASK_BUDGET_EXCEEDED')
        check_budget(self.directory, expected_bytes)
        record = {'asset_id': uuid.uuid4().hex, 'path': str(path), 'owner_task': self.owner_task,
                  'run_id': self.run_id, 'owner_token': self.token, 'created_at': now(), 'source': source,
                  'expected_bytes': expected_bytes, 'size_bytes': None, 'task_status': 'RUNNING',
                  'reference_count': None, 'references_verified': False,
                  'recovery_required': recovery_required, 'retain_until': None,
                  'identity_verified': False, 'unique_business_data': None, 'sha256': None}
        self.assets[path] = record
        self._event(record, 'REGISTERED')
        return record

    def _event(self, record, event):
        with (self.directory / 'lifecycle.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({**record, 'event': event, 'event_at': now()}, ensure_ascii=False) + '\n')

    def complete(self, relative: str, *, reference_count: int | None = None,
                 references_verified=False, unique_business_data=None, retain_until=None,
                 alternative_recovery=None) -> dict:
        path = relative_target(self.directory, relative)
        record = self.assets[path]
        record.update(size_bytes=path.stat().st_size, sha256=sha256_file(path), identity_verified=True,
                      current_identity_check='PASS', task_status='COMPLETED',
                      reference_count=reference_count, references_verified=references_verified,
                      unique_business_data=unique_business_data, retain_until=retain_until,
                      alternative_recovery=alternative_recovery)
        if record['size_bytes'] > record['expected_bytes']:
            record['budget_alert'] = True
        self._event(record, 'COMPLETED')
        return dict(record)

    def backup(self, source: Path, relative='primary.sqlite3') -> dict:
        # Recovery assets stay protected; use the same P1 backup and restore primitives.
        path = relative_target(self.directory, relative)
        if path not in self.assets:
            self.register(relative, source=str(source), expected_bytes=Path(source).stat().st_size + 1024**2)
        manifest = verified_backup(source, path, run_id=self.run_id)
        report = self.directory / 'restore_validation.json'
        if report.exists():
            restore = json.loads(report.read_text(encoding='utf-8'))
            if (restore.get('status') != 'PASS' or restore.get('backup_sha256') != manifest['sha256'] or
                    restore.get('commit_id') != manifest['commit_id'] or
                    restore.get('integrity') != 'PASS' or restore.get('foreign_keys') != 'PASS'):
                raise StorageError('SCRATCH_RESTORE_EVIDENCE_INVALID')
        else:
            restore = rehearse_restore(path, manifest, self.directory)
            write_new(report, restore)
        self.complete(relative)
        return {'backup': manifest, 'restore': restore, 'registry': dict(self.assets[path])}

    def discard_owned(self, relative: str):
        if getattr(self, 'resumed', False):
            raise StorageError('SCRATCH_RESUMED_DELETION_DISABLED')
        path = relative_target(self.directory, relative)
        owner = json.loads((self.directory / 'owner.json').read_text(encoding='utf-8'))
        if owner.get('token') != self.token or path not in self.assets:
            raise StorageError('SCRATCH_OWNERSHIP_INVALID')
        record = self.assets[path]
        if sha256_file(path) != record.get('sha256'):
            raise StorageError('SCRATCH_IDENTITY_CHANGED')
        if not cleanup_preview([record])['whitelist']:
            raise StorageError('SCRATCH_CLEANUP_BLOCKED')
        if path.resolve().parent != path.parent or not path.resolve().is_relative_to(self.directory):
            raise StorageError('SCRATCH_BOUNDARY_VIOLATION')
        path.unlink()  # Only a new file registered by this live task instance.
        record['task_status'] = 'DISPOSED'
        record['released_bytes'] = record['size_bytes']
        self._event(record, 'DISPOSED')
        return dict(record)
