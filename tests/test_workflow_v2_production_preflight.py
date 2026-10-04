import argparse
import os
from pathlib import Path

import yaml

from action_tracker.database.connection import connect
from action_tracker.database.schema import migrate_v2
from action_tracker.config import config_evidence, load_settings
from scripts.workflow_v2_production_preflight import run


BRANCH = "deploy/workflow-v2-production-20261004"


def _profile(tmp_path, **changes):
    profile = yaml.safe_load(Path("config/workflow_v2_production_profile.yaml").read_text(encoding="utf-8"))
    for path, value in changes.items():
        cursor = profile
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    return path


def _args(tmp_path, config, *, branch=BRANCH, head=None):
    source = Path(__file__).resolve().parents[1]
    return argparse.Namespace(
        source_root=source, data_root=tmp_path, config=config,
        expected_branch=branch, expected_head=head or __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
        expected_ref=None,
    )


def _valid_environment(tmp_path):
    for name in ("db", "snapshots", "staging", "state", "dictionary", "review_queue", "images", "exports", "logs", "backups", "temp"):
        (tmp_path / "runtime" / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dictionary").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "runtime" / "db" / "action_tracker.db"
    migrate_v2(db_path, role="PRIMARY")
    with connect(db_path) as db:
        db.execute("INSERT INTO commit_batches(commit_id,run_id,bundle_hash,schema_version,started_at,committed_at,status) VALUES(?,?,?,?,?,?,?)", ("c1", "r1", "b", "2.0.0", "2026-10-04", "2026-10-04", "COMMITTED"))
    return db_path


def test_preflight_normal_environment_and_historical_backlog(tmp_path, monkeypatch):
    _valid_environment(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    report = run(_args(tmp_path, _profile(tmp_path)))
    assert report["status"] == "PASS"
    assert report["production_data_modified"] is False
    assert report["qwen_call_performed"] is False


def test_preflight_wrong_branch_fails(tmp_path):
    _valid_environment(tmp_path)
    report = run(_args(tmp_path, _profile(tmp_path), branch="wrong"))
    assert report["status"] == "NOT_READY"


def test_preflight_wrong_head_fails(tmp_path):
    _valid_environment(tmp_path)
    report = run(_args(tmp_path, _profile(tmp_path), head="0" * 40))
    assert report["status"] == "NOT_READY"


def test_preflight_missing_database_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    report = run(_args(tmp_path, _profile(tmp_path)))
    assert report["status"] == "NOT_READY"


def test_preflight_integrity_and_storage_contract(tmp_path, monkeypatch):
    db = _valid_environment(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    db.write_bytes(b"not sqlite")
    assert run(_args(tmp_path, _profile(tmp_path)))["status"] == "NOT_READY"


def test_preflight_missing_key_fails(tmp_path, monkeypatch):
    _valid_environment(tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert run(_args(tmp_path, _profile(tmp_path)))["status"] == "NOT_READY"


def test_preflight_unsafe_switches_fail(tmp_path, monkeypatch):
    _valid_environment(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for key, value in {
        "knowledge.fallback_to_spanish": True,
        "workflow_v2.auto_policy_approval.enabled": True,
        "workflow_v2.auto_export.enabled": True,
        "localization.production_apply_enabled": True,
    }.items():
        assert run(_args(tmp_path, _profile(tmp_path, **{key: value}))) ["status"] == "NOT_READY"


def test_preflight_expected_ref_reports_audited_head(tmp_path, monkeypatch):
    _valid_environment(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    source = Path(__file__).resolve().parents[1]
    import subprocess
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    args = _args(tmp_path, _profile(tmp_path), head="ignored")
    args.expected_head = None
    args.expected_ref = "HEAD"
    report = run(args)
    assert report["status"] == "PASS"
    assert report["audited_ref"] == "HEAD"
    assert report["audited_content_head"] == head


def test_preflight_effective_config_hash_matches_runtime(tmp_path, monkeypatch):
    _valid_environment(tmp_path)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    profile = _profile(tmp_path)
    report = run(_args(tmp_path, profile))
    runtime_cfg = load_settings(overlay_paths=[profile])
    runtime_evidence = config_evidence(runtime_cfg)
    assert report["effective_config_hash"] == runtime_evidence["effective_config_hash"]
    assert report["base_config_sha256"] == runtime_evidence["base_config_sha256"]
    assert report["profile_sha256"] == runtime_evidence["profile_sha256"]
