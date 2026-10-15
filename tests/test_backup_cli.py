"""CI_SAFE: synthetic configs, fake backend, no cloud, CLI subprocess or real DB."""
import json
from pathlib import Path

import pytest

from action_tracker.operations import backup_cli as cli
from action_tracker.operations import backup_capture as capture
from action_tracker.operations import kopia_backend as kopia
from action_tracker.operations.backup_sets import digest, seal
from action_tracker.operations.storage_foundation import StorageError


RID = "a" * 64


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf8")
    return path


@pytest.fixture
def configuration(tmp_path):
    raw = {"contract": "BACKUP_OPERATIONS_CONFIG_V1", "backend": {}, "policy": {},
           "operations_root": str(tmp_path / "operations"),
           "writer_lock_directory": str(tmp_path / "writer-state"),
           "protected_roots": [str(tmp_path / "production")],
           "roots": {"business": str(tmp_path / "source")}}
    path = save(tmp_path / "config.json", raw)
    return raw, path


def enabled(raw, path, tmp_path):
    raw["backend"] = {"enabled": True, "provider": "filesystem", "repository_path": str(tmp_path / "repository"),
                      "expected_repository_id": RID}
    raw["policy"] = {"enabled": True}
    save(path, raw)


def activate(raw, path, tmp_path, *, trigger="daily"):
    raw["schedule"] = {"enabled": True, "trigger": trigger}
    report = seal({"contract": "BACKUP_RESTORE_V1", "status": "ROUNDTRIP_PASS", "test_fixture": True})
    report_path = save(tmp_path / "synthetic-restore-acceptance.json", report)
    acceptance_path = tmp_path / "synthetic-activation.json"
    raw["activation_receipt_path"] = str(acceptance_path)
    acceptance = {"contract": "BACKUP_ACTIVATION_V1", "production_enable_authorized": True,
        "approved_by": "synthetic-test-owner", "approval_id": "synthetic-approval",
        "configuration_sha256": digest({k: v for k, v in raw.items() if k != "activation_receipt_path"}),
        "restore_report_sha256": report["document_sha256"], "restore_report_path": str(report_path)}
    save(acceptance_path, acceptance)
    save(path, raw)
    return acceptance, acceptance_path


@pytest.fixture
def fake_backend(monkeypatch):
    instances = []
    class FakeBackend:
        def __init__(self, config, credentials, workspace):
            self.config, self.workspace = config, workspace
            self.calls = []
            instances.append(self)
        def probe(self):
            self.calls.append(("probe",))
            return {"status": "ENVIRONMENT_READY", "version": "synthetic"}
        def connect(self, *, readonly=False):
            self.calls.append(("connect", readonly))
            return {"status": "REPOSITORY_CONNECTED", "repository_id": RID}
        def disconnect(self):
            self.calls.append(("disconnect",))
            return {"status": "DISCONNECTED"}
        def create_repository(self, **kwargs):
            self.calls.append(("create", kwargs))
            return {"status": "REPOSITORY_CONNECTED", "repository_id": RID}
        def repository_status(self):
            self.calls.append(("status",))
            return {"repository_id": RID}
        def verify_snapshot(self, snapshot_id):
            self.calls.append(("verify", snapshot_id))
            return {"status": "SNAPSHOT_VERIFIED"}
    monkeypatch.setattr(kopia, "KopiaBackend", FakeBackend)
    return instances


def args(path, *argv):
    return cli.parser().parse_args(["--config", str(path), *map(str, argv)])


def test_default_configuration_is_disabled(configuration):
    _, path = configuration
    _, backend, policy, schedule = cli.load_config(path)
    assert backend.enabled is False
    assert policy.enabled is False
    assert schedule.enabled is False


@pytest.mark.parametrize("action", ["status", "scheduled", "event"])
def test_disabled_paths_do_not_construct_backend_or_directories(configuration, fake_backend, action):
    raw, path = configuration
    extra = ["--trigger", "task_completed", "--event-id", "event1", "--commit-id", "commit1"] if action == "event" else []
    result = cli.execute(args(path, action, *extra))
    assert result["status"] == "DISABLED" and result["side_effects"] is False
    assert fake_backend == []
    assert not Path(raw["operations_root"]).exists()


def test_disabled_schedule_does_not_capture_when_manual_backend_enabled(configuration, fake_backend, tmp_path):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    assert cli.execute(args(path, "scheduled"))["reason"] == "SCHEDULER_DISABLED"
    assert fake_backend == []
    assert not Path(raw["operations_root"]).exists()


@pytest.mark.parametrize("field", ["password", "access_key", "secret_key", "session_token", "env"])
def test_config_rejects_credentials_in_backend(configuration, field):
    raw, path = configuration
    raw["backend"][field] = "synthetic-secret"
    save(path, raw)
    with pytest.raises(StorageError, match="BACKEND_CONFIG_UNKNOWN_FIELDS"):
        cli.load_config(path)


