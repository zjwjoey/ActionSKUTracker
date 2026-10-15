"""Explicit backup jobs, immutable receipts, bounded Resume and isolated restore.

No hooks are installed into daily-run. The operator supplies the shared writer
lock directory; scheduling is opt-in after separate production acceptance.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, asdict
from functools import wraps
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .backup_sets import (IDENTIFIER, canonical, digest, now, read_json, safe_path, seal,
                          stage_plan, validate_seal, verify_restored, write_immutable, _validate_plan)
from .storage_foundation import StorageError, storage_lock


@dataclass(frozen=True)
class OperationPolicy:
    enabled: bool = False
    cloud_business_upload_authorized: bool = False
    max_upload_bytes: int = 8 * 1024**3
    max_restore_bytes: int = 8 * 1024**3
    max_scratch_bytes: int = 16 * 1024**3
    max_runtime_seconds: int = 1800
    max_attempts: int = 2
    # Transfer caps are conservative planning limits; CLI cannot meter billed
    # traffic. Strict metered cloud budgets therefore block, rather than lie.
    allow_estimated_cloud_budget: bool = False
    monthly_upload_warning_bytes: int = 32 * 1024**3

    def __post_init__(self):
        for name in ("enabled", "cloud_business_upload_authorized", "allow_estimated_cloud_budget"):
            if type(getattr(self, name)) is not bool:
                raise StorageError("BACKUP_BOOLEAN_REQUIRED")
        for name in ("max_upload_bytes", "max_restore_bytes", "max_scratch_bytes", "max_runtime_seconds",
                     "max_attempts", "monthly_upload_warning_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise StorageError("BACKUP_POSITIVE_BUDGET_REQUIRED")
        if self.max_attempts > 5:
            raise StorageError("BACKUP_RETRY_LIMIT_TOO_HIGH")


def public_backend_identity(backend) -> dict:
    config = backend.config
    values = {k: str(getattr(config, k, "")) for k in
              ("provider", "repository_path", "bucket", "endpoint", "region", "prefix", "purpose")}
    return {"provider": values["provider"], "config_sha256": digest(values),
            "repository_id": getattr(config, "expected_repository_id", "")}


def _repository_matches(backend, plan):
    expected = plan["repository"]
    public = public_backend_identity(backend)
    if public["config_sha256"] != expected["config_sha256"] or public["provider"] != expected["provider"]:
        raise StorageError("BACKUP_REPOSITORY_CONFIG_MISMATCH")
    status = backend.repository_status()
    if status.get("repository_id") != expected["repository_id"]:
        raise StorageError("BACKUP_REPOSITORY_ID_MISMATCH")


def _event(job: Path, status: str, **fields):
    events = sorted(job.glob("event-*.json"))
    previous = ""
    for entry in events:
        value = validate_seal(read_json(entry), "BACKUP_JOB_EVENT_V1")
        if value["previous_sha256"] != previous:
            raise StorageError("JOB_EVENT_CHAIN_INVALID")
        previous = value["document_sha256"]
    value = seal({"contract": "BACKUP_JOB_EVENT_V1", "sequence": len(events),
                  "previous_sha256": previous, "status": status, "created_at": now(), **fields})
    write_immutable(job / f"event-{len(events):06d}.json", value)
    return value


def job_events(job: Path) -> list[dict]:
    result, previous = [], ""
    for path in sorted(Path(job).glob("event-*.json")):
        event = validate_seal(read_json(path), "BACKUP_JOB_EVENT_V1")
        if event.get("previous_sha256") != previous or event.get("sequence") != len(result):
            raise StorageError("JOB_EVENT_CHAIN_INVALID")
        result.append(event); previous = event["document_sha256"]
    return result


def _checkpoint(job, start, policy):
    if (job / "cancel.json").exists():
        raise StorageError("BACKUP_CANCELLED")
    if time.monotonic() - start >= policy.max_runtime_seconds:
        raise StorageError("BACKUP_TIME_BUDGET_EXCEEDED")


def request_cancel(job: Path, *, requested_by: str) -> dict:
    job = safe_path(job, file=False)
    if not requested_by or not (job / "plan.json").is_file():
        raise StorageError("CANCEL_REQUEST_INVALID")
    record = seal({"contract": "BACKUP_CANCEL_V1", "requested_by": requested_by,
                   "created_at": now(), "plan_sha256": read_json(job / "plan.json")["document_sha256"]})
    write_immutable(job / "cancel.json", record)
    return {"status": "CANCELLATION_REQUESTED", "effect": "AT_NEXT_STAGE_BOUNDARY"}


def resume_cancelled(job: Path, *, requested_by: str):
    """Explicit operator resumes this job; preserve the cancellation evidence."""
    job = safe_path(job, file=False)
    if not requested_by:
        raise StorageError("RESUME_REQUEST_INVALID")
    with storage_lock(job, run_id="resume-cancelled"):
        path = job / "cancel.json"
        if path.exists():
            record = validate_seal(read_json(path), "BACKUP_CANCEL_V1")
            if record["plan_sha256"] != read_json(job / "plan.json")["document_sha256"]:
                raise StorageError("CANCEL_PLAN_MISMATCH")
            _event(job, "CANCELLATION_RELEASED", requested_by=requested_by,
                   cancellation_sha256=record["document_sha256"])
            # Only our exact cancellation record; not a business asset.
            path.rename(job / ("cancel-released-" + record["document_sha256"] + ".json"))


def _process_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        raise StorageError("LOCK_OWNER_INVALID")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied/unknown is alive/blocked. Never terminate a process.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reconcile_job_lock(job: Path, *, expected_lock_sha256: str, requested_by: str) -> dict:
    """Explicit recovery of this module's dead job lock, never a writer lock."""
    from .backup_sets import file_identity
    from .storage_foundation import sha256_file
    job = safe_path(job, file=False)
    if job.parent.name != "jobs" or not requested_by:
        raise StorageError("JOB_LOCK_RECONCILIATION_SCOPE_INVALID")
    plan = _validate_plan(read_json(job / "plan.json"))
    job_events(job)
    lock = safe_path(job / "daily-run.lock")
    identity = file_identity(lock)
    value = read_json(lock)
    if (sha256_file(lock) != expected_lock_sha256 or value.get("run_id") != plan["run_id"] or
            value.get("command") != "storage-baseline" or
            not re.fullmatch(r"[0-9a-f]{32}", value.get("storage_token", ""))):
        raise StorageError("JOB_LOCK_OWNER_MISMATCH")
    if _process_alive(value.get("pid")):
        raise StorageError("JOB_LOCK_OWNER_STILL_ACTIVE")
    archive = job / ("reconciled-lock-" + expected_lock_sha256 + ".json")
    write_immutable(archive, value)
    if file_identity(lock) != identity or sha256_file(lock) != expected_lock_sha256:
        raise StorageError("JOB_LOCK_CHANGED_DURING_RECONCILIATION")
    lock.unlink()
    _event(job, "DEAD_JOB_LOCK_RECONCILED", requested_by=requested_by, lock_sha256=expected_lock_sha256)
    return {"status": "JOB_LOCK_RECONCILED", "writer_lock_changed": False}


