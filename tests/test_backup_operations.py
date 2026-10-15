"""CI_SAFE: generated temporary data, SQLite fixtures and a fake backend only."""
import copy
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_tracker.operations import backup_sets as bs
from action_tracker.operations import backup_operations as op
from action_tracker.operations.backup_capture import capture_core
from action_tracker.operations.backup_scheduler import SchedulePolicy, event_key, windows_task_xml
from action_tracker.operations.storage_foundation import StorageError, sha256_file, verified_backup
from test_storage_foundation import database, projections


class Backend:
    def __init__(self, root):
        self.root = root
        self.config = SimpleNamespace(provider="filesystem", repository_path=str(root / "repo"),
                                      expected_repository_id="repo-1")
        self.snapshots = []
        self.writes = 0
        self.fail_after_commit = False

    def repository_status(self):
        return {"repository_id": self.config.expected_repository_id}

    def storage_usage(self):
        return {"status": "OBSERVED", "physical_bytes": 123}

    def list_snapshots(self, *, backup_set_id=None):
        return [s for s in self.snapshots if backup_set_id is None or s["backup_set_id"] == backup_set_id]

    def create_snapshot(self, source, *, backup_set_id, upload_limit_bytes, resume_only=False):
        found = self.list_snapshots(backup_set_id=backup_set_id)
        if found:
            return found[0]
        if resume_only:
            raise StorageError("SNAPSHOT_OUTCOME_UNRESOLVED")
        target = self.root / ("snapshot-" + str(len(self.snapshots)))
        shutil.copytree(source, target)
        self.writes += 1
        result = {"snapshot_id": str(len(self.snapshots)), "root_object_id": "root1",
                  "backup_set_id": backup_set_id, "incomplete": False, "saved": str(target)}
        self.snapshots.append(result)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise StorageError("COMMAND_TIMEOUT")
        return result

    def verify_snapshot(self, snapshot_id):
        return {"status": "PASS"}

    def restore_snapshot(self, snapshot_id, target):
        # Match the real adapter's exclusive-target contract: operations must
        # prepare the parent, while the backend alone creates the target.
        assert target.parent.is_dir()
        assert not target.exists()
        snapshot = next(s for s in self.snapshots if s["snapshot_id"] == snapshot_id)
        shutil.copytree(snapshot["saved"], target)
        return {"status": "PASS", "metrics": {"downloaded_bytes": {"status": "UNKNOWN"}}}


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "item.txt").write_text("Spanish source fact", encoding="utf-8")
    backend = Backend(tmp_path)
    roots = {"current": str(source)}
    request = dict(backup_set_id="test-set", run_id="test-run", profile="CURRENT_BUSINESS_BACKUP",
        assets=[{"asset_id": "source-1001", "root_id": "current", "relative_path": "item.txt",
                 "kind": "source_evidence", "sku": "1001", "source_version": "fact-1",
                 "sha256": sha256_file(source / "item.txt")}],
        roots=roots, repository=op.public_backend_identity(backend), config_version="V1",
        recovery_owner="test-owner", retention={"policy_id": "retain-until-review", "recovery_required": True})
    plan = bs.make_plan(**request)
    kwargs = dict(backend=backend, operations_root=tmp_path / "operations",
                  writer_lock_directory=tmp_path / "state", policy=op.OperationPolicy(enabled=True))
    return source, roots, backend, request, plan, kwargs


def test_default_disabled_never_touches_backend(tmp_path):
    assert op.run_backup({}, {}, backend=None, operations_root=tmp_path / "out",
                         writer_lock_directory=tmp_path / "state", policy=op.OperationPolicy())["status"] == "DISABLED"
    assert not (tmp_path / "out").exists()


def test_roundtrip_receipt_and_same_job_idempotence(setup, tmp_path):
    _, roots, backend, _, plan, kwargs = setup
    receipt = op.run_backup(plan, roots, **kwargs)
    assert receipt["verification_status"] == "SNAPSHOT_VERIFIED"
    assert receipt["restore_status"] == "NOT_TESTED"
    assert op.run_backup(plan, roots, **kwargs)["reused"] is True
    assert backend.writes == 1
    restored = op.restore_backup(receipt, backend=backend, target=tmp_path / "another-disk" / "restored",
        policy=kwargs["policy"], protected_roots=[Path(roots["current"])], report_path=tmp_path / "restore-report.json")
    assert restored["status"] == "ROUNDTRIP_PASS"
    assert len(restored["assets"]) == 1
    assert op.history(kwargs["operations_root"])[0]["status"] == "VERIFIED"


