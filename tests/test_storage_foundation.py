"""CI_SAFE: temp SQLite/files only; no network, live PRIMARY or credentials."""
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_tracker.operations import storage_foundation as sf
from action_tracker.operations import storage_cli
from action_tracker.config import load_settings


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE schema_metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE commit_batches(commit_id TEXT PRIMARY KEY,status TEXT,committed_at TEXT);
        CREATE TABLE products(sku TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE export_sync(commit_id TEXT PRIMARY KEY,master_status TEXT,known_status TEXT,
            offline_status TEXT,master_sha256 TEXT,known_sha256 TEXT,offline_sha256 TEXT,status TEXT);
        INSERT INTO schema_metadata VALUES('schema_family','ACTION_SQLITE_DATA'),
            ('schema_version','2.0.0'),('database_role','PRIMARY');
        INSERT INTO commit_batches VALUES('head1','COMMITTED','2026-10-14T00:00:00Z');
        INSERT INTO products VALUES('1001','original Spanish fact');
        """)
    return path


def projections(db_path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    paths = {key: directory / (key + ".bin") for key in ("master", "known", "offline")}
    for key, path in paths.items():
        path.write_bytes((key + " fixture").encode())
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO export_sync VALUES(?,?,?,?,?,?,?,?)", (
            "head1", "SUCCESS", "SUCCESS", "SUCCESS",
            *(sf.sha256_file(paths[k]) for k in ("master", "known", "offline")), "SUCCESS"))
    return paths


def test_backup_includes_uncheckpointed_wal(tmp_path):
    source = database(tmp_path / "source.db")
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO products VALUES('1002','new committed fact in WAL')")
        writer.commit()
        wal_before = Path(str(source) + "-wal").read_bytes()
        manifest = sf.verified_backup(source, tmp_path / "backup.db", run_id="r1")
        with sf.read_database(tmp_path / "backup.db", immutable=True) as copy:
            assert copy.execute("SELECT value FROM products WHERE sku='1002'").fetchone()[0].endswith("WAL")
        assert Path(str(source) + "-wal").read_bytes() == wal_before
        assert manifest["commit_id"] == "head1" and manifest["recovery_required"] is True
    finally:
        writer.close()


def test_backup_never_overwrites_existing_file(tmp_path):
    source = database(tmp_path / "source.db")
    destination = tmp_path / "backup.db"
    destination.write_bytes(b"original recovery evidence")
    with pytest.raises(sf.StorageError, match="BACKUP_ALREADY_EXISTS"):
        sf.verified_backup(source, destination, run_id="r")
    assert destination.read_bytes() == b"original recovery evidence"


def test_completed_backup_stage_retry_reuses_same_owned_artifact(tmp_path):
    source = database(tmp_path / "source.db")
    target = tmp_path / "backup.db"
    first = sf.verified_backup(source, target, run_id="r", code_version="v1")
    second = sf.verified_backup(source, target, run_id="r", code_version="v1")
    assert second["reused"] is True and second["sha256"] == first["sha256"]
    with pytest.raises(sf.StorageError, match="BACKUP_ALREADY_EXISTS"):
        sf.verified_backup(source, target, run_id="other-task", code_version="v1")


def test_failed_backup_keeps_new_partial_and_failure_evidence(tmp_path):
    source = database(tmp_path / "source.db")
    destination = tmp_path / "backup.db"
    with pytest.raises(sf.StorageError, match="TIME_BUDGET"):
        sf.verified_backup(source, destination, run_id="r", max_seconds=-1)
    assert destination.exists()
    assert json.loads(Path(str(destination) + ".failure.json").read_text())["status"] == "INCOMPLETE_RETAIN_FOR_REVIEW"
    assert not Path(str(destination) + ".asset.json").exists()


def test_same_head_concurrent_write_blocks_publication(tmp_path, monkeypatch):
    source = database(tmp_path / "source.db")
    original = sf.database_identity
    calls = 0
    def identity(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(source) as writer:
                writer.execute("UPDATE products SET value='unexpected mutation'")
        return original(db)
    monkeypatch.setattr(sf, "database_identity", identity)
    with pytest.raises(sf.StorageError, match="BACKUP_HEAD_CHANGED"):
        sf.verified_backup(source, tmp_path / "copy.db", run_id="r")
    assert not (tmp_path / "copy.db.asset.json").exists()


def test_audit_uses_current_export_sync_not_old_daily_hash(tmp_path):
    source = database(tmp_path / "source.db")
    paths = projections(source, tmp_path / "projections")
    paths["master"].write_bytes(b"legitimate later synchronization")
    with sqlite3.connect(source) as db:
        db.execute("UPDATE export_sync SET master_sha256=?", (sf.sha256_file(paths["master"]),))
    assert sf.projection_audit(source, paths)["status"] == "PASS"


def test_audit_blocks_actual_projection_mutation(tmp_path):
    source = database(tmp_path / "source.db")
    paths = projections(source, tmp_path / "projections")
    paths["known"].write_bytes(b"wrong state")
    assert sf.projection_audit(source, paths)["status"] == "BLOCKED"


def test_audit_refuses_empty_projection_set(tmp_path):
    source = database(tmp_path / "source.db")
    with pytest.raises(sf.StorageError, match="PROJECTION_SET_INCOMPLETE"):
        sf.projection_audit(source, {})


def test_restore_checks_bytes_and_does_not_keep_extra_full_copy(tmp_path):
    source = database(tmp_path / "source.db")
    backup = tmp_path / "backup.db"
    manifest = sf.verified_backup(source, backup, run_id="r")
    outside = tmp_path / "not_owned.txt"
    outside.write_text("keep")
    result = sf.rehearse_restore(backup, manifest, tmp_path)
    assert result["status"] == "PASS"
    assert not list(tmp_path.glob("restore-scratch-*"))
    assert outside.read_text() == "keep" and backup.exists()


def test_restore_rejects_tampered_backup(tmp_path):
    source = database(tmp_path / "source.db")
    backup = tmp_path / "backup.db"
    manifest = sf.verified_backup(source, backup, run_id="r")
    backup.write_bytes(b"wrong bytes")
    with pytest.raises(sf.StorageError, match="BACKUP_HASH_MISMATCH"):
        sf.rehearse_restore(backup, manifest, tmp_path)


def test_lock_never_reclaims_unknown_owner(tmp_path):
    existing = tmp_path / "daily-run.lock"
    existing.write_text("unknown operator evidence")
    with pytest.raises(sf.StorageError, match="RUN_ALREADY_ACTIVE"):
        with sf.storage_lock(tmp_path, run_id="r"):
            pass
    assert existing.read_text() == "unknown operator evidence"


def test_lock_release_preserves_changed_owner(tmp_path):
    with sf.storage_lock(tmp_path, run_id="r"):
        (tmp_path / "daily-run.lock").write_text('{"storage_token":"different"}')
    assert (tmp_path / "daily-run.lock").exists()


def test_retention_never_marks_unknown_or_recovery_as_deletable():
    items = [{"created_at": "2026-10-14", "recovery_required": True},
             {"created_at": "2026-01-01", "reference_status": "UNKNOWN"}]
    result = sf.retention_preview(items, keep_latest=1)
    assert all(r["status"] == "RETAIN" and r["delete_allowed"] is False for r in result["assets"])
    assert result["released_bytes"] == 0


def test_retention_candidate_requires_closed_recovery_and_references():
    good = {"contract": "BACKUP_ASSET_V1", "integrity": "PASS", "foreign_keys": "PASS",
            "sha256": "a" * 64, "recovery_required": False, "reference_status": "NO_REFERENCES",
            "task_completed": True, "restore_rehearsed": True}
    result = sf.retention_preview([{**good, "created_at": "2026-10-14"},
                                   {**good, "created_at": "2026-01-01"}], keep_latest=1)
    assert result["assets"][1]["status"] == "ARCHIVE_REVIEW_CANDIDATE"
    assert result["assets"][1]["delete_allowed"] is False


def test_retention_keeps_latest_per_source_not_globally():
    good = {"contract": "BACKUP_ASSET_V1", "integrity": "PASS", "foreign_keys": "PASS",
            "sha256": "a" * 64, "recovery_required": False, "reference_status": "NO_REFERENCES",
            "task_completed": True, "restore_rehearsed": True}
    result = sf.retention_preview([{**good, "source": "A", "created_at": "2026-10-14"},
                                   {**good, "source": "B", "created_at": "2026-01-01"}], keep_latest=1)
    assert all(r["status"] == "RETAIN" for r in result["assets"])


@pytest.mark.parametrize("value", ["../outside", "C:\\private\\file", "C:relative", "/outside", "x:stream"])
def test_locator_rejects_absolute_traversal_and_windows_ads(tmp_path, value):
    with pytest.raises(sf.StorageError, match="ASSET_PATH_ESCAPE"):
        sf.resolve_asset({"root_id": "data", "relative_path": value}, {"data": str(tmp_path)})


def test_offline_archive_is_unavailable_not_empty_official_source():
    result = sf.resolve_asset({"root_id": "archive", "relative_path": "source.json"}, {})
    assert result["status"] == "ASSET_UNAVAILABLE"
    assert "source_empty" not in result


def test_locator_keeps_hash_and_version_when_root_changes(tmp_path):
    root = tmp_path / "new_disk"
    root.mkdir()
    (root / "source.json").write_bytes(b"trusted historical source")
    asset = {"root_id": "archive", "relative_path": "source.json", "asset_id": "a1",
             "sha256": sf.sha256_file(root / "source.json"), "source_version": "v7"}
    result = sf.resolve_asset(asset, {"archive": str(root)})
    assert result["status"] == "AVAILABLE" and result["source_version"] == "v7"
    assert result["hash_verified"] is True and result["source_quality_promoted"] is False


def test_image_index_uses_manifest_identity_not_filename(tmp_path):
    import csv
    image = tmp_path / "objects/2002.png"
    image.parent.mkdir()
    image.write_bytes(b"image fixture")
    manifest = tmp_path / "images.csv"
    with manifest.open("w", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=["sku", "master_image_path", "master_hash",
                                             "source_image_url", "source_hash"])
        w.writeheader()
        w.writerow({"sku": "1001", "master_image_path": str(image),
                    "master_hash": sf.sha256_file(image), "source_image_url": "https://example.test/asset",
                    "source_hash": "source-version"})
    result = sf.image_asset_index(manifest, tmp_path)
    asset = result["assets"][0]
    assert asset["sku"] == "1001" and asset["relative_path"] == "objects/2002.png"
    assert asset["source_version"] == "source-version"
    assert result["fact_records_modified"] is False


def test_configured_archive_is_optional_and_does_not_create_directories(tmp_path):
    cfg = {"project_root": tmp_path, "storage_foundation": {"roots": {"archive": None}}}
    roots = sf.configured_roots(cfg)
    assert "archive" not in roots and not (tmp_path / "runtime").exists()


def test_root_override_does_not_change_other_loaded_configuration(tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        (root / "config").mkdir(parents=True)
        (root / "config/settings.yaml").write_text("paths:\n  master: runtime/master.xlsx\n", encoding="utf-8")
    a = load_settings(project_root_override=roots[0])
    b = load_settings(project_root_override=roots[1])
    assert a["paths"]["master"] == roots[0] / "runtime/master.xlsx"
    assert b["paths"]["master"] == roots[1] / "runtime/master.xlsx"


def test_baseline_resume_creates_no_second_database(tmp_path, monkeypatch):
    root = tmp_path / "data"
    source = database(root / "runtime/db/action_tracker.db")
    ps = projections(source, root / "projections")
    (root / "config").mkdir()
    (root / "config/settings.yaml").write_text(
        "paths:\n  master: projections/master.bin\n  state: projections\n"
        "storage:\n  db_path: runtime/db/action_tracker.db\n", encoding="utf-8")
    (ps["known"].parent / "known_skus.csv").write_bytes(ps["known"].read_bytes())
    (ps["offline"].parent / "offline_skus.csv").write_bytes(ps["offline"].read_bytes())
    (root / "runtime/deployment").mkdir()
    (root / "runtime/deployment/active.json").write_text(json.dumps({"source_checkout": str(tmp_path / "code")}))
    code = {"actual_head": "base", "patch_sha256": hashlib.sha256(b"").hexdigest()}
    monkeypatch.setattr(storage_cli, "code_evidence", lambda _root: (code, b""))
    args = SimpleNamespace(storage_action="baseline", data_root=str(root), output=str(tmp_path / "out"), profile=None)
    first = storage_cli.run_storage_command(args)
    second = storage_cli.run_storage_command(args)
    assert first["reused"] is False and second["reused"] is True
    assert len(list((tmp_path / "out").glob("*.sqlite3"))) == 1
    with sqlite3.connect(source) as db:
        db.execute("UPDATE products SET value='same head different content'")
    with pytest.raises(sf.StorageError, match="BASELINE_EXISTING_BACKUP_STALE"):
        storage_cli.run_storage_command(args)


def test_code_evidence_preserves_staged_and_unstaged_changes(tmp_path):
    import subprocess
    code = tmp_path / 'code'
    code.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(code), *args])
    git('init', '-b', 'main')
    git('config', 'user.name', 'Storage Fixture')
    git('config', 'user.email', 'fixture@example.test')
    (code / 'src').mkdir()
    staged = code / 'src/staged.py'
    unstaged = code / 'src/unstaged.py'
    staged.write_text('old staged\n')
    unstaged.write_text('old unstaged\n')
    git('add', 'src/staged.py', 'src/unstaged.py')
    git('commit', '-m', 'fixture baseline')
    head = git('rev-parse', 'HEAD').decode().strip()
    staged.write_text('new staged\n')
    git('add', 'src/staged.py')
    unstaged.write_text('new unstaged\n')
    data = tmp_path / 'data'
    deployment = data / 'runtime/deployment'
    deployment.mkdir(parents=True)
    (deployment / 'active.json').write_text(json.dumps({'source_checkout': str(code), 'code_sha': head}))
    evidence, patch = storage_cli.code_evidence(data)
    assert set(evidence['tracked_dirty_paths']) == {'src/staged.py', 'src/unstaged.py'}
    assert b'+new staged' in patch and b'+new unstaged' in patch
