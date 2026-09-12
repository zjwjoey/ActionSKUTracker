from __future__ import annotations

import importlib.util
from pathlib import Path


def load_controller():
    path = Path(__file__).resolve().parents[1] / "scripts" / "stage4_recovery_controller.py"
    spec = importlib.util.spec_from_file_location("stage4_recovery_controller", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rule_first_pipeline_gate_requires_accounting_and_no_production_write():
    controller = load_controller()
    assert controller.pipeline_shadow_ok({
        "rows": 2,
        "accepted_by_guard": 2,
        "review_required": 0,
        "json_parse_failures": 0,
        "production_writes": False,
        "model_gap_fields": 4,
        "model_inference_fields": 4,
    })
    assert not controller.pipeline_shadow_ok({
        "rows": 2,
        "accepted_by_guard": 2,
        "review_required": 0,
        "json_parse_failures": 0,
        "production_writes": True,
        "model_gap_fields": 4,
        "model_inference_fields": 4,
    })
    assert not controller.pipeline_shadow_ok({
        "rows": 2,
        "accepted_by_guard": 2,
        "review_required": 0,
        "json_parse_failures": 0,
        "production_writes": False,
        "model_gap_fields": 4,
        "model_inference_fields": 3,
    })
