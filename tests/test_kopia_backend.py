"""CI_SAFE: synthetic paths/credentials and mocked CLI; never cloud/real data."""
import json
import os
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from action_tracker.operations import kopia_backend as k
from action_tracker.operations import kopia_connectivity as poc


RID = "a" * 64
SID = "b" * 32
OBJ = "k" + "c" * 32


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "_private_workspace", lambda path: path.mkdir())
    result = k.KopiaBackend(k.BackendConfig(enabled=True, repository_path=str(tmp_path / "repo"),
            expected_repository_id=RID), k.BackendCredentials(password="synthetic-password"), tmp_path / "private")
    result._connected, result._readonly = True, False
    return result


def status():
    return {"uniqueIDHex": RID, "clientOptions": {"enableActions": False},
            "storage": {"secret": "synthetic-do-not-report"}}


def snapshot(source=None, inventory=None):
    inventory = inventory or {"logical_bytes": 3, "file_count": 1, "source_sha256": "d" * 64}
    return {"id": SID, "source": {"path": str(source)},
            "rootEntry": {"obj": OBJ, "summ": {"size": inventory["logical_bytes"], "files": inventory["file_count"]}},
            "tags": {"tag:backup-set": "set-1", "tag:source-sha256": inventory["source_sha256"]},
            "pins": ["action-backup-retention"], "stats": {"errorCount": 0}}


def mock_cli(monkeypatch, handler):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = handler(args)
        if isinstance(output, subprocess.CompletedProcess):
            return output
        return subprocess.CompletedProcess(args, 0, json.dumps(output), "")
    monkeypatch.setattr(k.subprocess, "run", run)
    return calls


def test_disabled_backend_never_reads_or_creates(tmp_path, monkeypatch):
    b = k.KopiaBackend(k.BackendConfig(), k.BackendCredentials(), tmp_path / "private")
    monkeypatch.setattr(k, "cli_environment", lambda *_: pytest.fail("CLI executed"))
    assert b.probe() == {"status": "DISABLED", "side_effects": False}
    with pytest.raises(k.BackendError, match="^DISABLED$"):
        b.create_repository(creation_authorized=True)
    with pytest.raises(k.BackendError, match="^DISABLED$"):
        b.create_snapshot(tmp_path / "missing", backup_set_id="x", upload_limit_bytes=1)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kwargs", [{"enabled": "false"}, {"max_parallel": 0}, {"max_parallel": 5},
    {"read_retries": 3}, {"timeout_seconds": True}, {"provider": "unknown"},
    {"prefix": "../existing/"}, {"prefix": "/existing/"}, {"endpoint": "http://host"},
    {"endpoint": "https://key:secret@host"}, {"bucket": "Wrong_Bucket"}, {"expected_repository_id": "x"}])
def test_configuration_fails_closed(kwargs):
    with pytest.raises(k.StorageError):
        k.BackendConfig(**kwargs)


def test_local_credentials_require_no_cloud_keys():
    credentials = k.BackendCredentials.from_environment("filesystem", {"ACTION_KOPIA_LOCAL_PASSWORD": "secret"})
    assert credentials.password == "secret"
    assert credentials.access_key == ""
    assert "secret" not in repr(credentials)


def test_credentials_and_unrelated_env_never_enter_args_or_reports(backend, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unrelated")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-ambient-cloud")
    monkeypatch.setenv("KOPIA_PASSWORD", "synthetic-ambient-password")
    calls = mock_cli(monkeypatch, lambda _: status())
    result = backend.repository_status()
    args, kwargs = calls[0]
    assert not any("synthetic" in item for item in args)
    assert kwargs["env"]["KOPIA_PASSWORD"] == "synthetic-password"
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
    assert "synthetic" not in json.dumps(result)
    for flag in ("--no-persist-credentials", "--no-auto-maintenance", "--no-use-credential-manager",
                 "--disable-repository-log", "--disable-file-logging", "--disable-content-log"):
        assert flag in args


def test_probe_also_has_allowlisted_environment(monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "synthetic")
    monkeypatch.setattr(poc.shutil, "which", lambda _: "kopia")
    calls = []
    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "0.23.1" if "--version" in args else
            "--endpoint --bucket --region --prefix --[no-]readonly", "")
    monkeypatch.setattr(poc.subprocess, "run", run)
    assert poc.cli_environment()["status"] == "ENVIRONMENT_READY"
    assert all("UNRELATED_SECRET" not in c["env"] for c in calls)


