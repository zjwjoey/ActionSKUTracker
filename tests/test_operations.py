import json
from pathlib import Path

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.operations.contracts import StepResult
from action_tracker.operations.runner import ProductionRunner
from action_tracker.operations.service import OperationsService
from action_tracker.operations import entry
from action_tracker.operations import runner as operations_runner
from action_tracker.operations.preflight import production_preflight


def _steps(**overrides):
    names = ("PREFLIGHT", "BACKUP", "COLLECTION", "QA", "DB_COMMIT", "EXPORT", "IMAGE", "KNOWLEDGE", "AI", "AUTO_APPROVAL", "REVIEW", "REPORT")
    return {n: overrides.get(n, lambda: StepResult("SUCCESS")) for n in names}


def test_duplicate_trigger_is_blocked(tmp_path: Path):
    lock_dir = tmp_path / "locks"; lock_dir.mkdir()
    first = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r1", steps=_steps(), lock_dir=lock_dir)
    first.lock.acquire("r1")
    try:
        second = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r2", steps=_steps(), lock_dir=lock_dir)
        try:
            second.run()
        except RuntimeError as exc:
            assert "RUN_ALREADY_ACTIVE" in str(exc)
        else:
            raise AssertionError("duplicate trigger was not blocked")
    finally:
        first.lock.release()


def test_collection_failure_never_reaches_commit(tmp_path: Path):
    steps = _steps(COLLECTION=lambda: StepResult("FAILED", error_code="COLLECTION_FAILED"))
    result = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r1", steps=steps, lock_dir=tmp_path / "locks").run()
    assert result["state"] == "FAILED"
    assert "DB_COMMIT" not in result["steps"]


def test_export_failure_after_commit_is_degraded_and_resume_does_not_repeat_commit(tmp_path: Path):
    calls = {"commit": 0, "export": 0}
    def commit(): calls["commit"] += 1; return StepResult("SUCCESS")
    def export():
        calls["export"] += 1
        return StepResult("FAILED", error_code="EXPORT_FAILED") if calls["export"] == 1 else StepResult("SUCCESS")
    runner = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r1", steps=_steps(DB_COMMIT=commit, EXPORT=export), lock_dir=tmp_path / "locks")
    first = runner.run(); assert first["state"] == "DEGRADED" and calls["commit"] == 1
    assert any(error["code"] == "EXPORT_FAILED" for error in first["errors"])
    second = runner.run(resume=True); assert second["state"] == "SUCCESS" and calls["commit"] == 1 and calls["export"] == 2


def test_from_step_requires_successful_dependencies(tmp_path: Path):
    runner = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r1", steps=_steps(), lock_dir=tmp_path / "locks")
    try: runner.run(from_step="EXPORT")
    except ValueError as exc: assert "FROM_STEP_DEPENDENCY" in str(exc)
    else: raise AssertionError("unsafe partial start was allowed")


def test_operations_service_is_read_model_and_health(tmp_path: Path):
    db = tmp_path / "db.sqlite"; migrate_v2(db, role="PRIMARY")
    with connect(db) as conn:
        conn.execute("INSERT INTO products(canonical_id,official_sku,status,current_price,product_url) VALUES('c1','1001','CURRENT',2.5,'https://example.test/p')")
        conn.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('r1','2026-08-30','COMMITTED','PASS',0)")
    service = OperationsService(db, reports_root=tmp_path / "reports", lock_path=tmp_path / "lock")
    status = service.system_status(); assert status["current_sku"] == 1 and status["database"]["metadata"]["database_role"] == "PRIMARY"
    assert service.latest_run()["run_id"] == "r1"
    assert service.data_quality()["invalid_price"] == 0
    assert service.health()["state"] == "HEALTHY"
    assert service.safe_action("retry-export-sync")["status"] == "CONFIRMATION_REQUIRED"
    assert service.safe_action("retry-export-sync", confirmed=True)["status"] == "READY"
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM operations_actions").fetchone()[0] == 1