def _usage_snapshot(backend):
    value = backend.storage_usage()
    return value if isinstance(value, dict) else {"status": "UNKNOWN"}


def _staging_budget(operations_root, plan, policy):
    already = 0
    for directory in (operations_root / "captures", operations_root / "sessions",
                      operations_root / 'capture-workspaces', operations_root / 'capture-lifecycle'):
        if directory.is_dir():
            for existing in directory.rglob("*"):
                if existing.is_file():
                    already += safe_path(existing).stat().st_size
    for old_job in (operations_root / "jobs").glob("*"):
        if old_job.name == plan["backup_set_id"]:
            continue
        old_stage = old_job / "stage"
        if old_stage.is_dir():
            for existing in old_stage.rglob("*"):
                if existing.is_file():
                    already += safe_path(existing).stat().st_size
    if already + plan["logical_bytes"] + len(canonical(plan)) + 1 > policy.max_scratch_bytes:
        raise StorageError("TOTAL_STAGING_BUDGET_EXCEEDED")


def storage_usage_report(operations_root: Path, *, backend, policy: OperationPolicy) -> dict:
    """Separate observed storage from estimates; never invent provider billing."""
    rows = history(operations_root)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    estimate = sum(r["logical_bytes"] * 2 + 16 * 1024**2 for r in rows
                   if r["created_at"].startswith(month) and r["snapshot_id"] is not None)
    scratch = 0
    for job in (Path(operations_root) / "jobs").glob("*"):
        stage = job / "stage"
        if stage.is_dir():
            for file in stage.rglob("*"):
                if file.is_file():
                    scratch += safe_path(file).stat().st_size
    # A backend reports only its current private session. Include all prior
    # sessions and captures in the local budget, without double counting it.
    other = {}
    for name in ("captures", "sessions", 'capture-workspaces', 'capture-lifecycle'):
        directory = safe_path(Path(operations_root) / name, file=False)
        other[name] = 0
        if directory.is_dir():
            for entry in directory.rglob("*"):
                checked = safe_path(entry, file=entry.is_file())
                if checked.is_file():
                    other[name] += checked.stat().st_size
    total_scratch = scratch + sum(other.values())
    return {"status": "OBSERVED_WITH_ESTIMATES", "backup_sets": len(rows),
            "snapshot_versions": sum(r["snapshot_id"] is not None for r in rows),
            "logical_backup_bytes": sum(r["logical_bytes"] for r in rows),
            "staged_bytes": {"status": "OBSERVED", "value": scratch},
            "capture_bytes": {"status": "OBSERVED", "value": other["captures"]},
            "managed_workspace_bytes": {"status": "OBSERVED", "value": other['capture-workspaces']},
            "capture_lifecycle_bytes": {"status": "OBSERVED", "value": other['capture-lifecycle']},
            "session_bytes": {"status": "OBSERVED", "value": other["sessions"]},
            "total_scratch_bytes": {"status": "OBSERVED", "value": total_scratch},
            "scratch_scope": "job stages, all captures and all sessions under operations_root; external restores excluded",
            "backend": _usage_snapshot(backend),
            "monthly_upload_bytes": {"status": "ESTIMATED", "value": estimate},
            "monthly_actual_transfer_bytes": {"status": "UNKNOWN", "value": None},
            "monthly_budget_alert": estimate >= policy.monthly_upload_warning_bytes,
            "scratch_budget_alert": total_scratch >= policy.max_scratch_bytes,
            "failed_jobs": sum(r["status"] == "FAILED" for r in rows),
            "billing_amount": {"status": "UNKNOWN", "value": None}}


