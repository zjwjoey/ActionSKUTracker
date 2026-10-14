"""CI_SAFE: mocked CLI/TLS, synthetic credentials; no network or real assets."""
import json
import subprocess

import pytest

from action_tracker.operations import kopia_connectivity as k
from action_tracker.operations.storage_foundation import StorageError


def test_default_disabled_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(k, "cli_environment", lambda *_: pytest.fail("CLI started"))
    result = k.connection_test(k.ConnectionConfig.from_settings({}))
    assert result["reason"] == "DISABLED"
    assert set(result["stages"].values()) == {"NOT_TESTED"}


@pytest.mark.parametrize("value", ["false", 0, None])
def test_enabled_is_strict_boolean(value):
    with pytest.raises(StorageError):
        k.ConnectionConfig.from_settings({"storage_foundation": {"kopia": {"enabled": value}}})


@pytest.mark.parametrize("endpoint", ["http://host", "https://key:secret@host", "https://host/path", "https://host?token=secret", "https://host:444", "https://host:invalid"])
def test_insecure_or_credential_endpoint_rejected(endpoint):
    with pytest.raises(StorageError):
        k.ConnectionConfig(endpoint=endpoint)


def test_provider_and_prefix_guard():
    with pytest.raises(StorageError):
        k.ConnectionConfig(provider="unknown")
    with pytest.raises(StorageError):
        k.ConnectionConfig(prefix="production/")


def test_credentials_are_not_in_repr_or_missing_report(monkeypatch):
    env = {"ACTION_KOPIA_OSS_ACCESS_KEY": "synthetic-access",
           "ACTION_KOPIA_OSS_SECRET_KEY": "synthetic-secret"}
    credentials = k.Credentials.from_environment("oss", env)
    assert "synthetic" not in repr(credentials)
    monkeypatch.setattr(k, "cli_environment", lambda *_: {"status": "ENVIRONMENT_READY"})
    result = k.connection_test(k.ConnectionConfig(enabled=True), env=env)
    assert result["reason"] == "CONFIGURATION_MISSING"
    assert "password" in result["missing"]
    assert "synthetic" not in json.dumps(result)
    assert result["stages"]["AUTH_OK"] == "NOT_TESTED"


def test_cli_missing_and_timeout(monkeypatch):
    monkeypatch.setattr(k.shutil, "which", lambda _: None)
    assert k.cli_environment()["reason"] == "KOPIA_NOT_INSTALLED"
    monkeypatch.setattr(k.shutil, "which", lambda _: "kopia")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("kopia", 15)
    monkeypatch.setattr(k.subprocess, "run", timeout)
    assert k.cli_environment()["reason"] == "KOPIA_CLI_EXECUTION_FAILED"


def test_cli_boolean_help_flag_syntax(monkeypatch):
    monkeypatch.setattr(k.shutil, "which", lambda _: "kopia")
    def run(args, **kwargs):
        text = "0.23.1" if "--version" in args else "--endpoint --bucket --region --prefix --[no-]readonly"
        return subprocess.CompletedProcess(args, 0, text, "")
    monkeypatch.setattr(k.subprocess, "run", run)
    assert k.cli_environment()["status"] == "ENVIRONMENT_READY"


def test_tls_fail_closed(monkeypatch):
    def bad(*args, **kwargs):
        raise k.ssl.SSLCertVerificationError("synthetic-secret")
    monkeypatch.setattr(k.socket, "create_connection", bad)
    assert k.tls_probe("example.com")["reason"] == "TLS_CERTIFICATE_INVALID"


def test_error_categories_never_echo_credentials():
    assert k.error_reason("SignatureDoesNotMatch synthetic-secret") == "AUTH_REJECTED"
    assert k.error_reason("arbitrary signed URL synthetic-secret") == "KOPIA_CONNECTION_FAILED"


def test_readonly_connection_isolated_secrets_not_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "cli_environment", lambda *_: {"status": "ENVIRONMENT_READY"})
    monkeypatch.setattr(k, "tls_probe", lambda *_: {"status": "NETWORK_OK"})
    monkeypatch.setattr(k, "_private_workspace", lambda p: p.mkdir())
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(k.subprocess, "run", run)
    env = {f"ACTION_KOPIA_COS_{name}": "synthetic-secret" for name in
           ("ACCESS_KEY", "SECRET_KEY", "PASSWORD", "SESSION_TOKEN")}
    cfg = k.ConnectionConfig(enabled=True, provider="cos", bucket="test-123",
                             endpoint="cos.ap-guangzhou.myqcloud.com", region="ap-guangzhou")
    result = k.connection_test(cfg, workspace=tmp_path / "private", env=env, connect=True)
    assert result["status"] == "REPOSITORY_CONNECTED"
    assert result["stages"]["ROUNDTRIP_PASS"] == "NOT_TESTED"
    assert result["actual_cloud_write"] is False
    assert "--readonly" in calls[0][0]
    assert "--no-persist-credentials" in calls[0][0]
    assert "--disable-repository-log" in calls[0][0]
    assert "--disable-file-logging" in calls[0][0]
    assert "synthetic-secret" not in str([c[0] for c in calls])
    assert calls[0][1]["env"]["AWS_SESSION_TOKEN"] == "synthetic-secret"
    assert calls[1][0][-2:] == ["repository", "disconnect"]
    assert "synthetic-secret" not in json.dumps(result)


def test_failed_cloud_connection_cannot_claim_auth_or_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "cli_environment", lambda *_: {"status": "ENVIRONMENT_READY"})
    monkeypatch.setattr(k, "tls_probe", lambda *_: {"status": "NETWORK_OK"})
    monkeypatch.setattr(k, "_private_workspace", lambda p: p.mkdir())
    monkeypatch.setattr(k.subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 1, "", "AccessDenied synthetic-secret"))
    env = {f"ACTION_KOPIA_OSS_{name}": "synthetic-secret" for name in
           ("ACCESS_KEY", "SECRET_KEY", "PASSWORD")}
    cfg = k.ConnectionConfig(enabled=True, bucket="test-bucket", endpoint="example.com", region="test-region")
    result = k.connection_test(cfg, workspace=tmp_path / "private", env=env, connect=True)
    assert result["reason"] == "ACCESS_DENIED"
    assert result["stages"]["AUTH_OK"] == "NOT_TESTED"
    assert result["stages"]["BUCKET_ACCESS_OK"] == "NOT_TESTED"
    assert "synthetic-secret" not in json.dumps(result)