def test_config_unknown_top_level_and_error_redaction(configuration, capsys):
    raw, path = configuration
    raw["password"] = "synthetic-secret"
    save(path, raw)
    assert cli.main(["--config", str(path), "status"]) == 2
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    assert json.loads(output)["reason"] == "BACKUP_CONFIG_INVALID_OR_UNKNOWN_FIELDS"


@pytest.mark.parametrize("path_field", ["operations_root", "writer_lock_directory"])
def test_relative_operation_roots_rejected(configuration, path_field):
    raw, path = configuration
    raw[path_field] = "relative/path"
    save(path, raw)
    with pytest.raises(StorageError, match="ABSOLUTE_ROOT_REQUIRED"):
        cli.load_config(path)


def test_missing_production_protection_rejected(configuration):
    raw, path = configuration
    raw["protected_roots"] = []
    save(path, raw)
    with pytest.raises(StorageError, match="PRODUCTION_PROTECTION_ROOTS_REQUIRED"):
        cli.load_config(path)


@pytest.mark.parametrize("relation", ["child", "parent", "same"])
def test_operations_root_overlap_protection(configuration, relation):
    raw, path = configuration
    protected = Path(raw["protected_roots"][0])
    raw["operations_root"] = str({"child": protected / "new-backups", "parent": protected.parent,
                                   "same": protected}[relation])
    save(path, raw)
    with pytest.raises(StorageError, match="OPERATIONS_PROTECTED_ROOT_OVERLAP"):
        cli.load_config(path)


def test_repository_cannot_be_created_inside_protected_root(configuration, tmp_path):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    raw["backend"]["repository_path"] = str(Path(raw["protected_roots"][0]) / "new-repository")
    save(path, raw)
    with pytest.raises(StorageError, match="REPOSITORY_PROTECTED_ROOT_OVERLAP"):
        cli.load_config(path)


@pytest.mark.parametrize("argv", [["status"], ["history"], ["usage"], ["scheduled"],
    ["repository-create", "--authorization", "grant.json"],
    ["plan", "--request", "request.json", "--output", "plan.json"],
    ["capture-core", "--run-id", "run1", "--output", "plan.json"],
    ["now", "--plan", "plan.json"], ["verify", "--receipt", "receipt.json"],
    ["restore-preview", "--receipt", "receipt.json", "--target", "target"],
    ["restore", "--receipt", "receipt.json", "--target", "target", "--report", "report.json"],
    ["retention-preview", "--receipt", "receipt.json"],
    ["cancel", "--backup-set-id", "set1", "--requested-by", "owner"],
    ["resume-cancelled", "--backup-set-id", "set1", "--requested-by", "owner"],
    ["event", "--trigger", "task_completed", "--event-id", "task1", "--commit-id", "commit1"]])
def test_parser_accepts_supported_operations(configuration, argv):
    assert args(configuration[1], *argv).action == argv[0]


def test_parser_rejects_unknown_operation(configuration):
    with pytest.raises(SystemExit) as exc:
        args(configuration[1], "delete-all")
    assert exc.value.code == 2


def test_capture_core_routes_owned_destination_and_disconnects(configuration, fake_backend, tmp_path, monkeypatch):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    raw["capture"] = {"source": str(tmp_path / "verified-source.sqlite"),
        "projections": {name: str(tmp_path / (name + ".projection")) for name in ("master", "known", "offline")},
        "code_version": "test-code", "recovery_owner": "test-owner",
        "retention": {"policy_id": "test-retain", "recovery_required": True}}
    Path(raw["capture"]["source"]).write_bytes(b"synthetic size-preflight fixture")
    for projection in raw["capture"]["projections"].values():
        Path(projection).write_bytes(b"synthetic projection fixture")
    save(path, raw)
    captured = []
    def fake_capture(**kwargs):
        captured.append(kwargs)
        return {"document_sha256": "test-plan-sha"}, {"core": str(kwargs["destination"])}
    monkeypatch.setattr(capture, "capture_core", fake_capture)
    output = tmp_path / "capture-plan.json"
    result = cli.execute(args(path, "capture-core", "--run-id", "owned-run", "--output", output))
    assert result["status"] == "CORE_CAPTURED"
    assert captured[0]["destination"] == Path(raw["operations_root"]) / "captures" / "owned-run"
    assert captured[0]["writer_lock_directory"] == Path(raw["writer_lock_directory"])
    assert captured[0]["repository"]["repository_id"] == RID
    assert fake_backend[0].calls == [("connect", True), ("disconnect",)]
    assert json.loads(output.read_text())["roots"]["core"].endswith("owned-run")


