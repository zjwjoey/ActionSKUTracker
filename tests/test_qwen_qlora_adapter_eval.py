from action_tracker.localization.contracts import SourceFacts
from scripts.evaluate_qwen3_adapter import _messages, _parse_completion


def test_adapter_evaluation_uses_same_json_contract_as_provider():
    source = SourceFacts.from_record({"sku": "TEST", "name_es": "Lámpara LED"})
    messages = _messages(source, ("name",))
    assert messages[-1]["role"] == "user"
    assert _parse_completion('{"sku":"TEST","fields":{"name":"LED灯"}}')["sku"] == "TEST"
    assert _parse_completion("说明 {\"sku\":\"TEST\"}") is None