def _timeboxed(function):
    @wraps(function)
    def call(*args, **kwargs):
        backend, policy = kwargs["backend"], kwargs["policy"]
        if policy.enabled and hasattr(backend, "set_deadline"):
            previous = getattr(backend, '_deadline', None)
            deadline = time.monotonic() + policy.max_runtime_seconds
            backend.set_deadline(min(previous, deadline) if previous is not None else deadline)
            try:
                return function(*args, **kwargs)
            finally:
                backend.set_deadline(previous)
        return function(*args, **kwargs)
    return call


def cloud_selection_scope(plan: dict, roots: dict[str, str], operations_root: Path) -> str:
    """Bind recurring grants to real source locations, not just portable IDs."""
    selected = {}
    for root_id in plan["source_roots"]:
        if root_id not in roots:
            raise StorageError("ASSET_UNAVAILABLE")
        path = safe_path(Path(roots[root_id]), file=False)
        owned_capture = safe_path(operations_root, file=False) / "captures" / plan["run_id"]
        if path == owned_capture or _managed_capture(operations_root, plan['run_id'], path):
            context = read_json(path / "capture-context.json")
            if (context.get("contract") != "CORE_CAPTURE_CONTEXT_V1" or
                    context.get("run_id") != plan["run_id"] or not context.get("projection_sources")):
                raise StorageError("CAPTURE_SCOPE_INCOMPLETE")
            selected[root_id] = {k: context[k] for k in ("source", "projection_sources", "code_version")}
        else:
            selected[root_id] = {"path": str(path)}
    return digest({"profile": plan["profile"], "source_roots": selected,
                   "asset_selection": [{k: a.get(k) for k in ("asset_id", "root_id", "relative_path", "kind", "sku")}
                                       for a in plan["assets"]]})


