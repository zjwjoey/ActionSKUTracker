import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "promote_stage4_177_owner_confirmed.py"
    spec = importlib.util.spec_from_file_location("stage4_promote_177", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_promotion_script_isolated_and_explicitly_owner_confirmed(tmp_path):
    module = _module()
    source = tmp_path / "in.jsonl"
    rows = [{"messages": [], "metadata": {"sku": str(i), "source_hash": "h", "ai_disposition": "ACCEPT_AS_GOLD"}} for i in range(177)]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    output = tmp_path / "out.jsonl"; manifest = tmp_path / "manifest.json"
    import sys
    old = sys.argv
    try:
        sys.argv = ["promote", "--input", str(source), "--output", str(output), "--manifest", str(manifest)]
        module.main()
    finally:
        sys.argv = old
    out = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(out) == 177
    assert all(row["metadata"]["gold_status"] == "HUMAN_CONFIRMED_REMEDIATION_GOLD" for row in out)
    assert all(row["metadata"]["training_eligible"] for row in out)