def test_production_entry_blocks_when_delegated_commit_is_not_confirmed(tmp_path: Path, monkeypatch):
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(entry, "database_path", lambda _cfg: db)
    monkeypatch.setattr(entry, "validate_production_database", lambda _db: {"integrity": "PASS", "foreign_keys": "PASS"})
    monkeypatch.setattr(entry, "production_preflight", lambda *_args: {"storage_mode": "SQLITE_PRIMARY"})
    monkeypatch.setattr(entry, "backup_sqlite", lambda *_args, **_kwargs: {"status": "SUCCESS"})
    monkeypatch.setattr(entry, "git_commit_info", lambda: "test")
    from action_tracker.orchestrator import daily
    monkeypatch.setattr(daily, "run_daily", lambda *_args, **_kwargs: {
        "run_id": "inner-1", "qa": {"passed": True, "state": "PASS"}, "commit_status": "DB_COMMIT_FAILED",
    })
    cfg = {"project_root": str(tmp_path), "paths": {"state": tmp_path / "state", "backups": tmp_path / "backups"}}
    result = entry.run_production(cfg, business_date="2026-08-30")
    assert result["state"] == "BLOCKED"
    assert result["steps"]["COLLECTION"]["error_code"] == "FORMAL_COMMIT_NOT_CONFIRMED"
    assert any(error["code"] == "FORMAL_COMMIT_NOT_CONFIRMED" for error in result["errors"])


def test_production_entry_marks_export_pending_as_degraded(tmp_path: Path, monkeypatch):
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(entry, "database_path", lambda _cfg: db)
    monkeypatch.setattr(entry, "validate_production_database", lambda _db: {"integrity": "PASS", "foreign_keys": "PASS"})
    monkeypatch.setattr(entry, "production_preflight", lambda *_args: {"storage_mode": "SQLITE_PRIMARY"})
    monkeypatch.setattr(entry, "backup_sqlite", lambda *_args, **_kwargs: {"status": "SUCCESS"})
    monkeypatch.setattr(entry, "git_commit_info", lambda: "test")
    from action_tracker.orchestrator import daily
    monkeypatch.setattr(daily, "run_daily", lambda *_args, **_kwargs: {
        "run_id": "inner-1", "qa": {"passed": True, "state": "PASS"}, "commit_status": "DB_COMMITTED_EXPORT_PENDING",
    })
    cfg = {"project_root": str(tmp_path), "paths": {"state": tmp_path / "state", "backups": tmp_path / "backups"}}
    result = entry.run_production(cfg, business_date="2026-08-30")
    assert result["state"] == "DEGRADED"
    assert result["steps"]["DB_COMMIT"]["status"] == "SUCCESS"
    assert result["steps"]["EXPORT"]["error_code"] == "EXPORT_PENDING"


