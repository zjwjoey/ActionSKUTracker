import importlib.util
import json
from pathlib import Path

import pytest


def _load_publisher():
    path = Path(__file__).resolve().parents[1] / "scripts" / "publish_dictionary_baseline.py"
    spec = importlib.util.spec_from_file_location("publish_dictionary_baseline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_publish_gate_rejects_audit_fail(tmp_path):
    publisher = _load_publisher()
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"summary": {"pass": 10, "warn": 0, "fail": 1}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="DICTIONARY_AUDIT_FAILED"):
        publisher._require_audit_pass(report)


def test_publish_gate_accepts_zero_fail_even_with_warnings(tmp_path):
    publisher = _load_publisher()
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"summary": {"pass": 10, "warn": 2, "fail": 0}}), encoding="utf-8")
    payload = publisher._require_audit_pass(report)
    assert payload["summary"]["fail"] == 0


def test_baseline_manifest_requires_source_damage_report():
    from action_tracker.dictionary import DICTIONARY_BASELINE_FILENAMES
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "dictionary" / "baseline_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert "source_damage_report.csv" in DICTIONARY_BASELINE_FILENAMES
    assert len(DICTIONARY_BASELINE_FILENAMES) == 7
    assert set(manifest["files"]) == set(DICTIONARY_BASELINE_FILENAMES)


def test_audit_detects_missing_or_unexpected_baseline_file():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_dictionary.py"
    spec = importlib.util.spec_from_file_location("audit_dictionary", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    missing, unexpected = module.baseline_file_set_mismatches(["product_dictionary.csv"])
    assert "source_damage_report.csv" in missing
    assert unexpected == []
