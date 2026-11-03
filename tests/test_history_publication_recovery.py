import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from action_tracker.exporting.history_publish import HistoryPublishError, recover_history_publication


def _crash_publish(tmp_path, crash_after):
    output = tmp_path / 'history.xlsx'
    output.write_bytes(b'old workbook')
    output.with_suffix('.manifest.json').write_bytes(b'old manifest')
    preview = tmp_path / 'preview.xlsx'; preview.write_bytes(b'new workbook')
    script = '''
import hashlib, os, pathlib, sys
from action_tracker.exporting import history_publish as hp
root = pathlib.Path(sys.argv[1]); stop = int(sys.argv[2]); real = hp._install; count = 0
def interrupted(src, dst):
 global count
 real(src, dst); count += 1
 if count == stop: os._exit(41)
hp._install = interrupted
hp.publish_history_pair(root/'preview.xlsx', root/'history.xlsx', {'output_sha256':hashlib.sha256(b'new workbook').hexdigest()})
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(crash_after)], env=os.environ.copy(), timeout=30)
    assert result.returncode == 41
    return output


@pytest.mark.parametrize('crash_after', [1, 2])
def test_restart_recovers_pair_after_abrupt_process_exit(tmp_path, crash_after):
    output = _crash_publish(tmp_path, crash_after)
    assert recover_history_publication(output) is True
    assert output.read_bytes() == b'old workbook'
    assert output.with_suffix('.manifest.json').read_bytes() == b'old manifest'
    assert recover_history_publication(output) is False


def test_recovery_does_not_overwrite_intervening_external_change(tmp_path):
    output = _crash_publish(tmp_path, 1)
    output.write_bytes(b'external edit')
    with pytest.raises(HistoryPublishError, match='HISTORY_JOURNAL_TARGET_CHANGED'):
        recover_history_publication(output)
    assert output.read_bytes() == b'external edit'
    assert output.with_name('.' + output.name + '.history-journal').is_dir()


def test_export_resume_after_crash_preserves_stale_lock_until_verified(tmp_path):
    from test_history_export import _history_cfg, _write_seed
    from action_tracker.exporting.history_export import HistoryExportError, export_history
    seed = tmp_path / 'seed.xlsx'; _write_seed(seed); cfg = _history_cfg(tmp_path, seed)
    out = tmp_path / 'exports' / '20260830_Action商品上下架明细.xlsx'; out.parent.mkdir(parents=True)
    out.write_bytes(b'old workbook'); out.with_suffix('.manifest.json').write_bytes(b'old manifest')
    preview = tmp_path / 'preview.xlsx'; preview.write_bytes(b'new workbook')
    script = '''
import hashlib, os, pathlib, sys
from action_tracker.operations.storage_foundation import storage_lock
from action_tracker.exporting import history_publish as hp
root = pathlib.Path(sys.argv[1]); real = hp._install
def interrupted(src, dst):
 real(src, dst); os._exit(41)
hp._install = interrupted
with storage_lock(root/'state', run_id='history-export-2026-08-30'):
 hp.publish_history_pair(root/'preview.xlsx', root/'exports'/'20260830_Action商品上下架明细.xlsx', {'output_sha256':hashlib.sha256(b'new workbook').hexdigest()})
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path)], env=os.environ.copy(), timeout=30)
    assert child.returncode == 41
    lock = Path(cfg['paths']['state']) / 'daily-run.lock'
    assert lock.is_file()
    with pytest.raises(HistoryExportError, match='RUN_ALREADY_ACTIVE'):
        export_history(cfg, export_date='2026-08-30')
    assert lock.is_file()  # Never silently reclaim an unverified owner.
    lock.unlink()  # The test child has exited; this synthetic lock is ours.
    result = export_history(cfg, export_date='2026-08-30')
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf8'))
    assert hashlib.sha256(out.read_bytes()).hexdigest() == manifest['output_sha256']
    assert not out.with_name('.' + out.name + '.history-journal').exists()