def test_unknown_write_outcome_resume_discovers_once(setup):
    _, roots, backend, _, plan, kwargs = setup
    backend.fail_after_commit = True
    with pytest.raises(StorageError, match="COMMAND_TIMEOUT"):
        op.run_backup(plan, roots, **kwargs)
    assert op.run_backup(plan, roots, **kwargs)["snapshot_id"] == "0"
    assert backend.writes == 1


def test_unknown_outcome_without_snapshot_never_blindly_retries(setup):
    _, roots, backend, _, plan, kwargs = setup
    backend.fail_after_commit = True
    with pytest.raises(StorageError): op.run_backup(plan, roots, **kwargs)
    backend.snapshots = []
    with pytest.raises(StorageError, match="OUTCOME_UNRESOLVED"):
        op.run_backup(plan, roots, **kwargs)
    assert backend.writes == 1
    with pytest.raises(StorageError, match="ATTEMPT_BUDGET"):
        op.run_backup(plan, roots, **kwargs)


def test_source_change_and_active_writer_lock_block(setup):
    source, roots, backend, _, plan, kwargs = setup
    kwargs["writer_lock_directory"].mkdir()
    lock = kwargs["writer_lock_directory"] / "daily-run.lock"
    lock.write_text("unknown owner")
    with pytest.raises(StorageError, match="RUN_ALREADY_ACTIVE"):
        op.run_backup(plan, roots, **kwargs)
    assert lock.read_text() == "unknown owner"
    lock.unlink()  # Only the synthetic lock owned by this test.
    (source / "item.txt").write_text("changed fact")
    with pytest.raises(StorageError, match="SOURCE_CHANGED"):
        op.run_backup(plan, roots, **kwargs)
    assert backend.writes == 0


def test_cancel_and_explicit_resume_preserve_evidence(setup):
    _, roots, backend, _, plan, kwargs = setup
    job = kwargs["operations_root"] / "jobs" / plan["backup_set_id"]
    bs.write_immutable(job / "plan.json", plan)
    op.request_cancel(job, requested_by="test-operator")
    with pytest.raises(StorageError, match="CANCELLED"):
        op.run_backup(plan, roots, **kwargs)
    op.resume_cancelled(job, requested_by="test-operator")
    assert list(job.glob("cancel-released-*.json"))
    assert op.run_backup(plan, roots, **kwargs)["snapshot_id"] == "0"


def test_plan_tampering_and_changed_plan_id_are_rejected(setup):
    _, roots, _, _, plan, kwargs = setup
    modified = copy.deepcopy(plan)
    modified["logical_bytes"] = 1
    with pytest.raises(StorageError, match="DIGEST_MISMATCH"):
        op.run_backup(modified, roots, **kwargs)
    op.run_backup(plan, roots, **kwargs)
    modified.pop("document_sha256")
    modified["logical_bytes"] = plan["logical_bytes"]
    modified["recovery_owner"] = "different-owner"
    with pytest.raises(StorageError, match="IMMUTABLE_RECORD_CONFLICT"):
        op.run_backup(bs.seal(modified), roots, **kwargs)


def test_restore_damaged_files_and_existing_destination_block(setup, tmp_path):
    _, roots, backend, _, plan, kwargs = setup
    receipt = op.run_backup(plan, roots, **kwargs)
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(StorageError, match="ALREADY_EXISTS"):
        op.restore_preview(receipt, backend=backend, target=destination, policy=kwargs["policy"], protected_roots=[])
    stored = Path(backend.snapshots[0]["saved"]) / plan["assets"][0]["snapshot_path"]
    stored.write_text("corrupt")
    with pytest.raises(StorageError, match="HASH_MISMATCH"):
        op.restore_backup(receipt, backend=backend, target=tmp_path / "bad-restore", policy=kwargs["policy"],
                          protected_roots=[], report_path=tmp_path / "bad-report.json")
    assert not (tmp_path / "bad-report.json").exists()