def test_operations_service_links_outer_and_delegated_run(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    migrate_v2(db, role="PRIMARY")
    with connect(db) as conn:
        conn.execute("INSERT INTO runs(run_id,run_date,status,qa_state,dry_run) VALUES('inner-1','2026-08-30','COMMITTED','PASS',0)")
    reports = tmp_path / "reports" / "2026-08-30" / "outer-1"
    reports.mkdir(parents=True)
    (reports / "state.json").write_text('{"run_id":"outer-1","business_date":"2026-08-30","state":"SUCCESS","steps":{"COLLECTION":{"details":{"delegated_run_id":"inner-1","qa":{"state":"PASS"}}}}}', encoding="utf-8")
    (reports / "summary.json").write_text('{"run_id":"outer-1"}', encoding="utf-8")
    service = OperationsService(db, reports_root=tmp_path / "reports")
    history = service.run_history()
    assert any(row["run_id"] == "outer-1" and row["delegated_run_id"] == "inner-1" for row in history)
    outer = service.run_detail("outer-1")
    assert outer["delegated_run_id"] == "inner-1" and outer["database"]["run_id"] == "inner-1"
    inner = service.run_detail("inner-1")
    assert inner["operations_run_id"] == "outer-1" and "summary.json" in inner["artifacts"]


def test_report_failure_after_commit_is_degraded_and_persisted(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(operations_runner, "write_daily_report", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    result = ProductionRunner(root=tmp_path / "reports", business_date="2026-08-30", run_id="r1", steps=_steps(), lock_dir=tmp_path / "locks").run()
    assert result["state"] == "DEGRADED"
    assert result["steps"]["REPORT"]["status"] == "FAILED"
    assert any(error["code"] == "REPORT_WRITE_FAILED" for error in result["errors"])


def test_production_resume_explicit_and_auto_selection(tmp_path: Path, monkeypatch):
    reports = tmp_path / "runtime" / "reports" / "daily" / "2026-08-30"
    (reports / "old").mkdir(parents=True)
    (reports / "new-success").mkdir(parents=True)
    (reports / "old" / "state.json").write_text('{"run_id":"old","state":"DEGRADED","started_at":"2026-08-30T01:00:00","pid":999999}', encoding="utf-8")
    (reports / "new-success" / "state.json").write_text('{"run_id":"new-success","state":"SUCCESS","started_at":"2026-08-30T02:00:00","pid":999999}', encoding="utf-8")
    assert entry._resolve_resume_run(tmp_path / "runtime" / "reports" / "daily", "2026-08-30", "old") == ("old", False)
    assert entry._resolve_resume_run(tmp_path / "runtime" / "reports" / "daily", "2026-08-30", None) == ("old", True)
    try:
        entry._resolve_resume_run(tmp_path / "runtime" / "reports" / "daily", "2026-08-30", "missing")
    except FileNotFoundError as exc:
        assert str(exc) == "RUN_STATE_MISSING"
    else:
        raise AssertionError("missing explicit run id was not rejected")
    try:
        entry.run_production({"project_root": str(tmp_path), "paths": {"state": tmp_path / "state", "backups": tmp_path / "backups"}}, business_date="2026-08-30", run_id="old")
    except ValueError as exc:
        assert str(exc) == "RUN_ID_ONLY_ALLOWED_WITH_RESUME"
    else:
        raise AssertionError("run id without resume was not rejected")


def test_preflight_checks_mode_and_writable_paths(tmp_path: Path):
    db = tmp_path / "runtime" / "db" / "action.db"
    db.parent.mkdir(parents=True)
    migrate_v2(db, role="PRIMARY")
    paths = {key: tmp_path / "runtime" / key for key in ("backups", "reports", "images", "exports")}
    for path in paths.values(): path.mkdir(parents=True)
    cfg = {"project_root": tmp_path, "storage": {"mode": "SQLITE_PRIMARY", "db_path": db}, "paths": {**paths, "master": tmp_path / "runtime" / "master" / "Action_Master.xlsx"}, "operations": {"min_free_disk_bytes": 1}}
    result = production_preflight(cfg, db, paths["reports"])
    assert result["storage_mode"] == "SQLITE_PRIMARY" and len(result["config_hash"]) == 64
    assert "api_key" not in json.dumps(result["config_snapshot"], ensure_ascii=False)
    cfg["storage"]["mode"] = "EXCEL_PRIMARY"
    try: production_preflight(cfg, db, paths["reports"])
    except Exception as exc: assert "SQLITE_PRIMARY" in str(exc)
    else: raise AssertionError("non-primary mode was accepted")
    cfg["storage"]["mode"] = "SQLITE_PRIMARY"
    cfg["operations"]["min_free_disk_bytes"] = 10**18
    try: production_preflight(cfg, db, paths["reports"])
    except Exception as exc: assert "DISK_SPACE_LOW" in str(exc)
    else: raise AssertionError("low disk threshold was accepted")