def test_windows_acl_helpers_do_not_inherit_application_secrets(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows ACL helpers")
    monkeypatch.setenv("UNRELATED_SECRET", "synthetic")
    calls = []
    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "test\\user" if args == ["whoami"] else "", "")
    monkeypatch.setattr(poc.subprocess, "run", run)
    poc._private_workspace(tmp_path / "acl")
    assert len(calls) == 2
    assert all("UNRELATED_SECRET" not in c["env"] for c in calls)


def test_identity_mismatch_cannot_continue(backend, monkeypatch):
    raw = status(); raw["uniqueIDHex"] = "e" * 64
    mock_cli(monkeypatch, lambda _: raw)
    with pytest.raises(k.BackendError, match="REPOSITORY_ID_MISMATCH"):
        backend.repository_status()
    assert not backend._connected


def test_existing_repository_requires_pinned_identity(backend):
    backend._connected = False; backend._repository_id = ""
    with pytest.raises(k.BackendError, match="EXPECTED_REPOSITORY_ID_REQUIRED"):
        backend.connect()


def test_repository_actions_are_rejected(backend, monkeypatch):
    raw = status(); raw["clientOptions"]["enableActions"] = True
    mock_cli(monkeypatch, lambda _: raw)
    with pytest.raises(k.BackendError, match="ACTIONS_MUST_BE_DISABLED"):
        backend.repository_status()


def test_existing_repository_directory_is_never_reinitialized(backend, tmp_path):
    backend._connected = False
    backend.config = replace(backend.config, expected_repository_id="")
    (tmp_path / "repo").mkdir()
    with pytest.raises(k.BackendError, match="NEW_REPOSITORY_PATH_REQUIRED"):
        backend.create_repository(creation_authorized=True)
    assert list((tmp_path / "repo").iterdir()) == []


def test_repository_create_needs_explicit_authorization(backend):
    with pytest.raises(k.BackendError, match="NOT_AUTHORIZED"):
        backend.create_repository()


def test_cloud_creation_scope_and_prior_poc_protected(backend):
    backend._connected = False
    backend.config = replace(backend.config, provider="oss", expected_repository_id="",
        bucket="test-bucket", endpoint="https://s3.oss-cn-hongkong.aliyuncs.com", region="cn-hongkong",
        prefix="actionskutracker-kopia-poc/existing/")
    with pytest.raises(k.BackendError, match="PRIOR_POC_PREFIX_PROTECTED"):
        backend.create_repository(creation_authorized=True)
    backend.config = replace(backend.config, prefix="new-scoped-prefix/")
    with pytest.raises(k.BackendError, match="SCOPED_CREATION_GRANT_REQUIRED"):
        backend.create_repository(creation_authorized=True)
    grant = {key: getattr(backend.config, key) for key in ("provider", "bucket", "endpoint", "region", "prefix")}
    now = datetime.now(timezone.utc)
    grant.update(approved_by="operator", approval_id="explicit-test", expires_at=(now + timedelta(hours=1)).isoformat(),
        empty_prefix_observed_at=now.isoformat(), observed_object_count=0, empty_prefix_evidence_id="inventory1")
    backend._cloud_creation_grant(grant)
    for bad in ({"prefix": "other/"}, {"observed_object_count": 1}, {"expires_at": now.isoformat()},
                {"empty_prefix_observed_at": (now - timedelta(minutes=6)).isoformat()}):
        with pytest.raises(k.BackendError, match="SCOPED_CREATION_GRANT_REQUIRED"):
            backend._cloud_creation_grant({**grant, **bad})


def test_write_timeout_is_not_retried_and_hides_diagnostics(backend, monkeypatch):
    calls = []
    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 1, output="synthetic-secret")
    monkeypatch.setattr(k.subprocess, "run", fail)
    with pytest.raises(k.BackendError, match="^KOPIA_WRITE_OUTCOME_UNKNOWN$") as exc:
        backend._run(["snapshot", "create", "synthetic-source"])
    assert len(calls) == 1
    assert "synthetic-secret" not in str(exc.value)


