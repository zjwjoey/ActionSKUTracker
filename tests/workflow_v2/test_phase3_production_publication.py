"""Regression coverage for the final Phase 3 Chinese publication boundary."""
from __future__ import annotations

from pathlib import Path

import pytest

from action_tracker.exporting.service import ExportValidationError
from action_tracker.workflow_v2.context import new_context
from action_tracker.workflow_v2.runner import WorkflowV2Runner


def _runner(tmp_path: Path, *, production: bool, auto_export: bool = True) -> WorkflowV2Runner:
    runner = WorkflowV2Runner(
        root=tmp_path / "reports",
        context=new_context(tmp_path / "reports", business_date="2026-10-06", run_id="phase3-release"),
        production_apply=production,
        auto_export=auto_export,
        cfg={"paths": {"exports": tmp_path / "formal-exports"}},
    )
    runner.context.export_ready = True
    runner.context.source_commit_id = "applied-commit"
    runner.es_projection = [{"编号": "100"}]
    runner.zh_projection = [{"编号": "100"}]
    runner.final_zh_rows_hash = "rows-hash"
    return runner


def _formal_result(root: Path, language: str, *, release_mode: str = "preview",
                   audited_hash: str = "rows-hash", published_hash: str = "rows-hash") -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True)
    output = root / f"{language}.xlsx"
    manifest = root / f"{language}.manifest.json"
    output.write_bytes(f"new-{language}".encode())
    manifest.write_text("{}", encoding="utf-8")
    result: dict[str, str] = {"output": str(output), "manifest": str(manifest)}
    if language == "zh":
        repair = root / "zh.repair-report.json"
        repair.write_text("{}", encoding="utf-8")
        result.update({
            "release_mode": release_mode,
            "audited_zh_rows_hash": audited_hash,
            "published_zh_rows_hash": published_hash,
            "repair_report": str(repair),
        })
    return result


def _fake_exporter(calls: list[tuple[str, str]]):
    def export_catalog(cfg, *, language, release_mode, **_kwargs):
        calls.append((language, release_mode))
        return _formal_result(Path(cfg["paths"]["exports"]), language, release_mode=release_mode.casefold())
    return export_catalog


def test_formal_production_zh_uses_production_release(monkeypatch, tmp_path):
    runner = _runner(tmp_path, production=True)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("action_tracker.exporting.service.export_catalog", _fake_exporter(calls))
    monkeypatch.setattr(
        "action_tracker.database.integration.regenerate_compatibility_exports",
        lambda *_args, **_kwargs: {"status": "PASS"},
    )

    result = runner._export_write()

    assert result.status == "PASS"
    assert calls == [("es", "PREVIEW"), ("zh", "PRODUCTION_RELEASE")]
    assert result.details["formal_exports"]["zh"]["release_mode"] == "production_release"


def test_formal_production_rejects_preview_zh_before_formal_root(tmp_path):
    runner = _runner(tmp_path, production=True)
    export_root = Path(runner.cfg["paths"]["exports"])
    export_root.mkdir(parents=True)
    sentinel = export_root / "existing.xlsx"
    sentinel.write_bytes(b"old")
    bundle = {
        "es": _formal_result(tmp_path / "source", "es"),
        "zh": _formal_result(tmp_path / "source", "zh", release_mode="preview"),
    }

    with pytest.raises(ValueError, match="PRODUCTION_ZH_RELEASE_MODE_REQUIRED"):
        runner._publish_formal_exports(bundle)

    assert sentinel.read_bytes() == b"old"
    assert sorted(path.name for path in export_root.iterdir()) == ["existing.xlsx"]


def test_nonproduction_canary_keeps_preview_mode(monkeypatch, tmp_path):
    runner = _runner(tmp_path, production=False)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("action_tracker.exporting.service.export_catalog", _fake_exporter(calls))

    result = runner._export_write()

    assert result.status == "PASS"
    assert calls == [("es", "PREVIEW"), ("zh", "PREVIEW")]


def test_production_release_gate_failure_never_publishes_formal_bundle(monkeypatch, tmp_path):
    runner = _runner(tmp_path, production=True)
    export_root = Path(runner.cfg["paths"]["exports"])
    export_root.mkdir(parents=True)
    sentinel = export_root / "existing.xlsx"
    sentinel.write_bytes(b"old")

    def failing_exporter(cfg, *, language, release_mode, **_kwargs):
        if language == "zh":
            assert release_mode == "PRODUCTION_RELEASE"
            raise ExportValidationError("RESEARCH_RELEASE_GATE_FAILED:P0")
        return _formal_result(Path(cfg["paths"]["exports"]), language, release_mode=release_mode.casefold())

    monkeypatch.setattr("action_tracker.exporting.service.export_catalog", failing_exporter)

    with pytest.raises(ExportValidationError, match="RESEARCH_RELEASE_GATE_FAILED"):
        runner._export_write()

    assert sentinel.read_bytes() == b"old"
    assert sorted(path.name for path in export_root.iterdir()) == ["existing.xlsx"]


def test_hash_mismatch_blocks_formal_publication_before_root_write(tmp_path):
    runner = _runner(tmp_path, production=True)
    export_root = Path(runner.cfg["paths"]["exports"])
    export_root.mkdir(parents=True)
    sentinel = export_root / "existing.xlsx"
    sentinel.write_bytes(b"old")
    bundle = {
        "zh": _formal_result(
            tmp_path / "source", "zh", release_mode="production_release",
            audited_hash="audited", published_hash="published",
        ),
    }

    with pytest.raises(ValueError, match="PUBLISHED_ROWS_DIFFER_FROM_AUDITED_ROWS"):
        runner._publish_formal_exports(bundle)

    assert sentinel.read_bytes() == b"old"
    assert sorted(path.name for path in export_root.iterdir()) == ["existing.xlsx"]


@pytest.mark.parametrize("failed_name", ["zh.xlsx", "zh.manifest.json", "zh.repair-report.json"])
def test_chinese_formal_bundle_replacement_failure_rolls_back_all_three(monkeypatch, tmp_path, failed_name):
    runner = _runner(tmp_path, production=True)
    export_root = Path(runner.cfg["paths"]["exports"])
    export_root.mkdir(parents=True)
    previous = {
        "zh.xlsx": b"old workbook",
        "zh.manifest.json": b"old manifest",
        "zh.repair-report.json": b"old repair report",
    }
    for name, content in previous.items():
        (export_root / name).write_bytes(content)
    bundle = {
        "zh": _formal_result(tmp_path / "source", "zh", release_mode="production_release"),
    }
    from action_tracker.workflow_v2 import runner as runner_module
    real_replace = runner_module.os.replace
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if Path(target).name == failed_name and not failed:
            failed = True
            raise OSError(f"failed replacement: {failed_name}")
        return real_replace(source, target)

    monkeypatch.setattr(runner_module.os, "replace", fail_once)
    with pytest.raises(OSError, match="failed replacement"):
        runner._publish_formal_exports(bundle)

    assert {name: (export_root / name).read_bytes() for name in previous} == previous