def test_capture_requires_complete_owned_configuration(configuration, fake_backend, tmp_path):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    with pytest.raises(StorageError, match="CORE_CAPTURE_CONFIGURATION_REQUIRED"):
        cli.execute(args(path, "capture-core", "--run-id", "run1", "--output", tmp_path / "out.json"))
    assert fake_backend[0].calls[-1] == ("disconnect",)


def test_now_routes_explicit_plan_roots_without_implicit_capture(configuration, fake_backend, tmp_path, monkeypatch):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    plan = {"backup_set_id": "set-1"}
    roots = {"core": str(tmp_path / "owned-capture")}
    plan_path = save(tmp_path / "plan.json", {"plan": plan, "roots": roots})
    calls = []
    def run(p, r, **kwargs):
        calls.append((p, r, kwargs))
        return {"status": "SYNTHETIC_ROUTE_PASS"}
    monkeypatch.setattr(cli, "run_backup", run)
    monkeypatch.setattr(cli, "_capture", lambda *a, **k: pytest.fail("Implicit capture"))
    assert cli.execute(args(path, "now", "--plan", plan_path))["status"] == "SYNTHETIC_ROUTE_PASS"
    assert calls[0][0] == plan and calls[0][1] == roots
    assert calls[0][2]["operations_root"] == Path(raw["operations_root"])
    assert fake_backend[0].calls == [("connect", False), ("disconnect",)]


def test_stage_failure_still_disconnects_and_redacts(configuration, fake_backend, tmp_path, monkeypatch, capsys):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    plan = save(tmp_path / "plan.json", {})
    def fail(*a, **k):
        raise RuntimeError("synthetic-private-credential")
    monkeypatch.setattr(cli, "run_backup", fail)
    assert cli.main(["--config", str(path), "now", "--plan", str(plan)]) == 2
    assert fake_backend[0].calls[-1] == ("disconnect",)
    output = capsys.readouterr().out
    assert "synthetic-private-credential" not in output
    assert json.loads(output)["reason"] == "BACKUP_OPERATION_FAILED"


def test_repository_creation_disconnects_its_private_session(configuration, fake_backend, tmp_path):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    raw["backend"]["expected_repository_id"] = ""
    save(path, raw)
    grant = save(tmp_path / "grant.json", {"approved_by": "test-owner", "approval_id": "test-grant"})
    assert cli.execute(args(path, "repository-create", "--authorization", grant))["repository_id"] == RID
    assert fake_backend[0].calls[-1] == ("disconnect",)


def test_enabled_scheduler_without_activation_is_blocked(configuration, fake_backend, tmp_path, capsys):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    raw["schedule"] = {"enabled": True, "trigger": "daily"}
    save(path, raw)
    assert cli.main(["--config", str(path), "scheduled"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    assert fake_backend == []
    assert not Path(raw["operations_root"]).exists()


def test_activation_bound_to_exact_configuration_and_restore_hash(configuration, tmp_path):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    acceptance, acceptance_path = activate(raw, path, tmp_path)
    assert cli.load_config(path)[3].enabled
    raw["policy"]["max_attempts"] = 3
    save(path, raw)
    with pytest.raises(StorageError, match="SCHEDULE_PRODUCTION_ACCEPTANCE_REQUIRED"):
        cli.load_config(path)
    raw["policy"].pop("max_attempts")
    save(path, raw)
    acceptance["restore_report_sha256"] = "changed"
    save(acceptance_path, acceptance)
    with pytest.raises(StorageError, match="SCHEDULE_PRODUCTION_ACCEPTANCE_REQUIRED"):
        cli.load_config(path)


def test_activated_event_captures_once_then_uses_existing_job_plan(configuration, fake_backend, tmp_path, monkeypatch):
    raw, path = configuration
    enabled(raw, path, tmp_path)
    activate(raw, path, tmp_path, trigger="task_completed")
    captured, runs = [], []
    def capture_fake(config, backend, run_id):
        captured.append(run_id)
        return {"backup_set_id": run_id}, {"core": str(Path(raw["operations_root"]) / "captures" / run_id)}
    def run(plan, roots, **kwargs):
        runs.append((plan, roots))
        job = Path(raw["operations_root"]) / "jobs" / plan["backup_set_id"]
        job.mkdir(parents=True, exist_ok=True)
        save(job / "plan.json", plan)
        return {"status": "SYNTHETIC_ROUTE_PASS"}
    monkeypatch.setattr(cli, "_capture", capture_fake)
    monkeypatch.setattr(cli, "run_backup", run)
    invocation = args(path, "event", "--trigger", "task_completed", "--event-id", "task1", "--commit-id", "commit1")
    cli.execute(invocation); cli.execute(invocation)
    assert len(captured) == 1 and len(runs) == 2
    assert runs[0] == runs[1]
    assert all(instance.calls == [("connect", False), ("disconnect",)] for instance in fake_backend)