def test_read_retry_is_bounded(backend, monkeypatch):
    calls = mock_cli(monkeypatch, lambda args: subprocess.CompletedProcess(args, 1, "", "deadline synthetic-secret"))
    with pytest.raises(k.BackendError, match="^CONNECTION_TIMEOUT$"):
        backend._run(["snapshot", "list"], readonly=True)
    assert len(calls) == 2


@pytest.mark.parametrize("diagnostic,code", [("upload limit reached synthetic-secret", "KOPIA_UPLOAD_LIMIT_EXCEEDED"),
    ("not enough space synthetic-secret", "KOPIA_STORAGE_SPACE_EXHAUSTED")])
def test_budget_and_disk_failures_are_closed_categories(backend, monkeypatch, diagnostic, code):
    calls = mock_cli(monkeypatch, lambda args: subprocess.CompletedProcess(args, 1, "", diagnostic))
    with pytest.raises(k.BackendError, match="^" + code + "$"):
        backend._run(["snapshot", "create", "synthetic-source"])
    assert len(calls) == 1


def test_job_deadline_reduces_subprocess_timeout(backend, monkeypatch):
    calls = mock_cli(monkeypatch, lambda _: status())
    backend.set_deadline(time.monotonic() + 0.5)
    backend.repository_status()
    assert 0 < calls[0][1]["timeout"] <= 0.5
    backend.set_deadline(time.monotonic() - 1)
    with pytest.raises(k.BackendError, match="^CONNECTION_TIMEOUT$"):
        backend.repository_status()
    assert len(calls) == 1


def test_resume_same_tag_and_source_reuses_without_write(backend, tmp_path, monkeypatch):
    source = tmp_path / "stage"; source.mkdir(); (source / "f").write_bytes(b"abc")
    raw = snapshot(source, k._source_inventory(source, content_hash=True))
    calls = mock_cli(monkeypatch, lambda args: status() if "status" in args else [raw])
    result = backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=1024**2, resume_only=True)
    assert result["reused"] and result["snapshot_id"] == SID
    assert all("create" not in args for args, _ in calls)
    (source / "f").write_bytes(b"xyz")  # Same size must still be a conflict.
    with pytest.raises(k.BackendError, match="SOURCE_CONFLICT"):
        backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=1024**2)


def test_unknown_outcome_with_no_manifest_never_writes(backend, tmp_path, monkeypatch):
    source = tmp_path / "stage"; source.mkdir()
    calls = mock_cli(monkeypatch, lambda args: status() if "status" in args else [])
    with pytest.raises(k.BackendError, match="SNAPSHOT_OUTCOME_UNRESOLVED"):
        backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=1024**2, resume_only=True)
    assert all("create" not in args for args, _ in calls)


def test_duplicate_tag_is_conflict(backend, tmp_path, monkeypatch):
    source = tmp_path / "stage"; source.mkdir()
    raw = snapshot(source, k._source_inventory(source, content_hash=True))
    mock_cli(monkeypatch, lambda args: status() if "status" in args else [raw, raw])
    with pytest.raises(k.BackendError, match="SNAPSHOT_CONFLICT"):
        backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=1024**2)


def test_create_pins_and_hashes_source_and_suppresses_notifications(backend, tmp_path, monkeypatch):
    source = tmp_path / "stage"; source.mkdir(); (source / "f").write_bytes(b"abc")
    raw = snapshot(source, k._source_inventory(source, content_hash=True))
    calls = mock_cli(monkeypatch, lambda args: status() if "status" in args else raw if "create" in args else [])
    result = backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=2 * 1024**2)
    args = [args for args, _ in calls if "create" in args][0]
    assert "--pin" in args and "action-backup-retention" in args
    assert "--force-hash=100" in args and "--no-send-snapshot-report" in args
    assert "--upload-limit-mb=2" in args
    assert result["metrics"]["uploaded_bytes"]["status"] == "UNKNOWN"


def test_readonly_connection_cannot_write(backend, tmp_path, monkeypatch):
    source = tmp_path / "stage"; source.mkdir()
    backend._readonly = True
    mock_cli(monkeypatch, lambda args: status() if "status" in args else [])
    with pytest.raises(k.BackendError, match="READONLY_CONNECTION"):
        backend.create_snapshot(source, backup_set_id="set-1", upload_limit_bytes=1024**2)


