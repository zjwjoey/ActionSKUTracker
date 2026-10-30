"""CI_SAFE: atomic evidence and same-run duplicate storage on generated files."""
import csv
import io
import json
import os
from pathlib import Path

import pytest

from action_tracker.snapshot import _write_csv, write_snapshot, write_staging
from action_tracker.database.integration import snapshot_digest


def setup_run(tmp_path, run='r1'):
    cfg = {'paths': {'snapshots': tmp_path / 'snapshots', 'staging': tmp_path / 'staging'}}
    rows = [{'sku': '1001', 'name': 'Producto, algodón', 'price': 1.99},
            {'sku': '1002', 'name': '第二行\nwith newline', 'price': None}]
    snap = write_snapshot(cfg, '2026-10-30', {
        'run_report': {'run_id': run}, 'sku_delta': rows, 'presence_evidence': rows,
        'product_updates': rows, 'translation_updates': rows})
    return cfg, rows, snap


@pytest.mark.parametrize('rows', [[], [{'sku': '1001', 'text': ' algodón\n棉 ', 'n': None}]])
def test_atomic_csv_preserves_exact_legacy_serialization(tmp_path, rows):
    path = tmp_path / 'facts.csv'
    _write_csv(path, rows)
    legacy = io.StringIO(newline='')
    if rows:
        writer = csv.DictWriter(legacy, fieldnames=list(rows[0]), extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    assert path.read_bytes() == legacy.getvalue().encode('utf-8-sig')
    assert list(tmp_path.iterdir()) == [path]


def test_csv_serialization_failure_preserves_existing_target_and_removes_owned_temp(tmp_path):
    class Broken:
        def __str__(self):
            raise ValueError('injected serialization failure')
    path = tmp_path / 'facts.csv'
    original = b'prior verified bytes'
    path.write_bytes(original)
    with pytest.raises(ValueError, match='injected'):
        _write_csv(path, [{'sku': '1001', 'value': 'ok'}, {'sku': '1002', 'value': Broken()}])
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_publish_failure_preserves_target(tmp_path, monkeypatch):
    path = tmp_path / 'facts.csv'; path.write_bytes(b'original')
    replace = Path.replace
    def fail_publish(self, target):
        if target == path:
            raise PermissionError('locked target')
        return replace(self, target)
    monkeypatch.setattr(Path, 'replace', fail_publish)
    with pytest.raises(PermissionError):
        _write_csv(path, [{'sku': '1001'}])
    assert path.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [path]


def test_same_run_views_share_storage_and_rewrites_keep_other_evidence_unchanged(tmp_path):
    cfg, rows, snap = setup_run(tmp_path)
    before = {p.name: p.read_bytes() for p in snap.iterdir()}
    before_digest = snapshot_digest(snap)
    stage = write_staging(cfg, 'r1', {'sku_changes': rows, 'lifecycle_changes': rows,
        'product_changes': rows, 'translation_changes': rows, 'presence_evidence': rows}, snapshot_dir=snap)
    assert os.path.samefile(stage / 'sku_changes.csv', snap / 'sku_delta.csv')
    assert os.path.samefile(stage / 'lifecycle_changes.csv', stage / 'sku_changes.csv')
    for name in ('product_changes.csv', 'translation_changes.csv', 'presence_evidence.csv'):
        assert (stage / name).read_bytes() == (snap / ('product_updates.csv' if name == 'product_changes.csv'
            else 'translation_updates.csv' if name == 'translation_changes.csv' else name)).read_bytes()
    _write_csv(stage / 'sku_changes.csv', [{'sku': 'different'}])
    assert (stage / 'lifecycle_changes.csv').read_bytes() == before['sku_delta.csv']
    assert {p.name: p.read_bytes() for p in snap.iterdir()} == before
    assert snapshot_digest(snap) == before_digest
    write_snapshot(cfg, '2026-10-30', {'run_report': {'run_id': 'r1'}, 'sku_delta': [{'sku': 'new'}]})
    assert (stage / 'lifecycle_changes.csv').read_bytes() == before['sku_delta.csv']


@pytest.mark.parametrize('failure', ['link_unavailable', 'different_bytes'])
def test_dedupe_fallback_keeps_correct_independent_bytes(tmp_path, monkeypatch, failure):
    cfg, rows, snap = setup_run(tmp_path)
    if failure == 'link_unavailable':
        def unavailable(*_args, **_kwargs):
            raise OSError('filesystem does not support links')
        monkeypatch.setattr(os, 'link', unavailable)
    else:
        rows = [{'sku': 'different'}]
    stage = write_staging(cfg, 'r1', {'sku_changes': rows}, snapshot_dir=snap)
    assert not os.path.samefile(stage / 'sku_changes.csv', snap / 'sku_delta.csv')
    expected = tmp_path / 'expected.csv'; _write_csv(expected, rows)
    assert (stage / 'sku_changes.csv').read_bytes() == expected.read_bytes()
    assert not list(stage.glob('.*.link')) and not list(stage.glob('.*.tmp'))


@pytest.mark.parametrize('bad_source', ['run', 'report', 'outside'])
def test_snapshot_identity_must_match_exact_run_and_authority(tmp_path, bad_source):
    cfg, rows, snap = setup_run(tmp_path)
    if bad_source == 'run':
        with pytest.raises(ValueError, match='identity'):
            write_staging(cfg, 'r2', {'sku_changes': rows}, snapshot_dir=snap)
        return
    if bad_source == 'report':
        (snap / 'run_report.json').write_text(json.dumps({'run_id': 'other'}))
    else:
        outside = tmp_path / 'outside' / 'r1'; outside.mkdir(parents=True)
        (outside / 'run_report.json').write_text(json.dumps({'run_id': 'r1'}))
        snap = outside
    with pytest.raises(ValueError):
        write_staging(cfg, 'r1', {'sku_changes': rows}, snapshot_dir=snap)
    assert not list((cfg['paths']['staging'] / 'r1').glob('*.csv'))


def test_separate_runs_are_never_aliased_by_equal_content(tmp_path):
    cfg, rows, first = setup_run(tmp_path, 'r1')
    _, _, second = setup_run(tmp_path, 'r2')
    stage1 = write_staging(cfg, 'r1', {'sku_changes': rows}, snapshot_dir=first)
    stage2 = write_staging(cfg, 'r2', {'sku_changes': rows}, snapshot_dir=second)
    assert not os.path.samefile(stage1 / 'sku_changes.csv', stage2 / 'sku_changes.csv')


def test_intrastaging_dedupe_does_not_require_or_discover_a_snapshot(tmp_path):
    cfg, rows, snap = setup_run(tmp_path)
    stage = write_staging(cfg, 'r1', {'sku_changes': rows, 'lifecycle_changes': rows})
    assert os.path.samefile(stage / 'sku_changes.csv', stage / 'lifecycle_changes.csv')
    assert not os.path.samefile(stage / 'sku_changes.csv', snap / 'sku_delta.csv')


def test_changed_source_during_link_cannot_replace_the_serialized_payload(tmp_path, monkeypatch):
    cfg, rows, snap = setup_run(tmp_path)
    source = snap / 'sku_delta.csv'
    expected = source.read_bytes()
    link = os.link
    def changed_source(src, dst):
        link(src, dst)
        # Simulate a conflicting writer against generated test evidence only.
        Path(src).write_bytes(b'concurrent invalid bytes')
    monkeypatch.setattr(os, 'link', changed_source)
    stage = write_staging(cfg, 'r1', {'sku_changes': rows}, snapshot_dir=snap)
    assert (stage / 'sku_changes.csv').read_bytes() == expected
    assert not os.path.samefile(stage / 'sku_changes.csv', source)
