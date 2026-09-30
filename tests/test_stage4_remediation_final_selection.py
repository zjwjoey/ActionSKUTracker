import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "select_stage4_remediation_final_200.py"
    spec = importlib.util.spec_from_file_location("stage4_final_selection", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_selection_refills_only_the_missing_class_quota():
    module = _module()
    base = []
    refill = []
    for kind in module.CLASSES:
        base.extend({"sku": f"base-{kind}-{index}", "failure_class": kind, "final_status": "APPROVED_MODEL_REVIEWED"} for index in range(39))
        refill.extend({"sku": f"refill-{kind}-{index}", "failure_class": kind, "final_status": "APPROVED_MODEL_REVIEWED"} for index in range(3))
    selected, additions = module.choose_to_target(base, refill)
    assert len(selected) == 200
    assert additions == {kind: 1 for kind in module.CLASSES}
