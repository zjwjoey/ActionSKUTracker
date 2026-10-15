"""Controlled, replaceable Kopia CLI backend; no production or deletion hooks.

This layer stores only explicit staged sources. P1 consistency, business scopes,
approval records and the durable job journal belong to the asset/operations layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

from .kopia_connectivity import (_private_workspace, cli_environment, endpoint_host,
                                 error_reason, subprocess_environment)
from .storage_foundation import StorageError


class BackendError(StorageError):
    """Public errors contain one closed code, never CLI output or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BackendConfig:
    enabled: bool = False
    provider: str = "filesystem"
    repository_path: str = ""
    bucket: str = ""
    endpoint: str = ""
    region: str = ""
    prefix: str = ""
    cli_path: str = "kopia"
    expected_repository_id: str = ""
    purpose: str = "business"
    timeout_seconds: int = 300
    read_retries: int = 1
    max_parallel: int = 1

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise BackendError("KOPIA_ENABLED_MUST_BE_BOOLEAN")
        if self.provider not in {"filesystem", "local", "oss", "cos", "s3"}:
            raise BackendError("KOPIA_PROVIDER_UNSUPPORTED")
        if self.purpose not in {"business", "synthetic_test"}:
            raise BackendError("KOPIA_REPOSITORY_PURPOSE_INVALID")
        for name in ("repository_path", "bucket", "endpoint", "region", "prefix",
                     "cli_path", "expected_repository_id"):
            if not isinstance(getattr(self, name), str) or any(c in getattr(self, name) for c in "\r\n\x00"):
                raise BackendError("KOPIA_CONFIG_TYPE_INVALID")
        for name, low, high in (("timeout_seconds", 1, 86400), ("read_retries", 0, 2), ("max_parallel", 1, 4)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise BackendError("KOPIA_RUNTIME_LIMIT_INVALID")
        if self.endpoint:
            endpoint_host(self.endpoint)
        if self.bucket and not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
            raise BackendError("KOPIA_BUCKET_INVALID")
        if self.region and not re.fullmatch(r"[a-z0-9-]{1,64}", self.region):
            raise BackendError("KOPIA_REGION_INVALID")
        if self.prefix and (not re.fullmatch(r"[A-Za-z0-9_/-]{1,240}/", self.prefix)
                            or "//" in self.prefix or self.prefix.startswith("/")):
            raise BackendError("KOPIA_PREFIX_INVALID")
        if self.expected_repository_id and not re.fullmatch(r"[0-9a-f]{64}", self.expected_repository_id):
            raise BackendError("KOPIA_REPOSITORY_ID_INVALID")

    def public_fingerprint(self) -> str:
        public = {key: getattr(self, key) for key in
                  ("provider", "repository_path", "bucket", "endpoint", "region", "prefix", "purpose")}
        return hashlib.sha256(json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class BackendCredentials:
    password: str = field(default="", repr=False)
    access_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)

    def __post_init__(self):
        if any(not isinstance(value, str) or "\x00" in value for value in
               (self.password, self.access_key, self.secret_key, self.session_token)):
            raise BackendError("KOPIA_CREDENTIAL_TYPE_INVALID")

    @classmethod
    def from_environment(cls, provider: str, env: Mapping | None = None):
        if provider not in {"filesystem", "local", "oss", "cos", "s3"}:
            raise BackendError("KOPIA_PROVIDER_UNSUPPORTED")
        env = os.environ if env is None else env
        base = "ACTION_KOPIA_" + ("LOCAL" if provider in {"filesystem", "local"} else provider.upper()) + "_"
        # Local repositories do not require cloud credentials.
        return cls(*(env.get(base + key, "") for key in
                     ("PASSWORD", "ACCESS_KEY", "SECRET_KEY", "SESSION_TOKEN")))


class BackupBackend(Protocol):
    config: BackendConfig

    def probe(self) -> dict: ...
    def connect(self, *, readonly: bool = False) -> dict: ...
    def create_repository(self, *, creation_authorized: bool = False, creation_grant: dict | None = None) -> dict: ...
    def create_snapshot(self, source: Path, *, backup_set_id: str, upload_limit_bytes: int, resume_only: bool = False) -> dict: ...
    def list_snapshots(self, *, backup_set_id: str | None = None) -> list[dict]: ...
    def verify_snapshot(self, snapshot_id: str) -> dict: ...
    def restore_snapshot(self, snapshot_id: str, target: Path) -> dict: ...
    def repository_status(self) -> dict: ...
    def storage_usage(self) -> dict: ...
    def disconnect(self) -> dict: ...
    def set_deadline(self, monotonic_deadline: float | None): ...


def _safe_path(value: Path, *, must_exist: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts[1:]):
        raise BackendError("KOPIA_ABSOLUTE_SAFE_PATH_REQUIRED")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise BackendError("KOPIA_LINK_PATH_FORBIDDEN")
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise BackendError("KOPIA_LINK_PATH_FORBIDDEN")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise BackendError("KOPIA_HARDLINK_FORBIDDEN")
    path = path.resolve()
    if must_exist and not path.exists():
        raise BackendError("ASSET_UNAVAILABLE")
    return path


def _source_inventory(path: Path, *, content_hash: bool = False, checkpoint=None) -> dict:
    if not path.is_dir():
        raise BackendError("KOPIA_STAGED_DIRECTORY_REQUIRED")
    size, count, identities = 0, 0, []
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            if checkpoint:
                checkpoint()
            item = _safe_path(Path(root) / name, must_exist=True)
            if item.is_file():
                if name.lower() == ".kopiaignore":
                    raise BackendError("KOPIA_STAGED_IGNORE_FILE_FORBIDDEN")
                size += item.stat().st_size
                count += 1
                if content_hash:
                    before = item.stat()
                    digest = hashlib.sha256()
                    with item.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            if checkpoint:
                                checkpoint()
                            digest.update(block)
                    after = item.stat()
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise BackendError("KOPIA_SOURCE_CHANGED")
                    identities.append([item.relative_to(path).as_posix(), after.st_size, digest.hexdigest()])
            elif not item.is_dir():
                raise BackendError("KOPIA_SPECIAL_FILE_FORBIDDEN")
    result = {"logical_bytes": size, "file_count": count}
    if content_hash:
        result["source_sha256"] = hashlib.sha256(json.dumps(sorted(identities), ensure_ascii=True,
            separators=(",", ":")).encode()).hexdigest()
    return result


def _unknown() -> dict:
    return {"status": "UNKNOWN", "value": None}


class KopiaBackend:
    def __init__(self, config: BackendConfig, credentials: BackendCredentials, workspace: Path):
        self.config, self.credentials = config, credentials
        self.workspace = Path(workspace)
        self._ready = False
        self._connected = False
        self._readonly = True
        self._repository_id = config.expected_repository_id
        self._deadline: float | None = None

    def set_deadline(self, monotonic_deadline: float | None):
        if monotonic_deadline is not None and (type(monotonic_deadline) not in {int, float}
                                               or monotonic_deadline <= 0):
            raise BackendError("KOPIA_DEADLINE_INVALID")
        self._deadline = monotonic_deadline

    def _check_deadline(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise BackendError("KOPIA_TIME_BUDGET_EXCEEDED")

    @property
    def _local(self):
        return self.config.provider in {"filesystem", "local"}

    def _enabled(self):
        if not self.config.enabled:
            raise BackendError("DISABLED")

    def probe(self) -> dict:
        if not self.config.enabled:
            return {"status": "DISABLED", "side_effects": False}
        return cli_environment(self.config.cli_path)

    def _prepare(self):
        self._enabled()
        if self._ready:
            return
        if not self.credentials.password or (not self._local and
                (not self.credentials.access_key or not self.credentials.secret_key)):
            raise BackendError("KOPIA_CREDENTIALS_MISSING")
        if not self._local and not all((self.config.bucket, self.config.endpoint, self.config.region, self.config.prefix)):
            raise BackendError("KOPIA_CONFIGURATION_MISSING")
        if self._local and not self.config.repository_path:
            raise BackendError("KOPIA_REPOSITORY_PATH_REQUIRED")
        self.workspace = _safe_path(self.workspace)
        if self.workspace.exists() or not self.workspace.parent.is_dir():
            raise BackendError("KOPIA_NEW_PRIVATE_WORKSPACE_REQUIRED")
        if self._local:
            repo = _safe_path(Path(self.config.repository_path))
            if repo.is_relative_to(self.workspace) or self.workspace.is_relative_to(repo):
                raise BackendError("KOPIA_WORKSPACE_REPOSITORY_OVERLAP")
        try:
            _private_workspace(self.workspace)
        except (OSError, subprocess.SubprocessError):
            raise BackendError("KOPIA_PRIVATE_WORKSPACE_FAILED") from None
        self._ready = True

    def _environment(self):
        env = subprocess_environment()
        env["KOPIA_PASSWORD"] = self.credentials.password
        if not self._local:
            env.update(AWS_ACCESS_KEY_ID=self.credentials.access_key,
                       AWS_SECRET_ACCESS_KEY=self.credentials.secret_key,
                       AWS_SESSION_TOKEN=self.credentials.session_token)
        return env

    def _base(self):
        return [self.config.cli_path, "--config-file", str(self.workspace / "repository.config"),
                "--no-persist-credentials", "--no-use-credential-manager", "--no-auto-maintenance",
                "--disable-repository-log", "--disable-file-logging", "--disable-content-log",
                "--no-progress", "--log-dir", str(self.workspace / "logs")]

    def _run(self, args: list[str], *, readonly: bool = False, json_result: bool = False):
        self._prepare()
        deadline = time.monotonic() + self.config.timeout_seconds
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        attempts = 1 + (self.config.read_retries if readonly else 0)
        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackendError("CONNECTION_TIMEOUT" if readonly else "KOPIA_WRITE_OUTCOME_UNKNOWN")
            try:
                process = subprocess.run(self._base() + args, env=self._environment(),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    stdin=subprocess.DEVNULL, timeout=remaining)
            except subprocess.TimeoutExpired:
                raise BackendError("CONNECTION_TIMEOUT" if readonly else "KOPIA_WRITE_OUTCOME_UNKNOWN") from None
            except OSError:
                raise BackendError("KOPIA_CLI_EXECUTION_FAILED") from None
            if process.returncode:
                diagnostics = process.stdout + process.stderr
                lowered = diagnostics.lower()
                if "upload limit" in lowered:
                    reason = "KOPIA_UPLOAD_LIMIT_EXCEEDED"
                elif any(value in lowered for value in ("no space left", "disk full", "not enough space")):
                    reason = "KOPIA_STORAGE_SPACE_EXHAUSTED"
                else:
                    reason = error_reason(diagnostics)
                if readonly and reason == "CONNECTION_TIMEOUT" and attempt + 1 < attempts:
                    continue
                raise BackendError(reason)
            if not json_result:
                return None
            try:
                return json.loads(process.stdout)
            except (ValueError, TypeError):
                raise BackendError("KOPIA_RESPONSE_INVALID" if readonly else "KOPIA_WRITE_OUTCOME_UNKNOWN") from None
        raise BackendError("KOPIA_CLI_EXECUTION_FAILED")

    def _connection_args(self, action: str, readonly: bool):
        args = ["repository", action, "filesystem" if self._local else "s3",
                "--no-check-for-updates", "--no-enable-actions",
                "--cache-directory", str(self.workspace / "cache")]
        if self._local:
            args += ["--path", str(_safe_path(Path(self.config.repository_path)))]
        else:
            args += ["--bucket", self.config.bucket, "--endpoint", endpoint_host(self.config.endpoint),
                     "--region", self.config.region, "--prefix", self.config.prefix]
        if readonly:
            args.append("--readonly")
        return args

    def _write_gate(self):
        if self._readonly:
            raise BackendError("KOPIA_READONLY_CONNECTION")
        if self._protected_poc_prefix():
            raise BackendError("KOPIA_PRIOR_POC_PREFIX_PROTECTED")

    def _protected_poc_prefix(self):
        if self._local or not self.config.prefix.startswith("actionskutracker-kopia-poc/"):
            return False
        # Only a separate, explicitly scoped V3 synthetic repository may share
        # the IAM parent prefix. The original PoC root and oss-* repos stay blocked.
        return not (self.config.purpose == "synthetic_test" and re.fullmatch(
            r"actionskutracker-kopia-poc/storage-v3-[a-z0-9-]{12,100}/", self.config.prefix))

    def _cloud_creation_grant(self, grant: dict | None):
        grant = grant or {}
        try:
            expires = datetime.fromisoformat(grant.get("expires_at", ""))
            observed = datetime.fromisoformat(grant.get("empty_prefix_observed_at", ""))
            current = datetime.now(timezone.utc)
            fresh = observed.tzinfo is not None and 0 <= (current - observed).total_seconds() <= 300
        except (ValueError, TypeError):
            raise BackendError("KOPIA_SCOPED_CREATION_GRANT_REQUIRED") from None
        if (not grant.get("approved_by") or not grant.get("approval_id") or expires.tzinfo is None
                or expires <= current or not fresh or grant.get("observed_object_count") != 0
                or type(grant.get("observed_object_count")) is not int
                or not grant.get("empty_prefix_evidence_id")
                or any(grant.get(k) != getattr(self.config, k) for k in
                       ("provider", "bucket", "endpoint", "region", "prefix"))):
            raise BackendError("KOPIA_SCOPED_CREATION_GRANT_REQUIRED")

    def create_repository(self, *, creation_authorized: bool = False, creation_grant: dict | None = None) -> dict:
        self._enabled()
        if creation_authorized is not True:
            raise BackendError("KOPIA_REPOSITORY_CREATION_NOT_AUTHORIZED")
        if self._connected or self.config.expected_repository_id:
            raise BackendError("KOPIA_REPOSITORY_REINITIALIZATION_FORBIDDEN")
        if not self._local:
            if self._protected_poc_prefix():
                raise BackendError("KOPIA_PRIOR_POC_PREFIX_PROTECTED")
            self._cloud_creation_grant(creation_grant)
        self._prepare()
        if self._local:
            repo = _safe_path(Path(self.config.repository_path))
            if repo.exists() or not repo.parent.is_dir():
                raise BackendError("KOPIA_NEW_REPOSITORY_PATH_REQUIRED")
            # Exclusive ownership prevents a caller from reinitializing an
            # unknown empty directory. Failure leaves evidence; never cleanup.
            repo.mkdir(exist_ok=False)
        self._run(self._connection_args("create", False))
        self._connected, self._readonly = True, False
        result = self._read_repository_status(bind_created=True)
        return {**result, "created": True,
                "empty_prefix_evidence": "NOT_APPLICABLE" if self._local else "CALLER_SCOPED_OBSERVATION"}

    def connect(self, *, readonly: bool = False) -> dict:
        self._enabled()
        if type(readonly) is not bool:
            raise BackendError("KOPIA_READONLY_BOOLEAN_REQUIRED")
        if not self._repository_id:
            raise BackendError("KOPIA_EXPECTED_REPOSITORY_ID_REQUIRED")
        if self._connected:
            if readonly != self._readonly:
                raise BackendError("KOPIA_RECONNECT_REQUIRED_FOR_ACCESS_CHANGE")
            return self.repository_status()
        if self._local and not _safe_path(Path(self.config.repository_path), must_exist=True).is_dir():
            raise BackendError("KOPIA_REPOSITORY_UNAVAILABLE")
        self._run(self._connection_args("connect", readonly), readonly=True)
        self._connected, self._readonly = True, readonly
        return self._read_repository_status()

    def _read_repository_status(self, *, bind_created: bool = False):
        raw = self._run(["repository", "status", "--json"], readonly=True, json_result=True)
        repository_id = raw.get("uniqueIDHex") if isinstance(raw, dict) else None
        if not isinstance(repository_id, str) or not re.fullmatch(r"[0-9a-f]{64}", repository_id):
            raise BackendError("KOPIA_REPOSITORY_ID_UNAVAILABLE")
        if bind_created and not self._repository_id:
            self._repository_id = repository_id
        if repository_id != self._repository_id:
            self._connected = False
            raise BackendError("KOPIA_REPOSITORY_ID_MISMATCH")
        options = raw.get("clientOptions")
        if not isinstance(options, dict) or options.get("enableActions") is not False:
            raise BackendError("KOPIA_ACTIONS_MUST_BE_DISABLED")
        return {"status": "REPOSITORY_CONNECTED", "repository_id": repository_id,
                "provider": self.config.provider, "readonly": self._readonly,
                "config_sha256": self.config.public_fingerprint(), "actions_enabled": False}

    def repository_status(self) -> dict:
        self._enabled()
        if not self._connected:
            raise BackendError("KOPIA_NOT_CONNECTED")
        return self._read_repository_status()

    @staticmethod
    def _snapshot(raw):
        if not isinstance(raw, dict):
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        root = raw.get("rootEntry") or {}
        stats = raw.get("stats") or {}
        if not all(isinstance(value, dict) for value in
                   (root, stats, raw.get("source", {}), raw.get("tags", {}))):
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        summary = root.get("summ") or {}
        if not isinstance(summary, dict):
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        snapshot_id, obj = raw.get("id"), root.get("obj")
        if (not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id)
                or not isinstance(obj, str) or not re.fullmatch(r"[a-zA-Z0-9]{1,256}", obj)):
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        incomplete = bool(raw.get("incompleteReason") or stats.get("errorCount") or
                          stats.get("ignoredErrorCount") or summary.get("numFailed"))
        size, count = summary.get("size", stats.get("totalSize", 0)), summary.get("files", stats.get("fileCount", 0))
        if type(size) is not int or size < 0 or type(count) is not int or count < 0:
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        return {"snapshot_id": snapshot_id, "root_object_id": obj,
                "backup_set_id": (raw.get("tags") or {}).get("tag:backup-set"),
                "source_sha256": (raw.get("tags") or {}).get("tag:source-sha256"),
                "source_path": (raw.get("source") or {}).get("path"),
                "logical_bytes": size,
                "file_count": count,
                "excluded_file_count": stats.get("excludedFileCount", 0),
                "incomplete": incomplete, "started_at": raw.get("startTime"),
                "finished_at": raw.get("endTime"), "pins": raw.get("pins", []), "reused": False}

    def list_snapshots(self, *, backup_set_id: str | None = None) -> list[dict]:
        self.repository_status()
        args = ["snapshot", "list", "--all", "--show-identical", "--incomplete", "--json"]
        if backup_set_id is not None:
            self._set_id(backup_set_id)
            args += ["--tags", "backup-set:" + backup_set_id]
        raw = self._run(args, readonly=True, json_result=True)
        if not isinstance(raw, list):
            raise BackendError("KOPIA_SNAPSHOT_RESPONSE_INVALID")
        values = [self._snapshot(item) for item in raw]
        return [v for v in values if backup_set_id is None or v["backup_set_id"] == backup_set_id]

    @staticmethod
    def _set_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            raise BackendError("KOPIA_BACKUP_SET_ID_INVALID")

    def _find_snapshot(self, snapshot_id: str) -> dict:
        if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot_id):
            raise BackendError("KOPIA_SNAPSHOT_ID_INVALID")
        found = [s for s in self.list_snapshots() if s["snapshot_id"] == snapshot_id]
        if len(found) != 1 or found[0]["incomplete"]:
            raise BackendError("ASSET_UNAVAILABLE")
        return found[0]

    def create_snapshot(self, source: Path, *, backup_set_id: str, upload_limit_bytes: int,
                        resume_only: bool = False) -> dict:
        self._enabled()
        self._set_id(backup_set_id)
        source = _safe_path(Path(source), must_exist=True)
        inventory = _source_inventory(source, content_hash=True, checkpoint=self._check_deadline)
        if type(upload_limit_bytes) is not int or upload_limit_bytes < 1024**2:
            raise BackendError("KOPIA_UPLOAD_LIMIT_BELOW_CLI_MINIMUM")
        if type(resume_only) is not bool:
            raise BackendError("KOPIA_RESUME_BOOLEAN_REQUIRED")
        if source.is_relative_to(self.workspace) or self.workspace.is_relative_to(source):
            raise BackendError("KOPIA_SOURCE_WORKSPACE_OVERLAP")
        if self._local:
            repo = _safe_path(Path(self.config.repository_path))
            if source.is_relative_to(repo) or repo.is_relative_to(source):
                raise BackendError("KOPIA_SOURCE_REPOSITORY_OVERLAP")
        existing = self.list_snapshots(backup_set_id=backup_set_id)
        if existing:
            if len(existing) != 1 or existing[0]["incomplete"]:
                raise BackendError("KOPIA_BACKUP_SET_SNAPSHOT_CONFLICT")
            snapshot = existing[0]
            if (os.path.normcase(str(Path(snapshot["source_path"]).resolve())) != os.path.normcase(str(source))
                    or any(snapshot[key] != inventory[key] for key in inventory)):
                raise BackendError("KOPIA_BACKUP_SET_SOURCE_CONFLICT")
            return {**snapshot, "reused": True, "metrics": {"uploaded_bytes": _unknown()}}
        if resume_only:
            raise BackendError("SNAPSHOT_OUTCOME_UNRESOLVED")
        self._write_gate()
        start = time.monotonic()
        # Kopia cap uses whole MiB and is not a provider-billed network meter.
        limit_mb = upload_limit_bytes // (1024**2)
        raw = self._run(["snapshot", "create", str(source), "--json", "--fail-fast", "--force-hash=100",
                         "--no-send-snapshot-report", "--parallel=" + str(self.config.max_parallel),
                         "--upload-limit-mb=" + str(limit_mb), "--tags", "backup-set:" + backup_set_id,
                         "--tags", "source-sha256:" + inventory["source_sha256"],
                         "--pin", "action-backup-retention"], json_result=True)
        snapshot = self._snapshot(raw)
        if snapshot["backup_set_id"] != backup_set_id or snapshot["incomplete"]:
            raise BackendError("KOPIA_SNAPSHOT_INCOMPLETE")
        if any(snapshot[key] != inventory[key] for key in inventory):
            raise BackendError("KOPIA_SNAPSHOT_SOURCE_COUNTS_MISMATCH")
        if _source_inventory(source, content_hash=True, checkpoint=self._check_deadline) != inventory:
            raise BackendError("KOPIA_SOURCE_CHANGED")
        return {**snapshot, "metrics": {"elapsed_seconds": time.monotonic() - start,
                "uploaded_bytes": _unknown(), "upload_limit_mib": limit_mb,
                "limit_scope": "KOPIA_UPLOAD_LIMIT_NOT_BILLED_TRAFFIC"}}

    def verify_snapshot(self, snapshot_id: str) -> dict:
        snapshot = self._find_snapshot(snapshot_id)
        raw = self._run(["snapshot", "verify", snapshot_id, "--verify-files-percent=100", "--json",
                         "--parallel=" + str(self.config.max_parallel), "--file-parallelism=1"],
                        readonly=True, json_result=True)
        if not isinstance(raw, dict) or raw.get("errorCount") != 0:
            raise BackendError("KOPIA_SNAPSHOT_VERIFICATION_FAILED")
        stats = raw.get("stats", {})
        # Kopia reads each unique file object once. Two identical files produce
        # readFileCount=1 but expectedTotalFileCount=2; dedup is not a missing read.
        read_files = stats.get("readFileCount")
        if (stats.get("expectedTotalFileCount") != snapshot["file_count"]
                or type(read_files) is not int or read_files < 0
                or (snapshot["file_count"] > 0 and read_files == 0)):
            raise BackendError("KOPIA_SNAPSHOT_VERIFICATION_INCOMPLETE")
        return {"status": "SNAPSHOT_VERIFIED", "snapshot_id": snapshot_id,
                "file_count": snapshot["file_count"], "unique_file_objects_read": read_files,
                "unique_object_bytes_read": stats.get("readBytes"),
                "verification": "KOPIA_100_PERCENT_FILES", "business_restore_verified": False,
                "downloaded_bytes": _unknown()}

    def restore_snapshot(self, snapshot_id: str, target: Path) -> dict:
        snapshot = self._find_snapshot(snapshot_id)
        target = _safe_path(Path(target))
        if target.exists() or not target.parent.is_dir():
            raise BackendError("KOPIA_NEW_RESTORE_TARGET_REQUIRED")
        if target.is_relative_to(self.workspace) or self.workspace.is_relative_to(target):
            raise BackendError("KOPIA_RESTORE_WORKSPACE_OVERLAP")
        if self._local:
            repo = _safe_path(Path(self.config.repository_path))
            if target.is_relative_to(repo) or repo.is_relative_to(target):
                raise BackendError("KOPIA_RESTORE_REPOSITORY_OVERLAP")
        # Claim an empty target exclusively. Every existing-file overwrite and
        # remote snapshot deletion flag is disabled; failures retain artifacts.
        try:
            _private_workspace(target)
        except (OSError, subprocess.SubprocessError):
            raise BackendError("KOPIA_PRIVATE_RESTORE_DIRECTORY_FAILED") from None
        start = time.monotonic()
        self._run(["snapshot", "restore", snapshot["root_object_id"], str(target),
                   "--no-overwrite-files", "--no-overwrite-directories", "--no-overwrite-symlinks",
                   "--no-delete-extra", "--no-ignore-errors", "--write-files-atomically",
                   "--parallel=" + str(self.config.max_parallel)])
        restored = _source_inventory(target, checkpoint=self._check_deadline)
        if any(restored[key] != snapshot[key] for key in restored):
            raise BackendError("KOPIA_RESTORE_COUNTS_MISMATCH")
        return {"status": "RESTORED", "snapshot_id": snapshot_id, "target": str(target),
                **restored, "business_restore_verified": False,
                "metrics": {"elapsed_seconds": time.monotonic() - start, "downloaded_bytes": _unknown()}}

    def storage_usage(self) -> dict:
        self.repository_status()
        raw = self._run(["blob", "list", "--json"], readonly=True, json_result=True)
        if not isinstance(raw, list):
            raise BackendError("KOPIA_USAGE_RESPONSE_INVALID")
        # Provider blob inventory is physical object size, not billed traffic.
        sizes = [item.get("length") if isinstance(item, dict) else None for item in raw]
        physical = ({"status": "OBSERVED", "value": sum(sizes)} if
                    all(type(n) is int and n >= 0 for n in sizes) else _unknown())
        cache = (_source_inventory(self.workspace / "cache", checkpoint=self._check_deadline)
                 if (self.workspace / "cache").is_dir() else {"logical_bytes": 0})
        snapshots = self.list_snapshots()
        return {"status": "OBSERVED" if physical["status"] == "OBSERVED" else "PARTIAL",
                "repository_id": self._repository_id, "repository_bytes": physical,
                "object_count": len(raw), "snapshot_count": len(snapshots),
                "logical_bytes": sum(s["logical_bytes"] for s in snapshots if not s["incomplete"]),
                "cache_bytes": {"status": "OBSERVED", "value": cache["logical_bytes"]},
                "uploaded_bytes": _unknown(), "downloaded_bytes": _unknown(), "billing_bytes": _unknown()}

    def disconnect(self) -> dict:
        self._enabled()
        if not self._connected:
            return {"status": "DISCONNECTED", "reused": True}
        self._run(["repository", "disconnect"])
        self._connected, self._readonly = False, True
        return {"status": "DISCONNECTED", "repository_id": self._repository_id}
