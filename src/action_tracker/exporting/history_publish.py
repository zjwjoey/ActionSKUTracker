"""Recoverable publication of a history workbook and its evidence manifest.

The caller must hold the shared storage lock. A durable local journal is kept
until both files have been installed and verified. Readers must still check
the manifest hash; two independent files cannot be replaced simultaneously.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


class HistoryPublishError(ValueError):
    pass


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _copy_synced(src: Path, dst: Path) -> None:
    with src.open('rb') as source, dst.open('xb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())


def _install(src: Path, target: Path) -> None:
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.install', dir=target.parent)
    os.close(fd)
    temp = Path(name)
    try:
        temp.unlink()
        _copy_synced(src, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _journal(output: Path) -> Path:
    return output.with_name('.' + output.name + '.history-journal')


def recover_history_publication(output: Path) -> bool:
    output = Path(output)
    journal = _journal(output)
    if not journal.exists():
        return False
    if not journal.is_dir() or journal.is_symlink():
        raise HistoryPublishError('HISTORY_JOURNAL_INVALID')
    try:
        meta = json.loads((journal / 'journal.json').read_text(encoding='utf8'))
        if meta.get('contract') != 'HISTORY_PUBLICATION_V1' or meta.get('output') != output.name:
            raise HistoryPublishError('HISTORY_JOURNAL_IDENTITY_MISMATCH')
        entries = meta['previous']
        new_hashes = meta['new_sha256']
        if not isinstance(entries, list) or len(entries) != 2 or not isinstance(new_hashes, list) or len(new_hashes) != 2:
            raise HistoryPublishError('HISTORY_JOURNAL_INVALID')
        targets = [output, output.with_suffix('.manifest.json')]
        for i, entry in enumerate(entries):
            if entry.get('name') != targets[i].name or not isinstance(entry.get('existed'), bool):
                raise HistoryPublishError('HISTORY_JOURNAL_IDENTITY_MISMATCH')
            backup = journal / ('old-' + str(i))
            if entry['existed'] and (not backup.is_file() or _hash(backup) != entry.get('sha256')):
                raise HistoryPublishError('HISTORY_JOURNAL_BACKUP_MISMATCH')
            if targets[i].exists():
                if not targets[i].is_file() or targets[i].is_symlink():
                    raise HistoryPublishError('HISTORY_JOURNAL_TARGET_CHANGED')
                allowed = {new_hashes[i]}
                if entry['existed']:
                    allowed.add(entry['sha256'])
                if _hash(targets[i]) not in allowed:
                    raise HistoryPublishError('HISTORY_JOURNAL_TARGET_CHANGED')
            elif entry['existed']:
                raise HistoryPublishError('HISTORY_JOURNAL_TARGET_CHANGED')
        for i, entry in enumerate(entries):
            if entry['existed']:
                _install(journal / ('old-' + str(i)), targets[i])
            else:
                targets[i].unlink(missing_ok=True)
        for i, entry in enumerate(entries):
            if entry['existed'] and _hash(targets[i]) != entry['sha256']:
                raise HistoryPublishError('HISTORY_JOURNAL_RESTORE_MISMATCH')
        shutil.rmtree(journal)
        return True
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, HistoryPublishError):
            raise
        raise HistoryPublishError('HISTORY_JOURNAL_RECOVERY_FAILED') from exc


def publish_history_pair(preview: Path, output: Path, manifest: dict) -> None:
    output = Path(output)
    journal = _journal(output)
    if journal.exists():
        raise HistoryPublishError('HISTORY_JOURNAL_RECOVERY_REQUIRED')
    if _hash(preview) != manifest.get('output_sha256'):
        raise HistoryPublishError('HISTORY_PREVIEW_HASH_MISMATCH')
    staging = Path(tempfile.mkdtemp(prefix='.' + output.name + '.prepare-', dir=output.parent))
    installed_journal = False
    try:
        targets = [output, output.with_suffix('.manifest.json')]
        previous = []
        for i, target in enumerate(targets):
            existed = target.exists()
            if existed and (not target.is_file() or target.is_symlink()):
                raise HistoryPublishError('HISTORY_OUTPUT_INVALID')
            entry = {'name': target.name, 'existed': existed}
            if existed:
                _copy_synced(target, staging / ('old-' + str(i)))
                entry['sha256'] = _hash(staging / ('old-' + str(i)))
            previous.append(entry)
        _copy_synced(preview, staging / 'new-0')
        with (staging / 'new-1').open('x', encoding='utf8', newline='\n') as f:
            f.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
            f.flush(); os.fsync(f.fileno())
        meta = {'contract': 'HISTORY_PUBLICATION_V1', 'output': output.name, 'previous': previous,
                'new_sha256': [_hash(staging / 'new-0'), _hash(staging / 'new-1')]}
        with (staging / 'journal.json').open('x', encoding='utf8', newline='\n') as f:
            f.write(json.dumps(meta, ensure_ascii=False, sort_keys=True) + '\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(staging, journal)
        installed_journal = True
        _install(journal / 'new-0', targets[0])
        _install(journal / 'new-1', targets[1])
        if _hash(targets[0]) != manifest['output_sha256'] or _hash(targets[1]) != _hash(journal / 'new-1'):
            raise HistoryPublishError('HISTORY_PUBLISHED_PAIR_MISMATCH')
        shutil.rmtree(journal)
        installed_journal = False
    except BaseException:
        if installed_journal:
            recover_history_publication(output)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