def test_budget_failure_is_before_any_snapshot(setup):
    _, roots, backend, _, plan, kwargs = setup
    kwargs["policy"] = replace(kwargs["policy"], max_upload_bytes=1)
    with pytest.raises(StorageError, match="UPLOAD_PLANNING_BUDGET"):
        op.run_backup(plan, roots, **kwargs)
    assert backend.writes == 0


@pytest.mark.parametrize("existing", [False, True])
def test_restore_report_conflict_blocks_before_download(setup, tmp_path, existing):
    _, roots, backend, _, plan, kwargs = setup
    receipt = op.run_backup(plan, roots, **kwargs)
    target = tmp_path / "new-parent" / "restored"
    report = tmp_path / "old-report.json" if existing else target / "report.json"
    if existing:
        report.write_text("prior evidence")
    with pytest.raises(StorageError, match="RESTORE_REPORT_.*"):
        op.restore_backup(receipt, backend=backend, target=target, policy=kwargs["policy"],
                          protected_roots=[], report_path=report)
    assert not target.parent.exists()
    if existing:
        assert report.read_text() == "prior evidence"


@pytest.mark.parametrize("path", ["../a", "C:/a", "a\\b", "/a", "a:stream", "a/CON.txt", "a./x"])
def test_relative_paths_reject_windows_and_posix_escapes(path):
    with pytest.raises(StorageError): bs.relative_path(path)


def test_links_duplicates_and_source_version_required(setup):
    source, _, _, request, _, _ = setup
    request["assets"][0]["source_version"] = None
    with pytest.raises(StorageError, match="SOURCE_BINDING"):
        bs.make_plan(**request)
    request["assets"][0]["source_version"] = "fact-1"
    request["assets"] *= 2
    with pytest.raises(StorageError, match="DUPLICATE"):
        bs.make_plan(**request)
    request["assets"] = request["assets"][:1]
    os.link(source / "item.txt", source / "alias.txt")
    with pytest.raises(StorageError, match="HARDLINK"):
        bs.make_plan(**request)


def test_core_capture_sqlite_projections_and_restore(tmp_path):
    src = database(tmp_path / "source" / "primary.db")
    projection = projections(src, tmp_path / "projections")
    backend = Backend(tmp_path)
    plan, roots = capture_core(source=src, projections=projection, destination=tmp_path / "captured",
        writer_lock_directory=tmp_path / "state", run_id="core-1", code_version="fixture-code-version",
        repository=op.public_backend_identity(backend), config_version="V1", recovery_owner="fixture-owner",
        retention={"policy_id": "keep-fixture"})
    assert plan["database_identities"][0]["commit_id"] == "head1"
    receipt = op.run_backup(plan, roots, backend=backend, operations_root=tmp_path / "operations",
        writer_lock_directory=tmp_path / "state", policy=op.OperationPolicy(enabled=True))
    result = op.restore_backup(receipt, backend=backend, target=tmp_path / "restored", policy=op.OperationPolicy(enabled=True),
        protected_roots=[src.parent], report_path=tmp_path / "restored.json")
    db = next(a for a in result["assets"] if "database" in a)
    assert db["database"]["integrity"] == db["database"]["foreign_keys"] == "PASS"
    assert db["database"]["export_sync"]["status"] == "SUCCESS"


def test_cloud_business_upload_requires_exact_plan_authorization(setup):
    _, roots, backend, request, _, kwargs = setup
    backend.config.provider = "oss"
    request["repository"] = op.public_backend_identity(backend)
    plan = bs.make_plan(**request)
    with pytest.raises(StorageError, match="EXACT_CLOUD_TRANSFER_BUDGET_UNAVAILABLE"):
        op.run_backup(plan, roots, **kwargs)
    kwargs["policy"] = replace(kwargs["policy"], allow_estimated_cloud_budget=True)
    with pytest.raises(StorageError, match="BUSINESS_CLOUD_UPLOAD_NOT_AUTHORIZED"):
        op.run_backup(plan, roots, **kwargs)
    assert backend.writes == 0


