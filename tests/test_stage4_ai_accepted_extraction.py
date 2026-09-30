import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "extract_stage4_ai_accepted.py"
    spec = importlib.util.spec_from_file_location("stage4_ai_accepted", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_extract_accepts_only_complete_ai_rows():
    module = _module()
    row = {"SKU": "1", module.AI_DISPOSITION: "ACCEPT_AS_GOLD", "AI审核理由": "ok"}
    row.update({column: "x" for column in module.SOURCE_COLUMNS.values()})
    row.update({column: "中文" for column in module.TARGET_COLUMNS.values()})
    result = module.extract([row])
    assert len(result) == 1 and result[0]["sku"] == "1"


def test_extract_skips_non_accepted_rows():
    module = _module()
    row = {"SKU": "1", module.AI_DISPOSITION: "REVISE", "AI审核理由": "fix"}
    row.update({column: "x" for column in module.SOURCE_COLUMNS.values()})
    row.update({column: "中文" for column in module.TARGET_COLUMNS.values()})
    assert module.extract([row]) == []
