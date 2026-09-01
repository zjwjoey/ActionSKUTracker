import json

import pytest

from scripts.train_qwen3_qlora import _load_and_validate_dataset_manifest, _load_rows, _validate_rows


def test_qlora_training_rows_require_structured_assistant_contract(tmp_path):
    path = tmp_path / "train.jsonl"
    valid = {
        "messages": [
            {"role": "system", "content": "json only"},
            {"role": "user", "content": json.dumps({
                "naming_policy_version": "NAMING_AND_SPEC_PLANNING_STANDARD_V1.0",
                "field_policy": ["name rule"], "global_guardrails": ["guardrail"],
            })},
            {"role": "assistant", "content": "{}"},
        ],
        "metadata": {"sku": "A", "field": "name"},
    }
    path.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    rows = _load_rows(path)
    _validate_rows(rows, rows)
    invalid = dict(valid)
    invalid["messages"] = valid["messages"][:-1]
    with pytest.raises(ValueError, match="MISSING_ASSISTANT_MESSAGE"):
        _validate_rows([invalid], rows)


def test_qlora_training_rejects_dataset_without_full_naming_policy(tmp_path):
    row = {
        "messages": [
            {"role": "system", "content": "json only"},
            {"role": "user", "content": "{}"},
            {"role": "assistant", "content": "{}"},
        ],
        "metadata": {"sku": "A", "field": "name"},
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="POLICY_TOO_OLD"):
        _load_and_validate_dataset_manifest(manifest, [row], [row])
