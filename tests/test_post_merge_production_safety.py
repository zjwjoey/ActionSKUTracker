"""Regression coverage for the post-merge production-safety hotfix.

All cases use mocks or temporary SQLite databases: no site request, browser
launch or production runtime mutation is allowed here.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_tracker.database.connection import connect
from action_tracker.database.production import CommitBundle, ProductionDatabaseError, ProductionWriter, apply_detail_corrections, database_status
from action_tracker.database.schema import migrate_v2
from action_tracker import cli
from action_tracker.monitor import listing
from action_tracker.operations import entry
from action_tracker.operations.backup import backup_sqlite
from action_tracker.services import runtime
from action_tracker.images.sync import ImageSyncService


def test_windows_pid_probe_never_uses_os_kill(monkeypatch):
    calls = []
    kernel = SimpleNamespace(
        OpenProcess=lambda rights, inherit, pid: calls.append((rights, inherit, pid)) or 77,
        CloseHandle=lambda handle: calls.append(("close", handle)),
    )
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", SimpleNamespace(windll=SimpleNamespace(kernel32=kernel)))
    monkeypatch.setattr(runtime.os, "kill", lambda *_args: pytest.fail("os.kill must not run on Windows"))
    assert runtime.is_process_alive(123) is True
    assert calls == [(0x1000, False, 123), ("close", 77)]


def test_windows_dead_pid_is_false_without_signal(monkeypatch):
    kernel = SimpleNamespace(OpenProcess=lambda *_args: 0, CloseHandle=lambda *_args: pytest.fail("unexpected close"))
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", SimpleNamespace(windll=SimpleNamespace(kernel32=kernel)))
    monkeypatch.setattr(runtime.os, "kill", lambda *_args: pytest.fail("os.kill must not run on Windows"))
    assert runtime.is_process_alive(999) is False


def test_posix_pid_probe_uses_signal_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.os, "kill", lambda pid, signal: calls.append((pid, signal)))
    assert runtime.is_process_alive(123) is True
    assert calls == [(123, 0)]


def test_resume_skips_running_live_pid_and_accepts_dead_pid(tmp_path, monkeypatch):
    root = tmp_path / "reports" / "2026-08-31"
    for name, pid in (("live", 101), ("dead", 202)):
        (root / name).mkdir(parents=True)
        (root / name / "state.json").write_text(json.dumps({"run_id": name, "state": "RUNNING", "pid": pid, "started_at": f"2026-08-31T0{1 if name == 'live' else 2}:00:00"}), encoding="utf-8")
    monkeypatch.setattr(entry, "is_process_alive", lambda pid: int(pid) == 101)
    assert entry._resolve_resume_run(tmp_path / "reports", "2026-08-31", None) == ("dead", True)


def test_resume_restores_delegated_qa_without_recollecting(tmp_path, monkeypatch):
    report = tmp_path / "runtime" / "reports" / "daily" / "2026-08-31" / "outer"
    report.mkdir(parents=True)
    (report / "state.json").write_text(json.dumps({
        "run_id": "outer", "state": "DEGRADED", "pid": 999999,
        "steps": {"COLLECTION": {"status": "SUCCESS", "details": {
            "delegated_run_id": "inner", "commit_status": "FULL_COMMIT", "commit_id": "c1", "qa": {"passed": True, "state": "PASS"},
        }}},
    }), encoding="utf-8")
    captured = {}
    class Runner:
        def __init__(self, **kwargs): captured.update(kwargs)
        def run(self, **_kwargs):
            qa = captured["steps"]["QA"]()
            commit = captured["steps"]["DB_COMMIT"]()
            return {"qa": qa, "commit": commit}
    monkeypatch.setattr(entry, "ProductionRunner", Runner)
    monkeypatch.setattr(entry, "database_path", lambda _cfg: tmp_path / "db.sqlite")
    monkeypatch.setattr(entry, "git_commit_info", lambda: "test")
    cfg = {"project_root": tmp_path, "paths": {"state": tmp_path / "state", "backups": tmp_path / "backups"}}
    result = entry.run_production(cfg, business_date="2026-08-31", resume=True, run_id="outer")
    assert result["qa"].status == "SUCCESS"
    assert result["commit"].status == "SUCCESS"
    assert captured["steps"]["COLLECTION"] is not None


def test_export_resume_repairs_only_pending_projection(tmp_path, monkeypatch):
    report = tmp_path / "runtime" / "reports" / "daily" / "2026-08-31" / "outer"
    report.mkdir(parents=True)
    (report / "state.json").write_text(json.dumps({
        "run_id": "outer", "state": "DEGRADED", "pid": 999999,
        "steps": {"COLLECTION": {"status": "SUCCESS", "details": {
            "delegated_run_id": "inner", "commit_status": "DB_COMMITTED_EXPORT_PENDING", "commit_id": "c1", "qa": {"passed": True, "state": "PASS"},
        }}},
    }), encoding="utf-8")
    captured = {}
    class Runner:
        def __init__(self, **kwargs): captured.update(kwargs)
        def run(self, **_kwargs): return {"export": captured["steps"]["EXPORT"]()}
    monkeypatch.setattr(entry, "ProductionRunner", Runner)
    monkeypatch.setattr(entry, "database_path", lambda _cfg: tmp_path / "db.sqlite")
    monkeypatch.setattr(entry, "git_commit_info", lambda: "test")
    from action_tracker.database import integration
    monkeypatch.setattr(integration, "regenerate_pending_exports", lambda _cfg, *, commit_id: [{"commit_id": commit_id, "status": "SUCCESS"}])
    cfg = {"project_root": tmp_path, "paths": {"state": tmp_path / "state", "backups": tmp_path / "backups"}}
    result = entry.run_production(cfg, business_date="2026-08-31", resume=True, run_id="outer")
    assert result["export"].status == "SUCCESS"
    assert result["export"].details["commit_id"] == "c1"


def test_listing_rejects_false_navigation_and_never_touches_raw_reload(monkeypatch):
    class Page:
        def evaluate(self, _script): return False
        def reload(self, *_args, **_kwargs): pytest.fail("Listing must not call page.reload")
        def wait_for_timeout(self, _millis): pass
    class Browser:
        page = Page()
        def goto(self, _url): return False
        def reload(self): return False
        def sleep(self): pass
    browser = Browser()
    assert listing._goto_page(browser, "https://example.test", "test", 1, 1) is False
    assert listing._wait_for_grid(browser, tries=1) is False


def _primary_db(path: Path) -> None:
    migrate_v2(path, role="PRIMARY")
    with connect(path) as db:
        db.execute("INSERT INTO products(canonical_id,official_sku,name_es,current_price,status,product_url) VALUES('ACT0001001','1001','Old',1.99,'CURRENT','https://old')")
        for language in ("es", "zh"):
            db.execute("INSERT INTO product_localizations(official_sku,language,name,cat1,cat2,spec,description,details,source,review_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", ("1001", language, "Old" if language == "es" else "旧", "A", "B", "old spec", "old desc", "old details", "OFFICIAL_FACT", "VERIFIED"))
        db.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run,started_at,ended_at,schema_version) VALUES('parent','2026-08-31','COMMITTED','PASS',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'2.0.0')")
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES('c1','parent','x','2.0.0',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'COMMITTED')")
        db.execute("INSERT INTO export_sync(commit_id,status) VALUES('c1','SUCCESS')")


def test_detail_correction_primary_only_changes_detail_facts(tmp_path):
    db = tmp_path / "primary.sqlite"; _primary_db(db)
    result = apply_detail_corrections(db, parent_run_id="parent", mode="APPLY", details_by_sku={"1001": {"name_es": "Nuevo", "desc_es": "Nueva descripción", "current_price": "999"}}, source_run_date="2026-08-31")
    assert result["applied_fields"] == 2
    with connect(db) as conn:
        row = conn.execute("SELECT name_es,current_price,status,product_url FROM products WHERE official_sku='1001'").fetchone()
        es = conn.execute("SELECT name,description FROM product_localizations WHERE official_sku='1001' AND language='es'").fetchone()
        assert tuple(row) == ("Nuevo", 1.99, "CURRENT", "https://old")
        assert tuple(es) == ("Nuevo", "Nueva descripción")
        assert conn.execute("SELECT count(*) FROM detail_corrections").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM event_history WHERE event_type='CONTENT_CHANGE'").fetchone()[0] == 1
        correction = conn.execute("SELECT commit_id,base_commit_id,event_count FROM commit_batches WHERE run_id=?", (result["correction_run_id"],)).fetchone()
        assert tuple(correction) == (result["commit_id"], "c1", 1)
        assert conn.execute("SELECT count(*) FROM commit_batches WHERE commit_id='c1' AND bundle_hash='x'").fetchone()[0] == 1
        assert conn.execute("SELECT run_id FROM event_history WHERE event_type='CONTENT_CHANGE'").fetchone()[0] == result["correction_run_id"]
        assert conn.execute("SELECT freshness_status FROM product_localizations WHERE official_sku='1001' AND language='zh'").fetchone()[0] == "STALE"


def test_detail_correction_old_parent_is_blocked_and_immutable(tmp_path):
    db = tmp_path / "primary.sqlite"; _primary_db(db)
    writer = ProductionWriter(db, role="PRIMARY")
    second = writer.commit(CommitBundle(run_id="newer", observation_date="2026-08-31", qa_state="PASS", base_commit_id="c1"))
    with pytest.raises(ProductionDatabaseError, match="DETAIL_CORRECTION_PARENT_NOT_CURRENT_HEAD"):
        apply_detail_corrections(db, parent_run_id="parent", mode="APPLY", details_by_sku={"1001": {"desc_es": "blocked"}})
    with connect(db) as conn:
        assert tuple(conn.execute("SELECT commit_id,bundle_hash,event_count FROM commit_batches WHERE commit_id='c1'").fetchone()) == ("c1", "x", 0)
        assert conn.execute("SELECT commit_id FROM commit_batches WHERE run_id='newer'").fetchone()[0] == second


def test_backfill_never_overwrites_existing_primary_fact(tmp_path):
    db = tmp_path / "primary.sqlite"; _primary_db(db)
    with connect(db) as conn:
        conn.execute("UPDATE product_localizations SET details='' WHERE official_sku='1001' AND language='es'")
    result = apply_detail_corrections(db, parent_run_id="historical", mode="BACKFILL", source_run_date="2026-08-31", details_by_sku={"1001": {"name_es": "Should not overwrite", "details_es": "Filled"}})
    assert result["applied_fields"] == 1
    with connect(db) as conn:
        es = conn.execute("SELECT name,details FROM product_localizations WHERE official_sku='1001' AND language='es'").fetchone()
        assert tuple(es) == ("Old", "Filled")
        assert conn.execute("SELECT base_commit_id FROM commit_batches WHERE run_id=?", (result["correction_run_id"],)).fetchone()[0] == "c1"
        assert conn.execute("SELECT run_id FROM event_history WHERE event_type='CONTENT_CHANGE'").fetchone()[0] == result["correction_run_id"]


def test_new_commit_supersedes_old_pending_export(tmp_path):
    db = tmp_path / "primary.sqlite"; migrate_v2(db, role="PRIMARY")
    writer = ProductionWriter(db, role="PRIMARY")
    first = writer.commit(CommitBundle(run_id="r1", observation_date="2026-08-30", qa_state="PASS"))
    second = writer.commit(CommitBundle(run_id="r2", observation_date="2026-08-31", qa_state="PASS", base_commit_id=first))
    with connect(db) as conn:
        assert conn.execute("SELECT status FROM export_sync WHERE commit_id=?", (first,)).fetchone()[0] == "SUPERSEDED"
        assert conn.execute("SELECT status FROM export_sync WHERE commit_id=?", (second,)).fetchone()[0] == "PENDING"
    assert database_status(db)["pending_export_sync"] == 1


def test_backup_reopens_and_validates_identity(tmp_path):
    db = tmp_path / "primary.sqlite"; migrate_v2(db, role="PRIMARY")
    result = backup_sqlite(db, tmp_path / "backups" / "snapshot.sqlite3", run_id="r1")
    assert result["integrity"] == "PASS" and result["foreign_keys"] == "PASS"
    assert result["database_role"] == "PRIMARY"


def test_sqlite_primary_rejects_formal_daily_runner(monkeypatch, tmp_path, capsys):
    cfg = {"storage": {"mode": "SQLITE_PRIMARY"}, "paths": {"logs": tmp_path / "logs"}, "project_root": tmp_path}
    from action_tracker import config, log
    monkeypatch.setattr(config, "load_settings", lambda: cfg)
    monkeypatch.setattr(config, "ensure_runtime_dirs", lambda _cfg: None)
    monkeypatch.setattr(log, "setup_logging", lambda *_args: None)
    assert cli.main(["daily-run", "--no-dry-run"]) == 2
    assert "FORMAL_RUN_REQUIRES_DATA_UPDATE" in capsys.readouterr().err


def test_qa_resolves_latest_run_directory_and_explicit_id(tmp_path, capsys):
    root = tmp_path / "snapshots"
    for date, run_id, state in (("2026-08-30", "old", "FAIL"), ("2026-08-31", "new", "PASS")):
        directory = root / date / run_id; directory.mkdir(parents=True)
        (directory / "qa_report.json").write_text(json.dumps({"state": state}), encoding="utf-8")
    assert cli._qa({"paths": {"snapshots": root}}, run_id="old") == 0
    assert '"FAIL"' in capsys.readouterr().out
    assert cli._qa({"paths": {"snapshots": root}}) == 0
    assert '"PASS"' in capsys.readouterr().out


def test_image_host_allowlist_rejects_unexpected_source(tmp_path):
    service = ImageSyncService(
        asset_root=tmp_path / "assets", staging_root=tmp_path / "staging", manifest_path=tmp_path / "manifest.csv",
        allowed_hosts=["asset.action.com"], max_retries=0,
    )
    result = service.sync([{"sku": "1001", "image_url": "https://unexpected.example/image.png"}], run_id="r1")
    assert result["download_failed_count"] == 1
    assert service.manifest.records["1001"].error_message == "IMAGE_SOURCE_HOST_NOT_ALLOWED"
