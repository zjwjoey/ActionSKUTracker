"""Optional operator-only Kopia connection seam; never called by daily-run.

No repository creation, snapshot, restore, maintenance or deletion API. The
read-only connection probe uses a new private workspace, not the default Kopia
configuration. Raw CLI diagnostics are deliberately never returned.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import ssl
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .storage_foundation import StorageError

STAGES = ("ENVIRONMENT_READY", "NETWORK_OK", "AUTH_OK", "BUCKET_ACCESS_OK",
          "REPOSITORY_CONNECTED", "ROUNDTRIP_PASS")


@dataclass(frozen=True)
class ConnectionConfig:
    enabled: bool = False
    provider: str = "oss"
    bucket: str = ""
    endpoint: str = ""
    region: str = ""
    cli_path: str = "kopia"
    prefix: str = "actionskutracker-kopia-poc/"

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise StorageError("KOPIA_ENABLED_MUST_BE_BOOLEAN")
        if self.provider not in {"oss", "cos"}:
            raise StorageError("KOPIA_PROVIDER_UNSUPPORTED")
        if self.prefix != "actionskutracker-kopia-poc/":
            raise StorageError("KOPIA_POC_PREFIX_REQUIRED")
        for name in ("bucket", "endpoint", "region", "cli_path"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise StorageError("KOPIA_CONFIG_TYPE_INVALID")
        if self.bucket and not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
            raise StorageError("KOPIA_BUCKET_INVALID")
        if self.endpoint:
            endpoint_host(self.endpoint)

    @classmethod
    def from_settings(cls, settings: Mapping, env: Mapping | None = None):
        raw = (settings.get("storage_foundation") or {}).get("kopia") or {}
        enabled = raw.get("enabled", False)
        if type(enabled) is not bool:
            raise StorageError("KOPIA_ENABLED_MUST_BE_BOOLEAN")
        provider = raw.get("provider", "oss")
        if provider not in {"oss", "cos"}:
            raise StorageError("KOPIA_PROVIDER_UNSUPPORTED")
        env = os.environ if env is None else env
        base = f"ACTION_KOPIA_{provider.upper()}_"
        values = {k: env.get(base + k.upper(), raw.get(k, ""))
                  for k in ("bucket", "endpoint", "region")}
        cfg = cls(enabled=enabled, provider=provider, **values,
                  cli_path=raw.get("cli_path") or "kopia")
        if cfg.endpoint:
            endpoint_host(cfg.endpoint)
        if cfg.bucket and not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", cfg.bucket):
            raise StorageError("KOPIA_BUCKET_INVALID")
        return cfg


@dataclass(frozen=True)
class Credentials:
    access_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, provider: str, env: Mapping | None = None):
        if provider not in {"oss", "cos"}:
            raise StorageError("KOPIA_PROVIDER_UNSUPPORTED")
        env = os.environ if env is None else env
        base = f"ACTION_KOPIA_{provider.upper()}_"
        return cls(*(env.get(base + name, "") for name in
                     ("ACCESS_KEY", "SECRET_KEY", "PASSWORD", "SESSION_TOKEN")))

    def missing(self):
        return [name for name in ("access_key", "secret_key", "password")
                if not getattr(self, name)]


def endpoint_host(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
        port = parsed.port
    except ValueError:
        raise StorageError("KOPIA_ENDPOINT_INVALID") from None
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.path not in {"", "/"} or parsed.query or parsed.fragment or
            not parsed.hostname or port not in {None, 443}):
        raise StorageError("KOPIA_ENDPOINT_REQUIRES_HTTPS_HOST")
    host = parsed.hostname
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        raise StorageError("KOPIA_ENDPOINT_INVALID")
    return host


def cli_environment(cli_path: str = "kopia") -> dict:
    executable = shutil.which(cli_path)
    if not executable:
        return {"status": "BLOCKED", "reason": "KOPIA_NOT_INSTALLED"}
    try:
        base = [executable, "--disable-file-logging", "--disable-content-log"]
        version = subprocess.run(base + ["--version"], capture_output=True,
                                 text=True, timeout=15, stdin=subprocess.DEVNULL)
        help_result = subprocess.run(base + ["repository", "connect", "s3", "--help"],
                                     capture_output=True, text=True, timeout=15,
                                     stdin=subprocess.DEVNULL)
        match = re.search(r"\b\d+\.\d+\.\d+\b", version.stdout)
        if version.returncode or help_result.returncode or not match:
            return {"status": "BLOCKED", "reason": "KOPIA_CLI_UNUSABLE"}
        required = ("--endpoint", "--bucket", "--region", "--prefix", "--readonly")
        help_text = (help_result.stdout + help_result.stderr).replace("--[no-]", "--")
        supported = all(flag in help_text for flag in required)
        return {"status": "ENVIRONMENT_READY" if supported else "BLOCKED",
                "version": match.group(), "s3_supported": supported,
                "reason": None if supported else "KOPIA_S3_FLAGS_UNSUPPORTED"}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "BLOCKED", "reason": "KOPIA_CLI_EXECUTION_FAILED"}


def tls_probe(endpoint: str) -> dict:
    host = endpoint_host(endpoint)
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                return {"status": "NETWORK_OK", "tls_verified": True,
                        "tls_version": tls.version()}
    except ssl.SSLCertVerificationError:
        return {"status": "BLOCKED", "reason": "TLS_CERTIFICATE_INVALID"}
    except (OSError, ssl.SSLError):
        return {"status": "BLOCKED", "reason": "ENDPOINT_UNREACHABLE_OR_TLS_FAILED"}


def error_reason(diagnostics: str) -> str:
    """Return a closed category, never echo endpoints, keys or signed requests."""
    text = diagnostics.lower()
    for needles, category in (
        (("signaturedoesnotmatch", "invalidaccesskeyid", "invalidtoken", "expiredtoken"), "AUTH_REJECTED"),
        (("accessdenied", "forbidden"), "ACCESS_DENIED"),
        (("nosuchbucket",), "BUCKET_NOT_FOUND"),
        (("repository not found", "unable to find repository"), "REPOSITORY_NOT_FOUND"),
        (("password", "decrypt"), "REPOSITORY_PASSWORD_REJECTED"),
        (("x509", "certificate"), "TLS_CERTIFICATE_INVALID"),
        (("timeout", "deadline"), "CONNECTION_TIMEOUT"),
    ):
        if any(needle in text for needle in needles):
            return category
    return "KOPIA_CONNECTION_FAILED"


def _private_workspace(path: Path):
    path.mkdir(parents=False, exist_ok=False)
    if os.name == "nt":
        # New directory only. Remove inherited access before creating config/logs.
        identity = subprocess.run(["whoami"], capture_output=True, text=True,
                                  timeout=10, check=True).stdout.strip()
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"{identity}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       capture_output=True, timeout=10, check=True)
    else:
        path.chmod(0o700)


def connection_test(config: ConnectionConfig, *, workspace: Path | None = None,
                    env: Mapping | None = None, connect: bool = False) -> dict:
    result = {"provider": config.provider, "status": "BLOCKED",
              "stages": {stage: "NOT_TESTED" for stage in STAGES},
              "actual_cloud_write": False, "roundtrip_performed": False,
              "objects_created": [], "objects_deleted": []}
    if not config.enabled:
        return {**result, "reason": "DISABLED"}
    environment = cli_environment(config.cli_path)
    result["environment"] = environment
    result["stages"]["ENVIRONMENT_READY"] = environment["status"]
    if environment["status"] != "ENVIRONMENT_READY":
        return {**result, "reason": environment["reason"]}
    if config.endpoint:
        network = tls_probe(config.endpoint)
        result["network"] = network
        result["stages"]["NETWORK_OK"] = network["status"]
        if network["status"] != "NETWORK_OK":
            return {**result, "reason": network["reason"]}
    credentials = Credentials.from_environment(config.provider, env)
    missing = [key for key in ("bucket", "endpoint", "region") if not getattr(config, key)]
    missing += credentials.missing()
    if missing:
        return {**result, "reason": "CONFIGURATION_MISSING", "missing": missing}
    if not connect:
        return {**result, "reason": "READONLY_CONNECT_NOT_REQUESTED"}
    if workspace is None:
        return {**result, "reason": "ISOLATED_WORKSPACE_REQUIRED"}
    try:
        _private_workspace(Path(workspace))
        base = [config.cli_path, "--config-file", str(Path(workspace) / "repository.config"),
                "--no-persist-credentials", "--disable-repository-log",
                "--disable-file-logging", "--disable-content-log",
                "--log-dir", str(Path(workspace) / "logs")]
        child_env = {k: v for k, v in os.environ.items()
                     if not k.startswith(("AWS_", "KOPIA_", "ACTION_KOPIA_"))}
        child_env.update(AWS_ACCESS_KEY_ID=credentials.access_key,
                         AWS_SECRET_ACCESS_KEY=credentials.secret_key,
                         AWS_SESSION_TOKEN=credentials.session_token,
                         KOPIA_PASSWORD=credentials.password)
        args = base + ["repository", "connect", "s3", "--readonly", "--no-check-for-updates",
                       "--cache-directory", str(Path(workspace) / "cache"),
                       "--bucket", config.bucket, "--endpoint", endpoint_host(config.endpoint),
                       "--region", config.region, "--prefix", config.prefix]
        connected = subprocess.run(args, env=child_env, capture_output=True, text=True,
                                   timeout=90, stdin=subprocess.DEVNULL)
        if connected.returncode:
            return {**result, "reason": error_reason(connected.stderr + connected.stdout)}
        # Successful encrypted repository connection proves authenticated read
        # access, never write permission or end-to-end restore.
        for stage in ("AUTH_OK", "BUCKET_ACCESS_OK", "REPOSITORY_CONNECTED"):
            result["stages"][stage] = stage
        result["bucket_access_scope"] = "repository_read_only"
        disconnected = subprocess.run(base + ["repository", "disconnect"], env=child_env,
                                      capture_output=True, text=True, timeout=30,
                                      stdin=subprocess.DEVNULL)
        result["disconnected"] = disconnected.returncode == 0
        return {**result, "status": "REPOSITORY_CONNECTED",
                "reason": None if not disconnected.returncode else "DISCONNECT_FAILED"}
    except subprocess.TimeoutExpired:
        return {**result, "reason": "CONNECTION_TIMEOUT"}
    except (OSError, subprocess.CalledProcessError):
        return {**result, "reason": "ISOLATED_CLI_EXECUTION_FAILED"}
