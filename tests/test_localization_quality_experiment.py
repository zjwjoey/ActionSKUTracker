"""CI_SAFE: temporary checkpoint tests; no real provider or production paths."""
import importlib.util
from pathlib import Path

import pytest

from action_tracker.localization.providers.base import TranslationRequest, TranslationResponse
from action_tracker.services.hashing import localization_field_source_hash

spec = importlib.util.spec_from_file_location("quality_experiment", Path(__file__).parents[1] / "scripts/run_localization_quality_experiment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CountingProvider:
    provider, model = "fixture", "fixture-v1"

    def __init__(self):
        self.calls = 0

    def finite_batch_identity(self):
        return {"contract": "TEST_ONLY"}

    def translate(self, request):
        self.calls += 1
        return TranslationResponse({request.field_name: "2件"}, self.provider, self.model,
                                   request.source_hash, "rq", "rs", "id",
                                   {"input_tokens": 10, "output_tokens": 2})


def request(text="2 unidades"):
    return TranslationRequest("1234", {"spec": text}, ("spec",), "hash")


def test_native_finite_resume_preserves_usage_and_makes_no_new_call(tmp_path):
    inner = CountingProvider()
    evidence = module.EvidenceProvider(inner, tmp_path)
    wrapper = module.FiniteResolverProvider(evidence)
    first = wrapper.translate(request())
    second = wrapper.translate(request())
    assert inner.calls == evidence.actual_calls == 1
    assert second.usage == first.usage == {"input_tokens": 10, "output_tokens": 2}


def test_response_sidecar_survives_window_before_native_checkpoint(tmp_path):
    inner = CountingProvider()
    evidence = module.EvidenceProvider(inner, tmp_path)
    evidence.translate(request())
    resumed = module.EvidenceProvider(inner, tmp_path)
    module.FiniteResolverProvider(resumed).translate(request())
    assert inner.calls == 1
    assert resumed.actual_calls == 0
    assert resumed.reused == 1


def test_changed_request_cannot_reuse_sidecar_or_call_provider(tmp_path):
    inner = CountingProvider()
    evidence = module.EvidenceProvider(inner, tmp_path)
    evidence.translate(request())
    with pytest.raises(ValueError, match="REQUEST_CHANGED"):
        evidence.translate(request("3 unidades"))
    assert inner.calls == 1


@pytest.mark.parametrize("status", ["SOURCE_UNAVAILABLE", "SOURCE_VERSION_CONFLICT", "SOURCE_LANGUAGE_REVIEW_REQUIRED"])
def test_uncertain_sources_are_refused_not_quality_success(status):
    record = {"sku": "1234", "spec_es": "2 unidades"}
    row = {"record": record, "field_evidence": {"spec": {
        "status": status, "source": record["spec_es"],
        "field_source_hash": localization_field_source_hash(record, "spec")}}}
    assert module.validate_source(row, "spec") is False


def test_trusted_flag_without_exact_proof_is_rejected():
    record = {"sku": "1234", "spec_es": "2 unidades"}
    row = {"record": record, "field_evidence": {"spec": {
        "status": "TRUSTED", "source": record["spec_es"],
        "field_source_hash": localization_field_source_hash(record, "spec"),
        "proof": {"reference": "fixture", "text": "3 unidades"}}}}
    with pytest.raises(ValueError, match="TRUSTED_PROOF_REQUIRED"):
        module.validate_source(row, "spec")
