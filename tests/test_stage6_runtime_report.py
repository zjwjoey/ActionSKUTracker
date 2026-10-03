import importlib.util
import subprocess
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


def test_runtime_git_provenance_reports_actual_repository_values():
    info = MODULE.read_git_provenance(ROOT)
    assert info["available"] is True
    assert info["commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert info["origin_main"] == subprocess.run(
        ["git", "rev-parse", "origin/main^{commit}"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert info["merge_base"] == subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def test_stage6_script_has_no_stale_hardcoded_git_metadata():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "788e290ece1ec4bb4a2ba110b0ab2ffe1f451864" not in source
    assert "fix/legacy-artifact-stage6-provenance" not in source
    assert "Origin main:" in source
    assert "Merge base:" in source


def test_git_failure_fails_closed_without_ready_status(monkeypatch):
    def fail(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="git unavailable")

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
