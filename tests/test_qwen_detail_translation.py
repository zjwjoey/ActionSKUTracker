from action_tracker.translation.qwen_mt import QwenMTFlashProvider
from action_tracker.translation.service import translate_records_with_qwen


def test_qwen_mt_flash_uses_translation_payload_and_reads_text():
    seen = {}

    def transport(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {"status_code": 200, "body": {"choices": [{"message": {"content": "中文商品"}}]}}

    provider = QwenMTFlashProvider(
        endpoint="https://example.test/compatible-mode/v1",
        api_key="secret",
        transport=transport,
        rate_limit_per_second=0,
        max_retries=0,
    )
    result = provider.translate("Producto", field="name_es", sku="123")
    assert result.ok
    assert result.text == "中文商品"
    assert seen["payload"]["model"] == "qwen-mt-flash"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "Producto"}]
    assert seen["payload"]["translation_options"] == {
        "source_lang": "Spanish", "target_lang": "Chinese"
    }
    assert seen["headers"]["Authorization"] == "Bearer secret"


def test_qwen_mt_flash_retries_transient_failure():
    calls = []

    def transport(*_):
        calls.append(1)
        if len(calls) == 1:
            return {"status_code": 429, "body": {"error": "busy"}}
        return {"status_code": 200, "body": {"choices": [{"message": {"content": "中文"}}]}}

    provider = QwenMTFlashProvider(
        endpoint="https://example.test/v1", api_key="secret", transport=transport,
        rate_limit_per_second=0, backoff_seconds=0, max_retries=1,
    )
    result = provider.translate("Bolsa")
    assert result.ok and result.attempts == 2


class _FakeProvider:
    def __init__(self):
        self.calls = []

    def translate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return type("Result", (), {"text": f"中译:{text}", "error": None})()


def test_detail_translation_only_handles_completed_skus_and_leaves_other_fields():
    provider = _FakeProvider()
    records = {
        "1": {
            "sku": "1", "canonical_id": "c1", "name_es": "Bolsa",
            "details_es": "Color: rojo", "name_zh": "", "details_zh": "",
            "spec_es": "20 g", "spec_zh": "已有中文", "translation_status": "OK",
        },
        "2": {
            "sku": "2", "canonical_id": "c2", "name_es": "Otro",
            "name_zh": "", "translation_status": "FALLBACK_ES",
        },
    }
    result, updates, report = translate_records_with_qwen(records, provider, eligible_skus={"1"})
    assert result["1"]["name_zh"] == "中译:Bolsa"
    assert result["1"]["details_zh"] == "中译:Color: rojo"
    assert result["1"]["spec_zh"] == "已有中文"
    assert result["2"]["name_zh"] == ""
    assert len(provider.calls) == 2
    assert report["translated_skus"] == 1
    assert report["field_successes"] == 2
    assert updates[0]["translation_status"] == "QWEN_TRANSLATED"
