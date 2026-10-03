import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_legacy_stage6_preview.py"
SPEC = importlib.util.spec_from_file_location("legacy_stage6_preview", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _status(*, excluded=0, actions=None, git=True, unchanged=True):
    return MODULE.determine_final_status(
        git_available=git, protected_inputs_unchanged=unchanged,
        candidate_total=2, eligible_count=2 - excluded,
        previewed_count=2 - excluded, action_counts=Counter(actions or {}),
        excluded_count=excluded,
    )


def test_runtime_git_provenance_reports_command_values(monkeypatch):
    outputs = {
        ("git", "rev-parse", "origin/main^{commit}"): "main-sha\n",
        ("git", "merge-base", "HEAD", "origin/main"): "base-sha\n",
        ("git", "branch", "--show-current"): "fix/stage6-provenance-v2\n",
        ("git", "rev-parse", "HEAD"): "head-sha\n",
    }

    def fake_run(command, **kwargs):
        return MODULE.subprocess.CompletedProcess(command, 0, outputs[tuple(command)], "")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    info = MODULE.read_git_provenance(ROOT)
    assert info["available"] is True
    assert info["origin_main"] == "main-sha"
    assert info["merge_base"] == "base-sha"
    assert info["branch"] == "fix/stage6-provenance-v2"
    assert info["commit"] == "head-sha"


def test_stage6_script_has_no_stale_hardcoded_git_metadata():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Base main:" not in source
    assert "current_commit" not in source
    assert "Origin main:" in source
    assert "Merge base:" in source


def test_git_failure_fails_closed_without_ready_status(monkeypatch):
    def fail(*args, **kwargs):
        return MODULE.subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="git unavailable")

    monkeypatch.setattr(MODULE.subprocess, "run", fail)
    info = MODULE.read_git_provenance(ROOT)
    assert info["available"] is False
    assert _status(git=False) == "LEGACY_STAGE6_ADAPTER_BLOCKED"


def test_mixed_blocked_batch_is_ready_with_explicit_exclusions():
    assert _status(excluded=1) == "LEGACY_STAGE6_PREVIEW_READY_WITH_EXCLUSIONS"
    assert _status(excluded=0) == "LEGACY_STAGE6_PREVIEW_READY"
    assert _status(excluded=1, actions={"BLOCKED_CONFLICT": 1}) == "LEGACY_STAGE6_ADAPTER_BLOCKED"
    assert _status(excluded=1, actions={"NO_SOURCE": 1}) == "LEGACY_STAGE6_ADAPTER_BLOCKED"
    assert _status(excluded=2) == "LEGACY_STAGE6_ADAPTER_BLOCKED"