def test_verification_reads_100_percent_files(backend, monkeypatch):
    raw = snapshot()
    calls = mock_cli(monkeypatch, lambda args: status() if "status" in args else
        {"errorCount": 0, "stats": {"readFileCount": 1, "expectedTotalFileCount": 1, "readBytes": 3}} if "verify" in args else [raw])
    assert backend.verify_snapshot(SID)["status"] == "SNAPSHOT_VERIFIED"
    args = [args for args, _ in calls if "verify" in args][0]
    assert "--verify-files-percent=100" in args


def test_structural_only_verification_is_rejected(backend, monkeypatch):
    mock_cli(monkeypatch, lambda args: status() if "status" in args else
        {"errorCount": 0, "stats": {"readFileCount": 0}} if "verify" in args else [snapshot()])
    with pytest.raises(k.BackendError, match="VERIFICATION_INCOMPLETE"):
        backend.verify_snapshot(SID)


def test_deduplicated_identical_files_need_one_object_read(backend, monkeypatch):
    raw = snapshot(inventory={"logical_bytes": 6, "file_count": 2, "source_sha256": "d" * 64})
    mock_cli(monkeypatch, lambda args: status() if "status" in args else
        {"errorCount": 0, "stats": {"readFileCount": 1, "expectedTotalFileCount": 2, "readBytes": 3}}
        if "verify" in args else [raw])
    result = backend.verify_snapshot(SID)
    assert result["status"] == "SNAPSHOT_VERIFIED"
    assert result["file_count"] == 2 and result["unique_file_objects_read"] == 1


def test_restore_never_overwrites_existing_target(backend, tmp_path, monkeypatch):
    target = tmp_path / "restore"; target.mkdir(); (target / "keep").write_bytes(b"known")
    mock_cli(monkeypatch, lambda args: status() if "status" in args else [snapshot()])
    with pytest.raises(k.BackendError, match="NEW_RESTORE_TARGET_REQUIRED"):
        backend.restore_snapshot(SID, target)
    assert (target / "keep").read_bytes() == b"known"


def test_missing_snapshot_is_asset_unavailable(backend, tmp_path, monkeypatch):
    mock_cli(monkeypatch, lambda args: status() if "status" in args else [])
    with pytest.raises(k.BackendError, match="^ASSET_UNAVAILABLE$"):
        backend.restore_snapshot(SID, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_restore_disables_all_overwrites_and_extra_deletion(backend, tmp_path, monkeypatch):
    target = tmp_path / "restore"
    def handler(args):
        if "status" in args: return status()
        if "restore" in args:
            (target / "f").write_bytes(b"abc")
            return None
        return [snapshot()]
    calls = mock_cli(monkeypatch, handler)
    result = backend.restore_snapshot(SID, target)
    args = [args for args, _ in calls if "restore" in args][0]
    assert all(flag in args for flag in ("--no-overwrite-files", "--no-overwrite-directories",
        "--no-overwrite-symlinks", "--no-delete-extra", "--no-ignore-errors"))
    assert not result["business_restore_verified"]


def test_usage_distinguishes_observed_object_bytes_from_traffic(backend, monkeypatch):
    mock_cli(monkeypatch, lambda args: status() if "status" in args else
        [{"length": 20}, {"length": 30}] if "blob" in args else [snapshot()])
    result = backend.storage_usage()
    assert result["repository_bytes"] == {"status": "OBSERVED", "value": 50}
    assert result["logical_bytes"] == 3
    assert result["billing_bytes"]["status"] == "UNKNOWN"


def test_symlink_and_hardlink_staging_rejected(tmp_path):
    original = tmp_path / "f"; original.write_bytes(b"x")
    linked = tmp_path / "hardlink"
    os.link(original, linked)
    with pytest.raises(k.BackendError, match="HARDLINK_FORBIDDEN"):
        k._source_inventory(tmp_path)


def test_ignore_files_cannot_silently_drop_staged_assets(tmp_path):
    (tmp_path / ".kopiaignore").write_text("*", encoding="utf8")
    with pytest.raises(k.BackendError, match="STAGED_IGNORE_FILE_FORBIDDEN"):
        k._source_inventory(tmp_path)
