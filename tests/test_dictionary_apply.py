import hashlib
import json
from pathlib import Path

import pytest

from action_tracker.dictionary_apply import DictionaryApplyError, dictionary_apply

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
    with pytest.raises(DictionaryApplyError, match="FORMAL_DICTIONARY_APPLY_NOT_ENABLED"):
        dictionary_apply(cfg, run_id="2026-08-26_130145", dry_run=False)