def _authorize_cloud(grant, plan, policy, selection_scope, operation_configuration_sha256):
    try:
        expires = datetime.fromisoformat(grant.get("expires_at", ""))
    except (ValueError, TypeError):
        raise StorageError("BUSINESS_CLOUD_UPLOAD_NOT_AUTHORIZED") from None
    common = (policy.cloud_business_upload_authorized and grant.get("approved_by") and
              grant.get("approval_id") and expires.tzinfo is not None and
              expires > datetime.now(timezone.utc) and
              grant.get("repository_id") == plan["repository"]["repository_id"])
    exact = grant.get("plan_sha256") == plan["document_sha256"]
    scoped = (grant.get("contract") == "CLOUD_BACKUP_AUTHORIZATION_V1" and
              grant.get("authorization_scope") == "FIRST_AND_RECURRING_BUSINESS_BACKUP" and
              grant.get("config_sha256") == plan["repository"]["config_sha256"] and
              grant.get("profile") == plan["profile"] and
              grant.get("source_root_ids") == plan["source_roots"] and
              grant.get("source_selection_sha256") == selection_scope and
              bool(operation_configuration_sha256) and
              grant.get("operation_configuration_sha256") == operation_configuration_sha256 and
              set(a["kind"] for a in plan["assets"]).issubset(set(grant.get("allowed_asset_kinds", []))) and
              type(grant.get("max_logical_bytes")) is int and
              plan["logical_bytes"] <= grant["max_logical_bytes"] and grant.get("instruction_reference"))
    if not common or not (exact or scoped):
        raise StorageError("BUSINESS_CLOUD_UPLOAD_NOT_AUTHORIZED")


def _managed_capture(operations_root, run_id, source):
    from .capture_lifecycle import owns_capture
    return owns_capture(operations_root, run_id, source)