def test_schedule_default_xml_disabled_and_event_identity(tmp_path):
    assert event_key(trigger="task_completed", event_id="run1", commit_id="head1", profile="CORE") == event_key(
        trigger="task_completed", event_id="run1", commit_id="head1", profile="CORE")
    kwargs = dict(python_executable=tmp_path / "python.exe", config_path=tmp_path / "settings.json",
                  working_directory=tmp_path, start_date="2026-10-15")
    xml = windows_task_xml(SchedulePolicy(trigger="daily"), **kwargs)
    assert "<Enabled>false</Enabled>" in xml
    assert "IgnoreNew" in xml
    with pytest.raises(StorageError, match="ACCEPTANCE_REQUIRED"):
        windows_task_xml(SchedulePolicy(enabled=True, trigger="daily"), **kwargs)


def test_retention_does_not_expire_unknown_responsibility(setup):
    _, roots, _, _, plan, kwargs = setup
    receipt = op.run_backup(plan, roots, **kwargs)
    result = op.retention_preview([receipt])
    assert result["snapshots"][0]["status"] == "RETAIN"
    assert result["remote_mutations"] == 0


def test_p1_image_index_and_portable_manifest_restore(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    image = source / "master.png"
    image.write_bytes(b"synthetic image bytes")
    record = {"asset_id": "sha256:" + sha256_file(image), "asset_type": "IMAGE_MASTER", "root_id": "data",
              "relative_path": "master.png", "sha256": sha256_file(image), "sku": "1001",
              "source_version": "official-version-1", "qa_status": "PASS"}
    assets = bs.select_images({"contract": "ASSET_INDEX_V1", "assets": [record]}, skus=["1001"])
    manifest = source / "bindings.json"
    manifest.write_text(json.dumps(bs.image_bindings(assets)), encoding="utf-8")
    assets.append({"asset_id": "image-bindings", "root_id": "data", "relative_path": "bindings.json",
                   "sha256": sha256_file(manifest), "kind": "image_manifest"})
    backend = Backend(tmp_path)
    roots = {"data": str(source)}
    plan = bs.make_plan(backup_set_id="images", run_id="images", profile="CURRENT_BUSINESS_BACKUP",
        roots=roots, assets=assets, repository=op.public_backend_identity(backend), config_version="V1",
        recovery_owner="test", retention={"policy_id": "retain"})
    bs.stage_plan(plan, roots, tmp_path / "stage", max_scratch_bytes=1024**2)
    assert bs.verify_restored(plan, tmp_path / "stage")["status"] == "ROUNDTRIP_PASS"
    manifest.write_text('{"contract":"IMAGE_ASSET_BINDINGS_V1","assets":[]}', encoding="utf-8")
    assets[-1]["sha256"] = sha256_file(manifest)
    with pytest.raises(StorageError, match="IMAGE_MANIFEST_BINDING_MISMATCH"):
        bs.make_plan(backup_set_id="images2", run_id="images2", profile="CURRENT_BUSINESS_BACKUP",
            roots=roots, assets=assets, repository=op.public_backend_identity(backend), config_version="V1",
            recovery_owner="test", retention={"policy_id": "retain"})


def test_image_selection_never_guesses_missing_or_ambiguous_sku():
    with pytest.raises(StorageError, match="UNAVAILABLE_OR_AMBIGUOUS"):
        bs.select_images({"contract": "ASSET_INDEX_V1", "assets": []}, skus=["1001"])


def test_recurring_cloud_grant_binds_actual_source_roots(setup, tmp_path):
    source, roots, backend, request, _, kwargs = setup
    backend.config.provider = "oss"
    request["repository"] = op.public_backend_identity(backend)
    plan = bs.make_plan(**request)
    policy = replace(kwargs["policy"], cloud_business_upload_authorized=True, allow_estimated_cloud_budget=True)
    from datetime import datetime, timedelta, timezone
    scope = op.cloud_selection_scope(plan, roots, kwargs["operations_root"])
    grant = {"contract": "CLOUD_BACKUP_AUTHORIZATION_V1", "authorization_scope": "FIRST_AND_RECURRING_BUSINESS_BACKUP",
             "approved_by": "fixture-operator", "approval_id": "synthetic-approval", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
             "repository_id": "repo-1", "config_sha256": plan["repository"]["config_sha256"], "profile": plan["profile"],
             "source_root_ids": plan["source_roots"], "source_selection_sha256": scope,
             "operation_configuration_sha256": "a" * 64, "allowed_asset_kinds": ["source_evidence"],
             "max_logical_bytes": 10000, "instruction_reference": "SYNTHETIC_TEST_ONLY"}
    op._authorize_cloud(grant, plan, policy, scope, "a" * 64)
    other = tmp_path / "different-source"
    other.mkdir()
    other_scope = op.cloud_selection_scope(plan, {"current": str(other)}, kwargs["operations_root"])
    with pytest.raises(StorageError, match="NOT_AUTHORIZED"):
        op._authorize_cloud(grant, plan, policy, other_scope, "a" * 64)
    with pytest.raises(StorageError, match="NOT_AUTHORIZED"):
        op._authorize_cloud(grant, plan, policy, scope, "b" * 64)


def test_synthetic_cloud_flag_cannot_upload_business_bytes(setup):
    _, roots, backend, request, _, kwargs = setup
    backend.config.provider = "oss"
    backend.config.purpose = "synthetic_test"
    request["repository"] = op.public_backend_identity(backend)
    plan = bs.make_plan(**request)
    kwargs["policy"] = replace(kwargs["policy"], allow_estimated_cloud_budget=True)
    with pytest.raises(StorageError, match="REJECTS_BUSINESS_DATA"):
        op.run_backup(plan, roots, **kwargs)
    with pytest.raises(StorageError, match="SYNTHETIC_RECIPE_INVALID"):
        op.run_backup(plan, roots, synthetic_only=True, **kwargs)
    assert backend.writes == 0


def test_dead_job_lock_recovery_is_explicit_and_never_reclaims_live_owner(setup, monkeypatch):
    _, roots, backend, _, plan, kwargs = setup
    job = kwargs["operations_root"] / "jobs" / plan["backup_set_id"]
    bs.write_immutable(job / "plan.json", plan)
    lock = job / "daily-run.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "run_id": plan["run_id"], "command": "storage-baseline",
                                "storage_token": "a" * 32}), encoding="utf-8")
    lock_hash = sha256_file(lock)
    with pytest.raises(StorageError, match="STILL_ACTIVE"):
        op.reconcile_job_lock(job, expected_lock_sha256=lock_hash, requested_by="test")
    assert lock.exists()
    monkeypatch.setattr(op, "_process_alive", lambda pid: False)
    with pytest.raises(StorageError, match="OWNER_MISMATCH"):
        op.reconcile_job_lock(job, expected_lock_sha256="b" * 64, requested_by="test")
    assert op.reconcile_job_lock(job, expected_lock_sha256=lock_hash, requested_by="test")["writer_lock_changed"] is False
    assert not lock.exists()
    assert op.run_backup(plan, roots, **kwargs)["snapshot_id"] == "0"


