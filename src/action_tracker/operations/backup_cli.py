"""Storage Backup V1 CLI. No normal daily-run imports or production activation.

Run with PYTHONPATH=src: python -m action_tracker.operations.backup_cli --help
Configuration is a dedicated, explicit JSON file with no credential fields.
Credentials are supplied by the environment or an external private bridge.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path

from .backup_sets import (make_plan, read_json, safe_path, seal, validate_seal, write_immutable)
from .backup_operations import (OperationPolicy, history, public_backend_identity, request_cancel,
                                restore_backup, restore_preview, retention_preview, resume_cancelled, run_backup,
                                release_owned_stage, storage_usage_report)
from .backup_scheduler import SchedulePolicy, event_key, windows_task_xml
from .storage_foundation import StorageError, storage_lock


def load_config(path):
    raw = read_json(path)
    allowed = {"contract", "backend", "policy", "operations_root", "writer_lock_directory", "roots",
               "protected_roots", "schedule", "request_path", "capture", "config_version", "activation_receipt_path",
               "cloud_authorization_path"}
    if set(raw) - allowed or raw.get("contract") != "BACKUP_OPERATIONS_CONFIG_V1":
        raise StorageError("BACKUP_CONFIG_INVALID_OR_UNKNOWN_FIELDS")
    # Do not allow passwords, access keys or arbitrary environment values here.
    from .kopia_backend import BackendConfig
    if set(raw.get("backend", {})) - {f.name for f in fields(BackendConfig)}:
        raise StorageError("BACKEND_CONFIG_UNKNOWN_FIELDS")
    config = BackendConfig(**raw.get("backend", {}))
    policy = OperationPolicy(**raw.get("policy", {}))
    schedule = SchedulePolicy(**raw.get("schedule", {}))
    for key in ("operations_root", "writer_lock_directory"):
        if not raw.get(key) or not Path(raw[key]).is_absolute():
            raise StorageError("BACKUP_ABSOLUTE_ROOT_REQUIRED")
    for path_value in [*raw.get("roots", {}).values(), *raw.get("protected_roots", [])]:
        if not Path(path_value).is_absolute():
            raise StorageError("BACKUP_ABSOLUTE_ROOT_REQUIRED")
    if not raw.get("protected_roots"):
        raise StorageError("PRODUCTION_PROTECTION_ROOTS_REQUIRED")
    operations = safe_path(Path(raw["operations_root"]), file=False)
    for root in raw["protected_roots"]:
        protected = safe_path(Path(root), file=False)
        if operations.is_relative_to(protected) or protected.is_relative_to(operations):
            raise StorageError("OPERATIONS_PROTECTED_ROOT_OVERLAP")
        if config.provider in {"filesystem", "local"} and config.repository_path:
            repository = safe_path(Path(config.repository_path), file=False)
            if repository.is_relative_to(protected) or protected.is_relative_to(repository):
                raise StorageError("REPOSITORY_PROTECTED_ROOT_OVERLAP")
    if schedule.enabled:
        from .backup_sets import digest
        acceptance = read_json(Path(raw.get("activation_receipt_path", "")))
        validation = validate_seal(read_json(Path(acceptance.get("restore_report_path", ""))), "BACKUP_RESTORE_V1")
        if (acceptance.get("contract") != "BACKUP_ACTIVATION_V1" or
                acceptance.get("production_enable_authorized") is not True or
                not acceptance.get("approved_by") or not acceptance.get("approval_id") or
                acceptance.get("configuration_sha256") != digest({k: v for k, v in raw.items() if k != "activation_receipt_path"}) or
                acceptance.get("restore_report_sha256") != validation["document_sha256"] or
                validation.get("status") != "ROUNDTRIP_PASS"):
            raise StorageError("SCHEDULE_PRODUCTION_ACCEPTANCE_REQUIRED")
    return raw, config, policy, schedule


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, type=Path)
    actions = p.add_subparsers(dest="action", required=True)
    for name in ("status", "history", "usage", "scheduled"):
        actions.add_parser(name)
    create = actions.add_parser("repository-create")
    create.add_argument("--authorization", type=Path, required=True)
    plan = actions.add_parser("plan")
    plan.add_argument("--request", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    capture = actions.add_parser("capture-core")
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--output", type=Path, required=True)
    execute = actions.add_parser("now")
    execute.add_argument("--plan", type=Path, required=True)
    execute.add_argument("--cloud-authorization", type=Path)
    for name in ("verify", "restore-preview", "restore"):
        command = actions.add_parser(name)
        command.add_argument("--receipt", type=Path, required=True)
        if name != "verify":
            command.add_argument("--target", type=Path, required=True)
        if name == "restore":
            command.add_argument("--report", type=Path, required=True)
    retention = actions.add_parser("retention-preview")
    retention.add_argument("--receipt", type=Path, action="append", required=True)
    retention.add_argument("--restore-report", type=Path, action="append", default=[])
    retention.add_argument("--release-decision", type=Path, action="append", default=[])
    for name in ("cancel", "resume-cancelled"):
        command = actions.add_parser(name)
        command.add_argument("--backup-set-id", required=True)
        command.add_argument("--requested-by", required=True)
    release = actions.add_parser("release-owned-stage")
    release.add_argument("--backup-set-id", required=True)
    release.add_argument("--restore-report", type=Path, required=True)
    reconcile = actions.add_parser("reconcile-job-lock")
    reconcile.add_argument("--backup-set-id", required=True)
    reconcile.add_argument("--expected-lock-sha256", required=True)
    reconcile.add_argument("--requested-by", required=True)
    schedule = actions.add_parser("schedule-export")
    schedule.add_argument("--output", type=Path, required=True)
    schedule.add_argument("--start-date", required=True)
    schedule.add_argument("--python", type=Path, default=Path(sys.executable))
    schedule.add_argument("--working-directory", type=Path, required=True)
    schedule.add_argument("--acceptance", type=Path)
    event = actions.add_parser("event")
    event.add_argument("--trigger", choices=["manual", "task_completed", "before_important_operation"], required=True)
    event.add_argument("--event-id", required=True)
    event.add_argument("--commit-id", required=True)
    return p


def _capture(raw, backend, run_id):
    from .backup_capture import capture_core
    from .backup_sets import IDENTIFIER
    if not IDENTIFIER.fullmatch(run_id):
        raise StorageError("BACKUP_ID_INVALID")
    capture = raw.get("capture") or {}
    allowed = {"source", "projections", "code_version", "recovery_owner", "retention"}
    if set(capture) - allowed or not allowed.issubset(capture):
        raise StorageError("CORE_CAPTURE_CONFIGURATION_REQUIRED")
    # Serialize admission as well as the existing production writer lock in
    # capture_core. Two different event IDs must not both pass an empty scan.
    operations = safe_path(Path(raw["operations_root"]), file=False)
    with storage_lock(operations / "capture-admission", run_id=run_id):
        return _capture_locked(raw, backend, run_id, capture, capture_core)


def _capture_locked(raw, backend, run_id, capture, capture_core):
    policy = OperationPolicy(**raw.get("policy", {}))
    operations = safe_path(Path(raw["operations_root"]), file=False)
    planned_capture = operations / "captures" / run_id
    # Captures are recovery assets, not disposable stage files. Until an
    # operator retires the prior capture through the existing Cleanup gates,
    # refuse another full copy. Never infer deletion rights from backup success.
    captures = safe_path(operations / "captures", file=False)
    if not planned_capture.exists() and captures.is_dir():
        for entry in captures.iterdir():
            checked = safe_path(entry, file=entry.is_file())
            if checked.is_file() or any(checked.iterdir()):
                raise StorageError("PRIOR_CAPTURE_REQUIRES_DISPOSITION")
    occupied = 0
    for directory in (operations / "captures", operations / "sessions", operations / "jobs"):
        if directory.is_dir():
            occupied += sum(safe_path(p).stat().st_size for p in directory.rglob("*") if p.is_file())
    if not planned_capture.exists():
        source = safe_path(Path(capture["source"]))
        needed = source.stat().st_size + sum(safe_path(Path(p)).stat().st_size for p in capture["projections"].values())
        wal = Path(str(source) + "-wal")
        if wal.exists(): needed += safe_path(wal).stat().st_size
        if occupied + needed > policy.max_scratch_bytes:
            raise StorageError("TOTAL_CAPTURE_BUDGET_EXCEEDED")
    return capture_core(source=Path(capture["source"]),
        projections={k: Path(v) for k, v in capture["projections"].items()},
        destination=Path(raw["operations_root"]) / "captures" / run_id,
        writer_lock_directory=Path(raw["writer_lock_directory"]), run_id=run_id,
        code_version=capture["code_version"], repository=public_backend_identity(backend),
        config_version=raw.get("config_version", "BACKUP_OPERATIONS_V1"),
        recovery_owner=capture["recovery_owner"], retention=capture["retention"],
        max_bytes=policy.max_scratch_bytes)


def execute(args):
    raw, cfg, policy, schedule = load_config(args.config)
    operations = Path(raw["operations_root"])
    for field in ("output", "report", "target"):
        output = getattr(args, field, None)
        if output is not None:
            resolved = safe_path(output, file=False)
            for protected in raw["protected_roots"]:
                root = safe_path(Path(protected), file=False)
                if resolved.is_relative_to(root) or root.is_relative_to(resolved):
                    raise StorageError("OUTPUT_PROTECTED_ROOT_OVERLAP")
    if args.action == "history":
        return {"status": "PASS", "jobs": history(operations)}
    if args.action == "retention-preview":
        return retention_preview([read_json(p) for p in args.receipt],
            restore_reports=[read_json(p) for p in args.restore_report],
            release_decisions=[read_json(p) for p in args.release_decision])
    if args.action in {"cancel", "resume-cancelled", "reconcile-job-lock"}:
        from .backup_sets import IDENTIFIER
        if not IDENTIFIER.fullmatch(args.backup_set_id):
            raise StorageError("BACKUP_ID_INVALID")
        job = operations / "jobs" / args.backup_set_id
        if args.action == "reconcile-job-lock":
            from .backup_operations import reconcile_job_lock
            return reconcile_job_lock(job, expected_lock_sha256=args.expected_lock_sha256,
                                      requested_by=args.requested_by)
        return (request_cancel(job, requested_by=args.requested_by) if args.action == "cancel" else
                resume_cancelled(job, requested_by=args.requested_by) or {"status": "RESUME_ALLOWED"})
    if args.action == "schedule-export":
        xml = windows_task_xml(schedule, python_executable=args.python, config_path=args.config.resolve(),
            working_directory=args.working_directory, start_date=args.start_date,
            acceptance=read_json(args.acceptance) if args.acceptance else None)
        with args.output.open("x", encoding="utf-16") as out:
            out.write(xml)
        return {"status": "XML_EXPORTED_NOT_REGISTERED", "enabled": schedule.enabled, "path": str(args.output)}
    if args.action in {"scheduled", "event"} and not schedule.enabled:
        return {"status": "DISABLED", "reason": "SCHEDULER_DISABLED", "side_effects": False}
    if not cfg.enabled or not policy.enabled:
        return {"status": "DISABLED", "side_effects": False}
    from .kopia_backend import BackendCredentials, KopiaBackend
    workspace = safe_path(operations / "sessions" / uuid.uuid4().hex, file=False)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    backend = KopiaBackend(cfg, BackendCredentials.from_environment(cfg.provider), workspace)
    if args.action == "status":
        return {"environment": backend.probe(), "enabled": policy.enabled,
                "schedule_enabled": schedule.enabled, "jobs": history(operations)}
    if args.action == "repository-create":
        grant = read_json(args.authorization)
        # A scoped external approval is required for local creation too.
        if not grant.get("approved_by") or not grant.get("approval_id"):
            raise StorageError("REPOSITORY_CREATION_AUTHORIZATION_REQUIRED")
        try:
            return backend.create_repository(creation_authorized=True, creation_grant=grant)
        finally:
            backend.disconnect()
    backend.connect(readonly=args.action in {"usage", "plan", "verify", "restore-preview", "restore", "capture-core"})
    try:
        if args.action == "usage":
            return storage_usage_report(operations, backend=backend, policy=policy)
        if args.action == "release-owned-stage":
            from .backup_sets import IDENTIFIER
            if not IDENTIFIER.fullmatch(args.backup_set_id):
                raise StorageError("BACKUP_ID_INVALID")
            return release_owned_stage(operations / "jobs" / args.backup_set_id, backend=backend,
                                       restore_report=read_json(args.restore_report))
        if args.action == "plan":
            request = read_json(args.request)
            plan = make_plan(**request, roots=raw.get("roots", {}), repository=public_backend_identity(backend),
                             max_logical_bytes=policy.max_scratch_bytes)
            write_immutable(args.output, plan)
            return {"status": "PLANNED", "plan_sha256": plan["document_sha256"], "logical_bytes": plan["logical_bytes"]}
        if args.action == "capture-core":
            plan, roots = _capture(raw, backend, args.run_id)
            write_immutable(args.output, {"plan": plan, "roots": roots})
            return {"status": "CORE_CAPTURED", "plan_sha256": plan["document_sha256"], "path": str(args.output)}
        if args.action in {"now", "scheduled", "event"}:
            if args.action == "now":
                value = read_json(args.plan)
                plan = value.get("plan", value)
                roots = value.get("roots", raw.get("roots", {}))
            else:
                # Configured CORE capture is the only automatic selector. No
                # recursive product/archive scans are ever started implicitly.
                if args.action == "event":
                    key = event_key(trigger=args.trigger, event_id=args.event_id,
                                    commit_id=args.commit_id, profile="CORE_BACKUP")
                else:
                    key = event_key(trigger=schedule.trigger, event_id=datetime.now().date().isoformat(),
                                    commit_id="daily-capture", profile="CORE_BACKUP")
                existing = operations / "jobs" / key / "plan.json"
                if existing.exists():
                    plan = read_json(existing)
                    roots = {"core": str(operations / "captures" / key)}
                else:
                    plan, roots = _capture(raw, backend, key)
            grant_path = getattr(args, "cloud_authorization", None) or raw.get("cloud_authorization_path")
            from .backup_sets import digest
            return run_backup(plan, roots, backend=backend, operations_root=operations,
                writer_lock_directory=Path(raw["writer_lock_directory"]), policy=policy,
                cloud_authorization=read_json(grant_path) if grant_path else None,
                operation_configuration_sha256=digest({k: v for k, v in raw.items()
                    if k not in {"cloud_authorization_path", "activation_receipt_path"}}))
        receipt = read_json(args.receipt)
        if args.action == "verify":
            validate_seal(receipt, "BACKUP_SET_V1")
            from .backup_operations import _repository_matches
            _repository_matches(backend, receipt["plan"])
            return backend.verify_snapshot(receipt["snapshot_id"])
        kwargs = dict(backend=backend, target=args.target, policy=policy,
                      protected_roots=[Path(p) for p in raw["protected_roots"]] + [operations])
        if args.action == "restore-preview":
            return restore_preview(receipt, **kwargs)
        return restore_backup(receipt, **kwargs, report_path=args.report)
    finally:
        backend.disconnect()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        category = str(exc) if isinstance(exc, StorageError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", str(exc)) else "BACKUP_OPERATION_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason": category}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
