"""CI_SAFE: temporary run state and mocked detail parsing; no network/PRIMARY."""
import json
import os
from types import SimpleNamespace

import pytest

from action_tracker.operations.contracts import StepResult
from action_tracker.operations.runner import ProductionRunner
from action_tracker.operations import runner as runner_module
from action_tracker.orchestrator.daily import _merge_light, _verified_fields_after_light_merge
from action_tracker.products import parser, updater
from test_operations import _steps


@pytest.mark.parametrize("failed_step", ["COLLECTION", "EXPORT"])
def test_resume_persists_new_owner_before_retry_and_preserves_run_identity(tmp_path, monkeypatch, failed_step):
    runner = ProductionRunner(root=tmp_path / "reports", business_date="2026-10-13",
        run_id="same-run", steps=_steps(**{failed_step: lambda: StepResult("FAILED")}),
        lock_dir=tmp_path / "locks")
    first = runner.run()
    original_start = first["started_at"]
    monkeypatch.setattr(runner_module.os, "getpid", lambda: 987654)
    monkeypatch.setattr(runner_module.socket, "gethostname", lambda: "resumed-host")
    observed = []

    def retry():
        persisted = json.loads(runner.state_path.read_text(encoding="utf-8"))
        lock = json.loads(runner.lock.path.read_text(encoding="utf-8"))
        observed.append((persisted["pid"], persisted["hostname"]))
        assert persisted["pid"] == lock["pid"] == 987654
        assert persisted["hostname"] == lock["hostname"] == "resumed-host"
        assert persisted["run_id"] == "same-run"
        assert persisted["started_at"] == original_start
        return StepResult("SUCCESS")

    runner.steps[failed_step] = retry
    second = runner.run(resume=True)
    assert second["state"] == "SUCCESS"
    assert observed == [(987654, "resumed-host")]
    assert not runner.lock.path.exists()


def test_successful_resume_is_immutable_noop(tmp_path, monkeypatch):
    runner = ProductionRunner(root=tmp_path / "reports", business_date="2026-10-13",
        run_id="done", steps=_steps(), lock_dir=tmp_path / "locks")
    original = runner.run()
    before = runner.state_path.read_bytes()
    monkeypatch.setattr(runner_module.os, "getpid", lambda: original["pid"] + 1)
    monkeypatch.setattr(runner.lock, "acquire", lambda *_args, **_kwargs: pytest.fail("NO_OP must not acquire a lock"))
    assert runner.run(resume=True) == original
    assert runner.state_path.read_bytes() == before


def test_failed_lock_acquisition_does_not_change_resume_owner(tmp_path):
    runner = ProductionRunner(root=tmp_path / "reports", business_date="2026-10-13",
        run_id="blocked", steps=_steps(COLLECTION=lambda: StepResult("FAILED")),
        lock_dir=tmp_path / "locks")
    runner.run()
    saved = json.loads(runner.state_path.read_text(encoding="utf-8"))
    saved.update(pid=os.getpid() + 12345, hostname="previous-owner")
    runner.state_path.write_text(json.dumps(saved), encoding="utf-8")
    before = runner.state_path.read_bytes()
    runner.lock.acquire("another-active-run")
    try:
        with pytest.raises(RuntimeError, match="RUN_ALREADY_ACTIVE"):
            runner.run(resume=True)
        assert runner.state_path.read_bytes() == before
    finally:
        runner.lock.release()


def _fetch_and_finalize(tmp_path, monkeypatch, *, raw, light=None):
    sku = "1001"
    url = "https://www.action.com/es-es/p/1001/fixture/"
    base = dict(sku=sku, name_es="Producto", current_price=2.5,
                original_price=3.0, discount=-0.17, product_url=url)
    detail = parser._normalize_detail(dict(sku=sku, name_es="Producto", **raw), url)
    monkeypatch.setattr(parser, "fetch_product_detail", lambda *_args, **_kwargs: dict(detail))
    plan = dict(sku=sku, canonical_id="ACT0001001", reason="DETAIL_REFRESH",
                need_detail=True, light=light, detail_url=url)
    _, updated = updater.fetch_and_merge(SimpleNamespace(sleep=lambda: None),
        [plan], {sku: base}, tmp_path)
    record = updated[sku]
    if light:
        _merge_light(record, light, skip_raw_tags=True)
    verified = _verified_fields_after_light_merge(record, light)
    return updater.retain_historical_facts(base, record, reappeared=False,
                                          verified_fields=verified)


@pytest.mark.parametrize("proof", [False, None, "true"])
def test_incomplete_detail_absence_keeps_historical_promotion(tmp_path, monkeypatch, proof):
    result = _fetch_and_finalize(tmp_path, monkeypatch, raw=dict(current_price="2,50 €",
        original_price="", discount="", price_observation_complete=proof))
    assert result["current_price"] == 2.5
    assert result["original_price"] == 3.0
    assert result["discount"] == -0.17
    assert result["fact_field_provenance"]["original_price"] == {
        "state": "HISTORY_RETAINED", "source": "BASELINE"}


def test_incomplete_detail_payload_does_not_prove_optional_price_absence(tmp_path, monkeypatch):
    result = _fetch_and_finalize(tmp_path, monkeypatch, raw=dict(current_price="2,50 €",
        original_price="", price_observation_complete=True))  # discount key missing
    assert result["original_price"] == 3.0
    assert result["discount"] == -0.17


def test_complete_detail_absence_clears_original_and_discount(tmp_path, monkeypatch):
    result = _fetch_and_finalize(tmp_path, monkeypatch, raw=dict(current_price="2,50 €",
        original_price="", discount="", price_observation_complete=True))
    assert result["original_price"] is None
    assert result["discount"] is None
    assert result["fact_field_provenance"]["original_price"] == {
        "state": "CURRENT_VERIFIED", "source": "DETAIL_CURRENT_RUN"}


@pytest.mark.parametrize("original", ["4,00 €", ""])
def test_newer_complete_detail_prices_keep_their_own_provenance(tmp_path, monkeypatch, original):
    light = dict(current_price=2.5, original_price=None, discount=None,
                 _price_observation_complete=True)
    result = _fetch_and_finalize(tmp_path, monkeypatch, light=light,
        raw=dict(current_price="3,00 €", original_price=original,
                 discount="-25 %" if original else "", price_observation_complete=True))
    assert result["current_price"] == 3.0
    assert result["original_price"] == (4.0 if original else None)
    for field in ("current_price", "original_price"):
        assert result["fact_field_provenance"][field] == {
            "state": "CURRENT_VERIFIED", "source": "DETAIL_CURRENT_RUN"}


def test_complete_listing_over_incomplete_detail_owns_prices(tmp_path, monkeypatch):
    light = dict(current_price=2.75, original_price=None, discount=None,
                 _price_observation_complete=True)
    result = _fetch_and_finalize(tmp_path, monkeypatch, light=light,
        raw=dict(current_price="3,00 €", original_price="", discount="",
                 price_observation_complete=False))
    assert result["current_price"] == 2.75
    assert result["original_price"] is None
    for field in ("current_price", "original_price"):
        assert result["fact_field_provenance"][field] == {
            "state": "CURRENT_VERIFIED", "source": "LISTING_CURRENT_RUN"}