@_timeboxed
def run_backup(plan: dict, roots: dict[str, str], *, backend, operations_root: Path,
               writer_lock_directory: Path, policy: OperationPolicy,
               synthetic_only: bool = False, cloud_authorization: dict | None = None,
               operation_configuration_sha256: str | None = None) -> dict:
    if not policy.enabled:
        return {"status": "DISABLED", "side_effects": False}
    _validate_plan(plan)
    if getattr(backend.config, "purpose", "business") == "synthetic_test" and not synthetic_only:
        raise StorageError("SYNTHETIC_REPOSITORY_REJECTS_BUSINESS_DATA")
    cloud = plan["repository"]["provider"] not in {"filesystem", "local"}
    if cloud:
        if not policy.allow_estimated_cloud_budget:
            raise StorageError("EXACT_CLOUD_TRANSFER_BUDGET_UNAVAILABLE")
        if not synthetic_only:
            grant = cloud_authorization or {}
            _authorize_cloud(grant, plan, policy, cloud_selection_scope(plan, roots, operations_root),
                             operation_configuration_sha256)
        else:
            from .backup_sets import synthetic_payload
            import hashlib
            if getattr(backend.config, "purpose", None) != "synthetic_test":
                raise StorageError("SYNTHETIC_REPOSITORY_PURPOSE_REQUIRED")
            for asset in plan["assets"]:
                payload = synthetic_payload(asset.get("synthetic_recipe"))
                if asset["sha256"] != hashlib.sha256(payload).hexdigest() or asset["size_bytes"] != len(payload):
                    raise StorageError("SYNTHETIC_CONTENT_PROVENANCE_REQUIRED")
            if plan["logical_bytes"] > 1024**2:
                raise StorageError("SYNTHETIC_UPLOAD_LIMIT_EXCEEDED")
    # Includes a conservative allowance for metadata/encryption packs, not a
    # promise about network egress or the provider's invoice.
    estimated_upload = plan["logical_bytes"] * 2 + 16 * 1024**2
    if estimated_upload > policy.max_upload_bytes:
        raise StorageError("UPLOAD_PLANNING_BUDGET_EXCEEDED")
    operations_root = safe_path(operations_root, file=False)
    writer_lock_directory = safe_path(writer_lock_directory, file=False)
    for source_root in roots.values():
        source = safe_path(Path(source_root), file=False)
        owned_capture = (source == operations_root / "captures" / plan["run_id"] or
                         _managed_capture(operations_root, plan['run_id'], source))
        if owned_capture:
            capture = read_json(source / "capture-context.json")
            if capture.get("contract") != "CORE_CAPTURE_CONTEXT_V1" or capture.get("run_id") != plan["run_id"]:
                raise StorageError("CAPTURE_DIRECTORY_UNOWNED")
        if operations_root.is_relative_to(source) or (source.is_relative_to(operations_root) and not owned_capture):
            raise StorageError("OPERATIONS_SOURCE_OVERLAP")
    operations_root.mkdir(parents=True, exist_ok=True)
    job = safe_path(operations_root / "jobs" / plan["backup_set_id"], file=False)
    if job.exists() and not (job / "plan.json").exists():
        raise StorageError("UNKNOWN_JOB_DIRECTORY")
    job.mkdir(parents=True, exist_ok=True)
    with storage_lock(job, run_id=plan["run_id"]):
        write_immutable(job / "plan.json", plan)
        _repository_matches(backend, plan)
        receipt_path = job / "receipt.json"
        if receipt_path.exists():
            receipt = validate_seal(read_json(receipt_path), "BACKUP_SET_V1")
            if receipt.get("plan_sha256") != plan["document_sha256"]:
                raise StorageError("BACKUP_RECEIPT_CONFLICT")
            snapshots = backend.list_snapshots(backup_set_id=plan["backup_set_id"])
            if not any(s["snapshot_id"] == receipt["snapshot_id"] for s in snapshots):
                raise StorageError("BACKUP_SNAPSHOT_UNAVAILABLE")
            return {**receipt, "reused": True}
        events = job_events(job)
        attempts = sum(e["status"] == "ATTEMPT_STARTED" for e in events)
        if attempts >= policy.max_attempts:
            raise StorageError("BACKUP_ATTEMPT_BUDGET_EXHAUSTED")
        start = time.monotonic()
        _checkpoint(job, start, policy)
        before_usage = _usage_snapshot(backend)
        _event(job, "ATTEMPT_STARTED", attempt=attempts + 1)
        try:
            with storage_lock(writer_lock_directory, run_id=plan["run_id"]):
                _checkpoint(job, start, policy)
                _staging_budget(operations_root, plan, policy)
                stage = job / "stage"
                stage_plan(plan, roots, stage, max_scratch_bytes=policy.max_scratch_bytes,
                           checkpoint=lambda: _checkpoint(job, start, policy))
                _event(job, "STAGED")
                _checkpoint(job, start, policy)
                # A previous write might have committed even if its process
                # timed out. Resume may discover it, but may not write again.
                uncertain = any(e["status"] == "SNAPSHOT_WRITE_STARTED" for e in events)
                if not uncertain:
                    _event(job, "SNAPSHOT_WRITE_STARTED")
                snapshot = backend.create_snapshot(stage, backup_set_id=plan["backup_set_id"],
                    upload_limit_bytes=policy.max_upload_bytes, resume_only=uncertain)
                if snapshot.get("incomplete"):
                    raise StorageError("SNAPSHOT_INCOMPLETE")
                _event(job, "SNAPSHOT_CREATED", snapshot_id=snapshot["snapshot_id"])
                _checkpoint(job, start, policy)
                verification = backend.verify_snapshot(snapshot["snapshot_id"])
                if verification.get("status") not in {"PASS", "SNAPSHOT_VERIFIED", "VERIFIED"}:
                    raise StorageError("SNAPSHOT_VERIFICATION_FAILED")
                _checkpoint(job, start, policy)
                after_usage = _usage_snapshot(backend)
                receipt = seal({"contract": "BACKUP_SET_V1", "backup_set_id": plan["backup_set_id"],
                    "run_id": plan["run_id"], "created_at": now(), "plan_sha256": plan["document_sha256"],
                    "plan": plan, "repository": plan["repository"], "snapshot_id": snapshot["snapshot_id"],
                    "root_object_id": snapshot.get("root_object_id"), "profile": plan["profile"],
                    "upload_status": "SNAPSHOT_COMMITTED", "verification_status": "SNAPSHOT_VERIFIED",
                    "restore_status": "NOT_TESTED", "config_version": plan["config_version"],
                    "recovery_owner": plan["recovery_owner"], "retention": plan["retention"],
                    "dependencies": plan["dependencies"], "logical_bytes": plan["logical_bytes"],
                    "cloud_authorization_id": (cloud_authorization or {}).get("approval_id"),
                    "metrics": {"elapsed_seconds": time.monotonic() - start,
                        "attempts": attempts + 1, "failed_attempts": sum(e["status"] == "FAILED" for e in events),
                        "upload_planning_bytes": {"status": "ESTIMATED", "value": estimated_upload},
                        "transfer": snapshot.get("metrics", {}), "repository_before": before_usage,
                        "repository_after": after_usage}, "production_switched": False})
                write_immutable(receipt_path, receipt)
                _event(job, "VERIFIED", receipt_sha256=receipt["document_sha256"])
                return receipt
        except Exception as exc:
            # Safe category only; never serialize subprocess/stdout/credentials.
            reason = str(exc) if isinstance(exc, StorageError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", str(exc)) else "BACKUP_STAGE_FAILED"
            _event(job, "CANCELLED" if reason == "BACKUP_CANCELLED" else "FAILED", reason=reason)
            raise StorageError(reason) from None


def restore_preview(receipt: dict, *, backend, target: Path, policy: OperationPolicy,
                    protected_roots: list[Path]) -> dict:
    validate_seal(receipt, "BACKUP_SET_V1")
    plan = _validate_plan(receipt["plan"])
    if receipt["plan_sha256"] != plan["document_sha256"] or receipt["repository"] != plan["repository"]:
        raise StorageError("RESTORE_RECEIPT_PLAN_MISMATCH")
    _repository_matches(backend, plan)
    snapshots = backend.list_snapshots(backup_set_id=plan["backup_set_id"])
    if not any(s["snapshot_id"] == receipt["snapshot_id"] and not s.get("incomplete") for s in snapshots):
        raise StorageError("BACKUP_SNAPSHOT_UNAVAILABLE")
    target = safe_path(target, file=False)
    if target.exists():
        raise StorageError("RESTORE_TARGET_ALREADY_EXISTS")
    for protected in protected_roots:
        protected = safe_path(protected, file=False)
        if target.is_relative_to(protected) or protected.is_relative_to(target):
            raise StorageError("RESTORE_PROTECTED_ROOT_OVERLAP")
    download_estimate = plan["logical_bytes"] * 2 + 16 * 1024**2
    needed = plan["logical_bytes"] + len(canonical(plan)) + 64 * 1024**2
    if download_estimate > policy.max_restore_bytes or needed > policy.max_scratch_bytes:
        raise StorageError("RESTORE_BUDGET_EXCEEDED")
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < needed:
        raise StorageError("RESTORE_DISK_SPACE_INSUFFICIENT")
    if plan["repository"]["provider"] not in {"filesystem", "local"} and not policy.allow_estimated_cloud_budget:
        raise StorageError("EXACT_CLOUD_TRANSFER_BUDGET_UNAVAILABLE")
    return {"status": "PREVIEW_PASS", "backup_set_id": plan["backup_set_id"],
            "snapshot_id": receipt["snapshot_id"], "target": str(target), "required_free_bytes": needed,
            "download_bytes": {"status": "ESTIMATED", "value": download_estimate},
            "files": len(plan["assets"]) + 1, "production_switch_allowed": False}


@_timeboxed
def restore_backup(receipt: dict, *, backend, target: Path, policy: OperationPolicy,
                   protected_roots: list[Path], report_path: Path) -> dict:
    if not policy.enabled:
        return {"status": "DISABLED", "side_effects": False}
    report_path = safe_path(report_path, file=False)
    if report_path.exists():
        raise StorageError("RESTORE_REPORT_ALREADY_EXISTS")
    for protected in protected_roots:
        if report_path.is_relative_to(safe_path(protected, file=False)):
            raise StorageError("RESTORE_REPORT_PROTECTED_ROOT_OVERLAP")
    preview = restore_preview(receipt, backend=backend, target=target, policy=policy, protected_roots=protected_roots)
    target = safe_path(Path(preview["target"]), file=False)
    if report_path.is_relative_to(target) or target.is_relative_to(report_path):
        raise StorageError("RESTORE_REPORT_TARGET_OVERLAP")
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_path(target.parent, file=False)
    start = time.monotonic()
    result = backend.restore_snapshot(receipt["snapshot_id"], target)
    checked = verify_restored(receipt["plan"], target)
    report = seal({"contract": "BACKUP_RESTORE_V1", **checked, "receipt_sha256": receipt["document_sha256"],
                   "repository": receipt["repository"], "snapshot_id": receipt["snapshot_id"],
                   "target": str(Path(target).resolve()), "preview": preview,
                   "metrics": {"elapsed_seconds": time.monotonic() - start,
                               "download": result.get("metrics", {"status": "UNKNOWN"})}})
    write_immutable(report_path, report)
    return report


def release_owned_stage(job: Path, *, backend, restore_report: dict) -> dict:
    """Remove only newly staged copies after verified restore, never sources.

    This is an explicit operation and does not grant any cleanup entitlement
    for the historical source assets or for backend repository objects.
    """
    job = safe_path(job, file=False)
    with storage_lock(job, run_id="release-owned-stage"):
        receipt = validate_seal(read_json(job / "receipt.json"), "BACKUP_SET_V1")
        report = validate_seal(restore_report, "BACKUP_RESTORE_V1")
        if (report.get("status") != "ROUNDTRIP_PASS" or
                report.get("receipt_sha256") != receipt["document_sha256"] or
                report.get("snapshot_id") != receipt["snapshot_id"]):
            raise StorageError("STAGE_RELEASE_RESTORE_PROOF_REQUIRED")
        _repository_matches(backend, receipt["plan"])
        verification = backend.verify_snapshot(receipt["snapshot_id"])
        if verification.get("status") not in {"PASS", "SNAPSHOT_VERIFIED", "VERIFIED"}:
            raise StorageError("SNAPSHOT_VERIFICATION_FAILED")
        # Ensure the independent restoration is still available and intact.
        verify_restored(receipt["plan"], Path(report["target"]))
        stage = job / "stage"
        restore_root = safe_path(Path(report["target"]), file=False)
        if restore_root.is_relative_to(job) or job.is_relative_to(restore_root):
            raise StorageError("INDEPENDENT_RESTORE_ROOT_REQUIRED")
        if not stage.exists():
            prior = [e for e in job_events(job) if e["status"] == "OWNED_STAGE_RELEASED"]
            if prior:
                return {"status": "ALREADY_RELEASED", "logical_bytes": prior[-1]["logical_bytes"]}
            raise StorageError("OWNED_STAGE_MISSING_WITHOUT_RECEIPT")
        verify_restored(receipt["plan"], stage)
        before = shutil.disk_usage(job).free
        files = [stage / a["snapshot_path"] for a in receipt["plan"]["assets"]] + [stage / "backup-set.json"]
        logical = sum(p.stat().st_size for p in files)
        _event(job, "OWNED_STAGE_RELEASE_STARTED", restore_sha256=report["document_sha256"], logical_bytes=logical)
        for file in files:
            safe_path(file).unlink()
        # Empty directories only. An unexpected new file prevents removal;
        # never recursively delete a historical tree or unknown contents.
        for directory in sorted([p for p in stage.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
            safe_path(directory, file=False).rmdir()
        stage.rmdir()
        observed = shutil.disk_usage(job).free - before
        _event(job, "OWNED_STAGE_RELEASED", logical_bytes=logical, volume_free_delta_bytes=observed,
               restore_sha256=report["document_sha256"])
        return {"status": "OWNED_STAGE_RELEASED", "logical_bytes": logical,
                "volume_free_delta_bytes": observed, "historical_assets_deleted": 0}


def history(operations_root: Path) -> list[dict]:
    result = []
    for job in sorted((Path(operations_root) / "jobs").glob("*")):
        if not job.is_dir():
            continue
        plan = _validate_plan(read_json(job / "plan.json"))
        events = job_events(job)
        receipt = validate_seal(read_json(job / "receipt.json"), "BACKUP_SET_V1") if (job / "receipt.json").exists() else None
        result.append({"backup_set_id": plan["backup_set_id"], "created_at": plan["created_at"],
                       "profile": plan["profile"], "logical_bytes": plan["logical_bytes"],
                       "status": events[-1]["status"] if events else "PLANNED",
                       "snapshot_id": receipt["snapshot_id"] if receipt else None,
                       "receipt_sha256": receipt["document_sha256"] if receipt else None})
    return result


def retention_preview(receipts: list[dict], *, daily: int = 7, weekly: int = 4, monthly: int = 12,
                      restore_reports: list[dict] | None = None, release_decisions: list[dict] | None = None) -> dict:
    if any(type(v) is not int or v < 1 for v in (daily, weekly, monthly)):
        raise StorageError("RETENTION_COUNTS_INVALID")
    ordered = sorted([validate_seal(r, "BACKUP_SET_V1") for r in receipts], key=lambda r: r["created_at"], reverse=True)
    restores = {r["document_sha256"]: validate_seal(r, "BACKUP_RESTORE_V1") for r in (restore_reports or [])}
    decisions = {}
    for decision in release_decisions or []:
        validate_seal(decision, "SNAPSHOT_RETENTION_DECISION_V1")
        key = decision.get("receipt_sha256")
        if key in decisions:
            raise StorageError("RETENTION_DECISION_AMBIGUOUS")
        decisions[key] = decision
    groups, output = {}, []
    for receipt in ordered:
        key = (receipt["repository"]["repository_id"], receipt["profile"],
               digest(receipt["plan"]["source_roots"]))
        counts = groups.setdefault(key, {"day": set(), "week": set(), "month": set()})
        t = datetime.fromisoformat(receipt["created_at"])
        if t.tzinfo is None:
            raise StorageError("RETENTION_TIMESTAMP_INVALID")
        periods = {"day": t.date().isoformat(), "week": t.strftime("%G-W%V"), "month": t.strftime("%Y-%m")}
        keep = any(periods[p] not in counts[p] and len(counts[p]) < limit
                   for p, limit in (("day", daily), ("week", weekly), ("month", monthly)))
        for p in periods: counts[p].add(periods[p])
        responsibility = receipt["retention"]
        decision = decisions.get(receipt["document_sha256"], {})
        restored = restores.get(decision.get("restore_report_sha256"), {})
        try:
            release_after = datetime.fromisoformat(decision.get("release_after", ""))
            expires = datetime.fromisoformat(decision.get("expires_at", ""))
            timely = (release_after.tzinfo is not None and expires.tzinfo is not None and
                      release_after <= datetime.now(timezone.utc) < expires)
        except (ValueError, TypeError):
            timely = False
        eligible = bool(not keep and timely and decision.get("approved_by") and decision.get("approval_id") and
                    decision.get("policy_id") == responsibility.get("policy_id") and
                    decision.get("recovery_required") is False and decision.get("reference_status") == "NO_REFERENCES" and
                    restored.get("status") == "ROUNDTRIP_PASS" and
                    restored.get("receipt_sha256") == receipt["document_sha256"] and
                    restored.get("snapshot_id") == receipt["snapshot_id"])
        output.append({"backup_set_id": receipt["backup_set_id"], "snapshot_id": receipt["snapshot_id"],
                       "status": "EXPIRATION_REVIEW_REQUIRED" if eligible else "RETAIN",
                       "delete_allowed": False, "maintenance_allowed": False})
    return {"status": "PREVIEW_ONLY", "snapshots": output, "remote_mutations": 0}
