import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from action_tracker.dictionary_apply import DictionaryApplyError, _gate_errors, _preview_rows, dictionary_apply
from action_tracker.dictionary_resolver import FieldResolution, RecordResolution

from test_exporting import _cfg, _record, _run_log, _write_dictionary, _write_master, _write_snapshot


def test_dictionary_apply_dry_run_writes_preview_without_changing_master(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "2026-08-26_130145"
    record = _record("1001", last_seen="2026-08-26")
    _write_master(cfg["paths"]["master"], [_run_log(run_id, "2026-08-26")], [record])
    _write_snapshot(cfg["paths"]["snapshots"], run_id, "2026-08-26", [record])
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    before = hashlib.sha256(cfg["paths"]["master"].read_bytes()).hexdigest()

    result = dictionary_apply(cfg, run_id=run_id, dry_run=True)
    output_dir = Path(result["output_dir"])
    assert result["dry_run"] is True
    assert (output_dir / "apply_preview.csv").exists()
    assert (output_dir / "review_required.csv").exists()
    manifest = json.loads((output_dir / "apply_manifest.json").read_text(encoding="utf-8"))
    assert manifest["master_hash_before"] == before
    assert manifest["formal_write"] is False
    assert hashlib.sha256(cfg["paths"]["master"].read_bytes()).hexdigest() == before


def test_dictionary_apply_formal_write_is_blocked(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(DictionaryApplyError, match="PRODUCTION_DICTIONARY_APPLY_DISABLED"):
        dictionary_apply(cfg, run_id="2026-08-26_130145", dry_run=False)


def test_apply_manifest_has_gate_and_diff_contract(tmp_path):
    cfg = _cfg(tmp_path)
    run_id = "2026-08-26_130145"
    record = _record("1001", last_seen="2026-08-26")
    _write_master(cfg["paths"]["master"], [_run_log(run_id, "2026-08-26")], [record])
    _write_snapshot(cfg["paths"]["snapshots"], run_id, "2026-08-26", [record])
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    result = dictionary_apply(cfg, run_id=run_id, dry_run=True)
    manifest = json.loads((Path(result["output_dir"]) / "apply_manifest.json").read_text(encoding="utf-8"))
    required = {"run_id", "run_date", "master_hash_before", "temporary_master_hash", "master_hash_after_if_committed",
                "dictionary_hash", "dictionary_baseline_hash", "total_current_skus", "auto_ready_count",
                "review_required_count", "source_blocked_count", "preview_field_count", "actual_changed_field_count",
                "unchanged_field_count", "immutable_fact_change_count", "production_enabled", "committed", "generated_at"}
    assert required.issubset(manifest)
    rows = list(csv.DictReader((Path(result["output_dir"]) / "field_diff.csv").open(encoding="utf-8-sig")))
    assert {row["field"] for row in rows} <= {"name_zh", "cat1_zh", "cat2_zh", "spec_zh", "desc_zh", "details_zh"}


def test_diff_does_not_count_equal_value_as_actual_change():
    record = {"sku": "1", "name_zh": "同名", "cat1_zh": "家务清洁", "cat2_zh": "", "spec_zh": "", "desc_zh": "", "details_zh": ""}
    fields = {
        key: FieldResolution(value, "manual_override", "READY")
        for key, value in (("name", "同名"), ("cat1", "家务清洁"), ("cat2", ""), ("spec", ""), ("description", ""), ("details", ""))
    }
    resolution = RecordResolution("1", fields, "MATCH", "OK", "AUTO_READY", ())
    rows, summary = _preview_rows([record], [resolution])
    assert rows == []
    assert summary["actual_changed_field_count"] == 0
    assert summary["unchanged_field_count"] == 6


def test_apply_gate_rejects_non_formal_or_failed_qa_before_write(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["dictionary_apply"] = {"production_enabled": True}
    run_id = "2026-08-26_130145"
    record = _record("1001", last_seen="2026-08-26")
    _write_master(cfg["paths"]["master"], [_run_log(run_id, "2026-08-26")], [record])
    snapshot = cfg["paths"]["snapshots"] / "2026-08-26" / run_id
    snapshot.mkdir(parents=True)
    (snapshot / "run_report.json").write_text(json.dumps({"run_id": run_id, "run_date": "2026-08-26", "dry_run": False, "commit_status": "FULL_COMMIT"}), encoding="utf-8")
    (snapshot / "qa_report.json").write_text(json.dumps({"state": "FAIL", "passed": False}), encoding="utf-8")
    _write_dictionary(cfg["paths"]["dictionary_baseline"], record)
    before = hashlib.sha256(cfg["paths"]["master"].read_bytes()).hexdigest()
    source = SimpleNamespace(directory=snapshot, records=())
    errors = _gate_errors(cfg, source, [], object(), {}, before)
    assert "QA_NOT_PASS" in errors
    assert hashlib.sha256(cfg["paths"]["master"].read_bytes()).hexdigest() == before