def test_retention_review_uses_separate_restore_and_release_evidence(setup, tmp_path):
    _, roots, backend, request, plan, kwargs = setup
    first = op.run_backup(plan, roots, **kwargs)
    request.update(backup_set_id="next-set", run_id="next-run")
    second_plan = bs.make_plan(**request)
    second = op.run_backup(second_plan, roots, **kwargs)
    restored = op.restore_backup(first, backend=backend, target=tmp_path / "retention-restore", policy=kwargs["policy"],
                                  protected_roots=[], report_path=tmp_path / "retention-restore.json")
    from datetime import datetime, timedelta, timezone
    decision = bs.seal({"contract": "SNAPSHOT_RETENTION_DECISION_V1", "receipt_sha256": first["document_sha256"],
        "restore_report_sha256": restored["document_sha256"], "policy_id": first["retention"]["policy_id"],
        "approved_by": "synthetic-test-operator", "approval_id": "synthetic-retention", "recovery_required": False,
        "reference_status": "NO_REFERENCES", "release_after": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    assert {r["status"] for r in op.retention_preview([first, second], daily=1, weekly=1, monthly=1)["snapshots"]} == {"RETAIN"}
    rows = op.retention_preview([first, second], daily=1, weekly=1, monthly=1,
                               restore_reports=[restored], release_decisions=[decision])["snapshots"]
    assert rows[0]["status"] == "RETAIN"
    assert rows[1]["status"] == "EXPIRATION_REVIEW_REQUIRED"
    assert not any(r["delete_allowed"] or r["maintenance_allowed"] for r in rows)
