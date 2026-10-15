"""LOCAL_ONLY. Real Kopia acceptance in a new isolated Windows directory.

Never opens PRIMARY. Optional real-data input must be an existing P1 backup
sidecar plus matching P2 candidate projections. No network calls or cloud data.
The generated repository password is protected with Windows DPAPI, not logged.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import replace
from pathlib import Path

from action_tracker.operations.backup_sets import (make_plan, read_json, seal, sha256_file,
                                                  verify_restored, write_immutable)
from action_tracker.operations.backup_operations import (OperationPolicy, public_backend_identity,
    run_backup, restore_backup, storage_usage_report, release_owned_stage)
from action_tracker.operations.kopia_backend import BackendConfig, BackendCredentials, KopiaBackend
from action_tracker.operations.kopia_connectivity import _private_workspace
from action_tracker.operations.storage_foundation import StorageError


def dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise StorageError("WINDOWS_DPAPI_REQUIRED_FOR_THIS_ACCEPTANCE_SCRIPT")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    original = Blob(len(value), buffer)
    encrypted = Blob()
    protect = ctypes.windll.crypt32.CryptProtectData
    protect.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    protect.restype = wintypes.BOOL
    if not protect(ctypes.byref(original), "Action Storage V3 isolated acceptance", None,
                   None, None, 1, ctypes.byref(encrypted)):
        raise StorageError("DPAPI_PROTECTION_FAILED")
    try:
        return ctypes.string_at(encrypted.data, encrypted.size)
    finally:
        ctypes.windll.kernel32.LocalFree(encrypted.data)


def dpapi_unprotect(value: bytes) -> bytes:
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    encrypted, plain = Blob(len(value), buffer), Blob()
    unprotect = ctypes.windll.crypt32.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    unprotect.restype = wintypes.BOOL
    if not unprotect(ctypes.byref(encrypted), None, None, None, None, 1, ctypes.byref(plain)):
        raise StorageError("DPAPI_UNPROTECTION_FAILED")
    try:
        return ctypes.string_at(plain.data, plain.size)
    finally:
        ctypes.windll.kernel32.LocalFree(plain.data)


def verify_existing(root, cli_path, code_head):
    identity = read_json(root / "REPOSITORY_IDENTITY.json")
    if not code_head or len(code_head) != 40:
        raise StorageError("EXACT_CODE_HEAD_REQUIRED")
    password = dpapi_unprotect((root / "private-credentials/repository-password.dpapi").read_bytes()).decode()
    config = BackendConfig(enabled=True, repository_path=identity["repository_path"], cli_path=str(cli_path),
                           expected_repository_id=identity["repository_id"], timeout_seconds=600)
    backend = KopiaBackend(config, BackendCredentials(password=password), root / "sessions" / ("final-" + uuid.uuid4().hex))
    backend.connect(readonly=True)
    results = []
    try:
        for job_id, target in (("synthetic-v1", "restored-v1"), ("synthetic-v2", "restored-v2"),
                               ("real-p1-core", "real-core-restored")):
            job = root / "operations/jobs" / job_id
            if not job.exists():
                continue
            receipt = read_json(job / "receipt.json")
            verification = backend.verify_snapshot(receipt["snapshot_id"])
            restored = verify_restored(receipt["plan"], root / target)
            results.append({"backup_set_id": job_id, "snapshot": verification, "restored": restored})
        usage = backend.storage_usage()
    finally:
        backend.disconnect()
    write_immutable(root / ("HEAD_REVALIDATION_" + code_head + ".json"),
                    {"status": "PASS", "code_head": code_head, "results": results,
                     "usage": usage, "new_database_copies": 0, "cloud_operations": 0})
    print(json.dumps({"status": "PASS", "code_head": code_head, "revalidated_sets": len(results),
                      "new_database_copies": 0}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kopia", type=Path, required=True)
    parser.add_argument("--real-p1-manifest", type=Path)
    parser.add_argument("--projection-root", type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--code-head")
    args = parser.parse_args()
    root = args.output.resolve()
    if args.verify_existing:
        return verify_existing(root, args.kopia, args.code_head)
    if root.exists():
        raise StorageError("ACCEPTANCE_ROOT_MUST_BE_NEW")
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    (root / "sessions").mkdir()
    private = root / "private-credentials"
    _private_workspace(private)
    password = secrets.token_urlsafe(40)
    (private / "repository-password.dpapi").write_bytes(dpapi_protect(password.encode()))
    credentials = BackendCredentials(password=password)
    config = BackendConfig(enabled=True, repository_path=str(root / "repository"), cli_path=str(args.kopia),
                           timeout_seconds=600, max_parallel=1)
    backend = KopiaBackend(config, credentials, root / "sessions" / "initial")
    environment = backend.probe()
    write_immutable(root / "ENVIRONMENT.json", environment)
    created = backend.create_repository(creation_authorized=True)
    config = replace(config, expected_repository_id=created["repository_id"])
    backend.config = config
    write_immutable(root / "REPOSITORY_IDENTITY.json", {"repository_id": created["repository_id"],
        "provider": "filesystem", "repository_path": str(root / "repository"), "cli_path": str(args.kopia),
        "password_storage": "WINDOWS_DPAPI_CURRENT_USER", "production": False})
    policy = OperationPolicy(enabled=True, max_upload_bytes=8 * 1024**3, max_restore_bytes=8 * 1024**3,
                             max_scratch_bytes=12 * 1024**3, max_runtime_seconds=1800)
    source = root / "synthetic-source"
    source.mkdir()
    block = secrets.token_bytes(128 * 1024)
    (source / "a.bin").write_bytes(block * 8)
    (source / "b.bin").write_bytes(block * 8)
    (source / "note.txt").write_text("Artificial acceptance files only. Version one.\n", encoding="utf-8")
    def synthetic_plan(version):
        return make_plan(backup_set_id="synthetic-v" + str(version), run_id="synthetic-v" + str(version),
            profile="CURRENT_BUSINESS_BACKUP", roots={"synthetic": str(source)},
            assets=[{"asset_id": p.name.replace(".", "-"), "root_id": "synthetic", "relative_path": p.name,
                     "kind": "run_manifest", "sha256": sha256_file(p)} for p in sorted(source.iterdir())],
            repository=public_backend_identity(backend), config_version="STORAGE_V3_ACCEPTANCE_V1",
            recovery_owner="Storage V3 synthetic acceptance", retention={"policy_id": "retain-test-evidence", "recovery_required": True})
    common = dict(backend=backend, operations_root=root / "operations", writer_lock_directory=root / "isolated-writer-lock", policy=policy)
    plan1 = synthetic_plan(1)
    receipt1 = run_backup(plan1, {"synthetic": str(source)}, **common)
    repeated = run_backup(plan1, {"synthetic": str(source)}, **common)
    assert repeated["reused"] is True and repeated["snapshot_id"] == receipt1["snapshot_id"]
    usage1 = backend.storage_usage()
    (source / "note.txt").write_text("Artificial acceptance files only. Version two.\n", encoding="utf-8")
    plan2 = synthetic_plan(2)
    receipt2 = run_backup(plan2, {"synthetic": str(source)}, **common)
    usage2 = backend.storage_usage()
    assert receipt2["snapshot_id"] != receipt1["snapshot_id"]
    backend.disconnect()
    wrong = KopiaBackend(config, BackendCredentials(password="incorrect-" + secrets.token_hex(12)), root / "sessions" / "wrong-password")
    try:
        wrong.connect(readonly=True)
        raise AssertionError("wrong password accepted")
    except StorageError as error:
        wrong_password = {"status": "PASS", "observed_error": str(error)}
    reader = KopiaBackend(config, credentials, root / "sessions" / "reconnected")
    reader.connect(readonly=True)
    restore1 = restore_backup(receipt1, backend=reader, target=root / "restored-v1", policy=policy,
                             protected_roots=[source], report_path=root / "RESTORE_V1.json")
    restore2 = restore_backup(receipt2, backend=reader, target=root / "restored-v2", policy=policy,
                             protected_roots=[source], report_path=root / "RESTORE_V2.json")
    assert (root / "restored-v1/assets/synthetic/note.txt").read_bytes() != (root / "restored-v2/assets/synthetic/note.txt").read_bytes()
    try:
        bad_plan = {**plan2, "backup_set_id": "budget-block", "run_id": "budget-block"}
        bad_plan.pop("document_sha256")
        run_backup(seal(bad_plan), {"synthetic": str(source)}, backend=reader,
                   operations_root=root / "budget-operations", writer_lock_directory=root / "isolated-writer-lock",
                   policy=replace(policy, max_upload_bytes=1))
        raise AssertionError("budget bypass")
    except StorageError as error:
        budget = {"status": "PASS", "observed_error": str(error), "kind": "REAL_PREFLIGHT_CONFIGURED_LIMIT_NOT_FILLED_DISK"}
    missing = source / "missing.txt"
    try:
        make_plan(backup_set_id="missing", run_id="missing", profile="CURRENT_BUSINESS_BACKUP",
            roots={"synthetic": str(source)}, assets=[{"asset_id": "missing", "root_id": "synthetic", "relative_path": missing.name,
            "kind": "run_manifest", "sha256": "0" * 64}], repository=public_backend_identity(reader),
            config_version="V1", recovery_owner="test", retention={"policy_id": "test"})
        raise AssertionError("missing asset accepted")
    except StorageError as error:
        missing_result = {"status": "PASS", "observed_error": str(error)}
    logical_versions = usage2["logical_bytes"]
    physical = usage2["repository_bytes"]["value"]
    gate2 = {"status": "PASS", "data_classification": "ARTIFICIAL_LOCAL_ONLY", "environment": environment,
             "repository_id": config.expected_repository_id, "snapshot_ids": [receipt1["snapshot_id"], receipt2["snapshot_id"]],
             "roundtrip_versions": [restore1["status"], restore2["status"]], "resume": "SAME_SNAPSHOT_NO_DUPLICATE",
             "wrong_password": wrong_password, "budget": budget, "missing_file": missing_result,
             "usage_after_v1": usage1, "usage_after_v2": usage2,
             "dedup_compression_estimate": {"status": "ESTIMATED", "logical_version_bytes": logical_versions,
                 "observed_repository_bytes": physical, "logical_minus_repository_bytes": logical_versions - physical,
                 "note": "Combined deduplication/compression minus repository overhead; not pure dedup or billing."},
             "new_bytes_for_v2": {"status": "OBSERVED_NET_REPOSITORY_GROWTH",
                 "value": physical - usage1["repository_bytes"]["value"]}, "cloud_operations": 0}
    write_immutable(root / "GATE2_LOCAL_KOPIA.json", gate2)
    print(json.dumps({"gate": 2, "status": "PASS", "logical_version_bytes": logical_versions, "repository_bytes": physical}), flush=True)
    reader.disconnect()
    if args.real_p1_manifest:
        if not args.projection_root:
            raise StorageError("REAL_PROJECTION_ROOT_REQUIRED")
        p1 = read_json(args.real_p1_manifest)
        p1dir = args.real_p1_manifest.resolve().parent
        dbpath = Path(p1["backup_path"])
        assert dbpath.parent == p1dir
        real_backend = KopiaBackend(config, credentials, root / "sessions" / "real-database")
        real_backend.connect()
        assets = [{"asset_id": "real-p1", "root_id": "p1", "relative_path": dbpath.name,
                   "kind": "verified_database", "sha256": p1["sha256"]}]
        for key, rel in (("master", "master/Action_Master.xlsx"), ("known", "state/known_skus.csv"), ("offline", "state/offline_skus.csv")):
            assets.append({"asset_id": key, "root_id": "projections", "relative_path": rel,
                           "kind": "projection", "projection_name": key, "sha256": sha256_file(args.projection_root / rel)})
        for name, kind in (("baseline_context.json", "run_manifest"), ("restore_validation.json", "recovery_record")):
            assets.append({"asset_id": name.replace(".", "-"), "root_id": "p1", "relative_path": name,
                           "kind": kind, "sha256": sha256_file(p1dir / name)})
        roots = {"p1": str(p1dir), "projections": str(args.projection_root.resolve())}
        plan = make_plan(backup_set_id="real-p1-core", run_id="real-p1-core", profile="CORE_BACKUP", roots=roots,
            assets=assets, repository=public_backend_identity(real_backend), config_version="STORAGE_V3_ACCEPTANCE_V1",
            recovery_owner="Storage V3 local acceptance; retains existing P1 ownership",
            retention={"policy_id": "retain-original-p1-obligations", "recovery_required": True})
        receipt = run_backup(plan, roots, backend=real_backend, operations_root=root / "operations",
            writer_lock_directory=root / "isolated-writer-lock", policy=policy)
        real_backend.disconnect()
        real_reader = KopiaBackend(config, credentials, root / "sessions" / "real-reconnected")
        real_reader.connect(readonly=True)
        restored = restore_backup(receipt, backend=real_reader, target=root / "real-core-restored", policy=policy,
            protected_roots=[p1dir, args.projection_root], report_path=root / "GATE4_REAL_SQLITE_RESTORE.json")
        assert restored["status"] == "ROUNDTRIP_PASS"
        usage = storage_usage_report(root / "operations", backend=real_reader, policy=policy)
        write_immutable(root / "FINAL_STORAGE_USAGE.json", usage)
        released = release_owned_stage(root / "operations/jobs/real-p1-core", backend=real_reader, restore_report=restored)
        write_immutable(root / "OWNED_STAGE_RELEASE.json", released)
        real_reader.disconnect()
        print(json.dumps({"gate": 4, "status": restored["status"], "logical_bytes": restored["logical_bytes"],
                          "commit_id": p1["commit_id"], "cloud_operations": 0}), flush=True)
    write_immutable(root / "LOCAL_ACCEPTANCE_COMPLETE.json", {"status": "PASS", "gate2": "PASS",
                    "gate4": "PASS" if args.real_p1_manifest else "NOT_REQUESTED", "production_writes": 0,
                    "new_cloud_test": "BLOCKED_BY_USER_NO_CREDENTIAL_REFRESH", "historical_assets_deleted": 0})


if __name__ == "__main__":
    main()
