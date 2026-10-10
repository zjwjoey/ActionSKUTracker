from dataclasses import replace

import pytest

from action_tracker.localization.pipeline import translate_pending_requests
from action_tracker.localization.hashes import value_hash
from action_tracker.localization.providers.base import FakeTranslationProvider, ProviderError, TranslationRequest


class InterruptedProvider(FakeTranslationProvider):
    def __init__(self):
        super().__init__(mapping={"name": "待审核品名"})
        self.calls = []
        self.fail_sku = "3200665"

    def translate(self, request):
        self.calls.append(request.sku)
        if request.sku == self.fail_sku:
            raise ProviderError("TIMEOUT", retryable=True)
        return super().translate(request)


def requests():
    # Real historical source titles; no guessed correct Chinese is asserted.
    return tuple(TranslationRequest(sku, {"name_es": source}, ("name",), value_hash(source),
                                    request_id=f"historical-test-{sku}") for sku, source in (
        ("3200638", "Manguera flexible"), ("3200665", "Lendrera eléctrica Silvergear")))


def test_failed_batch_resumes_only_the_failed_field_and_never_approves(tmp_path):
    provider = InterruptedProvider()
    path = tmp_path / "checkpoint.json"
    with pytest.raises(ProviderError):
        translate_pending_requests(requests(), provider, path)
    provider.fail_sku = None
    result = translate_pending_requests(requests(), provider, path)
    assert provider.calls == ["3200638", "3200665", "3200665"]
    assert result["provider_calls"] == 1 and result["reused_responses"] == 1
    assert all(row["decision"] == "PENDING_SEMANTIC_REVIEW" and row["semantic_status"] == "PENDING"
               for row in result["responses"])
    again = translate_pending_requests(requests(), provider, path)
    assert again["provider_calls"] == 0 and again["reused_responses"] == 2
    assert again["production_writes"] is False
    assert provider.calls == ["3200638", "3200665", "3200665"]


def test_changed_source_or_model_cannot_reuse_an_old_batch(tmp_path):
    provider = InterruptedProvider(); provider.fail_sku = None
    path = tmp_path / "checkpoint.json"
    original = requests()
    translate_pending_requests(original, provider, path)
    changed = (replace(original[0], fields={"name_es": "Manguera flexible nueva"}), original[1])
    with pytest.raises(ValueError, match="PLAN_CHANGED"):
        translate_pending_requests(changed, provider, path)
    provider.model = "different-model"
    with pytest.raises(ValueError, match="PLAN_CHANGED"):
        translate_pending_requests(original, provider, path)
    assert len(provider.calls) == 2


def test_missing_source_and_duplicate_fields_never_call_provider(tmp_path):
    provider = InterruptedProvider()
    empty = replace(requests()[0], fields={"name_es": ""})
    with pytest.raises(ValueError, match="SINGLE_TRUSTED_SOURCE_REQUIRED"):
        translate_pending_requests((empty,), provider, tmp_path / "empty.json")
    with pytest.raises(ValueError, match="DUPLICATE_FIELD"):
        translate_pending_requests((requests()[0], requests()[0]), provider, tmp_path / "duplicate.json")
    assert provider.calls == []
